"""Persist and refresh alert-only CE/PE signals. Never places a broker or paper order."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import as_utc_naive, now_ist, now_utc
from app.db.models import AlertLog, AlertSetting, AlertSignal, AppSetting
from app.domain.alert_engine import (
    ACTIVE,
    BUY,
    CANCELLED,
    EXPIRED,
    NEW,
    OPEN_STATUSES,
    WAIT,
    AlertConfig,
    AlertDecision,
    MarketFacts,
    OptionFacts,
    decide,
    next_status,
    public_status,
)
from app.domain.alert_indicators import ema_pair, supertrend_side, swing_support_resistance
from app.domain.dashboard_brief import implied_market_direction
from app.domain.market_facts import market_facts_from_bars
from app.services.mapping import jsonable

log = logging.getLogger("hextrade.alerts")


def load_config(db: Session) -> AlertConfig:
    row = db.get(AlertSetting, 1)
    if row is None:
        return AlertConfig()
    return AlertConfig(
        min_confidence=float(row.min_confidence),
        min_score_separation=float(row.min_score_separation),
        cooldown_seconds=int(row.cooldown_seconds),
        max_spread_percent=float(row.max_spread_percent),
        min_option_volume=int(row.min_option_volume),
        min_option_oi=int(row.min_option_oi),
        min_rr=float(row.min_rr),
        sl_percent=float(row.sl_percent),
        target1_rr=float(row.target1_rr),
        target2_rr=float(row.target2_rr),
        supertrend_atr_mult=float(row.supertrend_atr_mult),
        time_start=row.time_start,
        time_end=row.time_end,
        nifty_enabled=bool(row.nifty_enabled),
        sensex_enabled=bool(row.sensex_enabled),
        ce_alerts=bool(row.ce_alerts),
        pe_alerts=bool(row.pe_alerts),
        notifications_enabled=bool(row.notifications_enabled),
        sound_enabled=bool(row.sound_enabled),
        browser_notify=bool(row.browser_notify),
    )


def config_dict(config: AlertConfig) -> dict:
    return {
        "notifications_enabled": config.notifications_enabled,
        "sound_enabled": config.sound_enabled,
        "browser_notify": config.browser_notify,
        "nifty_enabled": config.nifty_enabled,
        "sensex_enabled": config.sensex_enabled,
        "ce_alerts": config.ce_alerts,
        "pe_alerts": config.pe_alerts,
        "min_confidence": config.min_confidence,
        "min_score_separation": config.min_score_separation,
        "cooldown_seconds": config.cooldown_seconds,
        "max_spread_percent": config.max_spread_percent,
        "min_option_volume": config.min_option_volume,
        "min_option_oi": config.min_option_oi,
        "min_rr": config.min_rr,
        "sl_percent": config.sl_percent,
        "target1_rr": config.target1_rr,
        "target2_rr": config.target2_rr,
        "supertrend_atr_mult": config.supertrend_atr_mult,
        "time_start": config.time_start.strftime("%H:%M"),
        "time_end": config.time_end.strftime("%H:%M"),
        "alert_only": True,
        "note": "Alert-only. Analytical signal, not a guaranteed prediction. No broker order is placed.",
    }


def save_config(db: Session, data: dict) -> AlertConfig:
    row = db.get(AlertSetting, 1)
    if row is None:
        row = AlertSetting(id=1)
        db.add(row)
    booleans = (
        "notifications_enabled",
        "sound_enabled",
        "browser_notify",
        "nifty_enabled",
        "sensex_enabled",
        "ce_alerts",
        "pe_alerts",
    )
    for name in booleans:
        if name in data:
            setattr(row, name, bool(data[name]))
    for name in (
        "min_confidence",
        "min_score_separation",
        "cooldown_seconds",
        "max_spread_percent",
        "min_option_volume",
        "min_option_oi",
        "min_rr",
        "sl_percent",
        "target1_rr",
        "target2_rr",
        "supertrend_atr_mult",
    ):
        if data.get(name) is not None:
            setattr(row, name, data[name])
    for name in ("time_start", "time_end"):
        if data.get(name):
            hour, minute = str(data[name]).split(":")[:2]
            setattr(row, name, time(int(hour), int(minute)))
    row.alert_only = True
    db.flush()
    return load_config(db)


def run_alerts(runtime, db: Session) -> None:
    """Called from the market loop. Evaluates only; never submits an order."""
    try:
        config = load_config(db)
    except Exception:
        log.exception("alert settings unavailable")
        return
    from app.services.engine import market_view

    for symbol in ("NIFTY", "SENSEX"):
        try:
            view = market_view(db, runtime, symbol)
            _evaluate_symbol(runtime, db, view, config)
        except Exception:
            log.exception("alert evaluation failed for %s", symbol)
    _advance_open(runtime, db, config)


def ticker_notices(db: Session) -> list[dict]:
    rows = db.scalars(
        select(AlertSignal)
        .where(AlertSignal.signal == BUY, AlertSignal.status == NEW, AlertSignal.acknowledged == False)
        .order_by(AlertSignal.id.desc())
        .limit(10)
    ).all()
    return [_row_dict(row) for row in reversed(list(rows))]


def workspace(db: Session, runtime=None) -> dict:
    config = load_config(db)
    open_rows = [
        _row_dict(row)
        for row in db.scalars(select(AlertSignal).where(AlertSignal.status.in_(OPEN_STATUSES)).order_by(AlertSignal.id.desc())).all()
    ]
    history = [
        _row_dict(row)
        for row in db.scalars(select(AlertSignal).order_by(AlertSignal.id.desc()).limit(80)).all()
    ]
    log_rows = list(db.scalars(select(AlertLog).order_by(AlertLog.id.desc()).limit(80)).all())
    logs = [
        {
            "id": row.id,
            "time": jsonable(row.created_at),
            "instrument": row.instrument,
            "option_type": row.option_type,
            "event_type": row.event_type,
            "result": row.result,
            "reason": row.reason,
            "score": float(row.score) if row.score is not None else None,
        }
        for row in log_rows
    ]
    latest_scores = []
    seen = set()
    for row in log_rows:
        if row.event_type not in {"SCORE", "SIGNAL"}:
            continue
        key = (row.instrument, row.option_type)
        if key in seen:
            continue
        seen.add(key)
        latest_scores.append(
            {
                "instrument": row.instrument,
                "option_type": row.option_type,
                "signal": row.result,
                "score": float(row.score) if row.score is not None else None,
                "reason": row.reason,
                "time": jsonable(row.created_at),
            }
        )
    snapshots = []
    if runtime is not None:
        from app.services.engine import market_view

        for symbol in ("NIFTY", "SENSEX"):
            try:
                view = market_view(db, runtime, symbol)
                snapshots.append(_live_snapshot(db, runtime, view, config))
            except Exception:
                log.exception("alert snapshot failed for %s", symbol)
                snapshots.append({"instrument": symbol, "error": "snapshot unavailable"})
    return {
        "note": "ALERT ONLY — analytical signal, not a guaranteed prediction. No broker order is placed.",
        "config": config_dict(config),
        "open": open_rows,
        "history": history,
        "logs": logs,
        "snapshots": snapshots,
        "latest_scores": latest_scores,
        "historical_alerts": _historical_alerts(db),
        "disclaimer": "HYPOTHETICAL / PAPER signals only. No broker orders.",
        "timezone": "Asia/Kolkata",
        "strategy_name": "CE/PE option buy",
        "strategy_version": _strategy_version(db),
    }


def acknowledge(db: Session, signal_id: int) -> dict | None:
    row = db.get(AlertSignal, signal_id)
    if row is None:
        return None
    row.acknowledged = True
    if row.status == NEW:
        row.status = ACTIVE
    row.updated_at = as_utc_naive(now_utc())
    _log(db, row.instrument, row.option_type, "ACK", row.status, "Acknowledged", signal_id=row.id, score=row.confidence)
    return _row_dict(row)


def dismiss(db: Session, signal_id: int) -> dict | None:
    row = db.get(AlertSignal, signal_id)
    if row is None:
        return None
    row.status = CANCELLED
    row.updated_at = as_utc_naive(now_utc())
    _log(db, row.instrument, row.option_type, "DISMISS", CANCELLED, "Dismissed by user", signal_id=row.id, score=row.confidence)
    return _row_dict(row)


def _evaluate_symbol(runtime, db, view: dict, config: AlertConfig) -> None:
    from app.services.engine import INTERVALS, chain_snapshot, load_bars

    symbol = view.get("symbol") or ""
    evaluation = view.get("evaluation")
    params = view.get("params")
    profile = view.get("profile")
    future = view.get("future")
    packs = view.get("candidates") or {}
    interval = INTERVALS.get(params.timeframe_minutes, "minute") if params else "minute"
    bars = load_bars(db, int(future.instrument_token), interval) if future else []
    closes = [bar.close for bar in bars]
    ema9, ema21 = ema_pair(closes) if closes else (None, None)
    shared = (
        market_facts_from_bars(
            bars,
            ema_period=params.ema_period if params else 20,
            rsi_period=params.rsi_period if params else 14,
            atr_period=params.atr_period if params else 14,
            volume_ema_period=params.volume_ema_period if params else 20,
            timeframe=interval,
        )
        if bars
        else None
    )
    trend = view.get("displayed_direction") or implied_market_direction(
        close=evaluation.close if evaluation else None,
        ema=evaluation.ema if evaluation else None,
        vwap=evaluation.vwap if evaluation else None,
        momentum=evaluation.momentum if evaluation else None,
    )
    app = db.get(AppSetting, 1)
    above = app.strikes_above if app else 10
    below = app.strikes_below if app else 10
    chain = chain_snapshot(db, runtime, symbol, None, above, below)
    support, resistance = swing_support_resistance(bars) if bars else (None, None)
    last_buy = db.scalar(
        select(AlertSignal)
        .where(AlertSignal.instrument == symbol, AlertSignal.signal == BUY)
        .order_by(AlertSignal.id.desc())
    )
    clock = now_ist().time().replace(microsecond=0)
    facts = MarketFacts(
        instrument=symbol,
        clock=clock,
        stale=bool(view.get("stale")),
        close=(shared.close if shared and shared.close is not None else (evaluation.close if evaluation else view.get("futures_ltp") or view.get("ltp"))),
        vwap=(shared.vwap if shared and shared.vwap is not None else (evaluation.vwap if evaluation else view.get("vwap"))),
        ema20=(shared.ema20 if shared and shared.ema20 is not None else (evaluation.ema if evaluation else view.get("ema"))),
        ema9=ema9,
        ema21=ema21,
        rsi=(shared.rsi if shared and shared.rsi is not None else (evaluation.rsi if evaluation else view.get("rsi"))),
        atr=(shared.atr if shared and shared.atr is not None else (evaluation.atr if evaluation else view.get("atr"))),
        atr_threshold=float(profile.atr_threshold) if profile and profile.atr_threshold is not None else None,
        volume=(shared.volume if shared and shared.volume is not None else (evaluation.volume if evaluation else view.get("volume"))),
        volume_ema=(shared.volume_ema if shared and shared.volume_ema is not None else (evaluation.volume_ema if evaluation else view.get("volume_ema"))),
        momentum=evaluation.momentum if evaluation else None,
        trend=trend,
        supertrend=supertrend_side(bars, params.atr_period if params else 14, config.supertrend_atr_mult) if bars else None,
        support=support,
        resistance=resistance,
        pcr=chain.pcr if chain else None,
        data_time=now_utc(),
        now=now_utc(),
        last_buy_at=(
            last_buy.created_at.replace(tzinfo=timezone.utc)
            if last_buy and last_buy.created_at.tzinfo is None
            else (last_buy.created_at if last_buy else None)
        ),
        last_buy_side=last_buy.option_type if last_buy else None,
        last_buy_strike=float(last_buy.strike) if last_buy and last_buy.strike is not None else None,
    )
    ce = _option_facts("CE", packs.get("ce"), chain, runtime)
    pe = _option_facts("PE", packs.get("pe"), chain, runtime)
    _log(
        db,
        symbol,
        "",
        "INPUT",
        trend,
        f"close={facts.close} vwap={facts.vwap} ema9={ema9} ema21={ema21} rsi={facts.rsi} vol={facts.volume} pcr={facts.pcr} stale={facts.stale}",
        close=facts.close,
    )
    for decision in decide(facts, ce, pe, config):
        _persist_decision(db, decision, ce if decision.option_type == "CE" else pe, config)


def _option_facts(kind: str, candidate: dict | None, chain, runtime) -> OptionFacts | None:
    if not candidate:
        return None
    token = candidate.get("token")
    quote = runtime.cache.get(int(token)) if token and runtime else {}
    side = _chain_side(chain, candidate.get("strike"), kind)
    bid = (quote or {}).get("bid") if quote else None
    ask = (quote or {}).get("ask") if quote else None
    if bid is None and side:
        bid = side.bid
    if ask is None and side:
        ask = side.ask
    ltp = candidate.get("ltp")
    if ltp is None and quote:
        ltp = quote.get("ltp")
    return OptionFacts(
        option_type=kind,
        strike=candidate.get("strike"),
        expiry=candidate.get("expiry") or (chain.expiry.isoformat() if chain and chain.expiry else None),
        tradingsymbol=candidate.get("tradingsymbol") or "",
        ltp=ltp,
        bid=bid,
        ask=ask,
        volume=(quote or {}).get("volume") if quote else (side.volume if side else None),
        oi=(quote or {}).get("oi") if quote else (side.oi if side else None),
        oi_change=side.oi_change if side else None,
        iv=side.iv if side else None,
        strategy_score=float(candidate.get("score_percent") or 0),
        strategy_passed=int(candidate.get("passed_count") or 0),
        strategy_total=int(candidate.get("total_count") or 0),
        token=int(token) if token is not None else None,
    )


def _chain_side(chain, strike, kind: str):
    if chain is None or strike is None:
        return None
    for row in chain.rows:
        if float(row.strike) == float(strike):
            return row.ce if kind == "CE" else row.pe
    return None


def _persist_decision(db, decision: AlertDecision, option: OptionFacts | None, config: AlertConfig) -> None:
    reason_text = "; ".join(decision.blocked) if decision.signal == WAIT else "; ".join(row.detail for row in decision.reasons if row.passed)
    _log(
        db,
        decision.instrument,
        decision.option_type,
        "SCORE" if decision.signal == WAIT else "SIGNAL",
        decision.signal,
        reason_text[:500],
        close=None,
        score=decision.confidence,
    )
    if decision.signal != BUY:
        return
    if not config.notifications_enabled:
        _log(db, decision.instrument, decision.option_type, "NOTIFY_SKIP", WAIT, "Notifications disabled", score=decision.confidence)
        return
    now = as_utc_naive(now_utc())
    row = AlertSignal(
        created_at=now,
        updated_at=now,
        instrument=decision.instrument,
        option_type=decision.option_type,
        strike=Decimal(str(decision.strike)) if decision.strike is not None else None,
        expiry=_expiry(decision.expiry),
        tradingsymbol=decision.tradingsymbol,
        signal=BUY,
        status=NEW,
        confidence=Decimal(str(decision.confidence)),
        ce_score=Decimal(str(decision.ce_score)),
        pe_score=Decimal(str(decision.pe_score)),
        entry_price=_dec(decision.entry_price),
        entry_low=_dec(decision.entry_low),
        entry_high=_dec(decision.entry_high),
        stop_loss=_dec(decision.stop_loss),
        target1=_dec(decision.target1),
        target2=_dec(decision.target2),
        risk_reward=_dec(decision.risk_reward),
        last_premium=_dec(decision.entry_price),
        reasons_json=json.dumps([row.as_dict() for row in decision.reasons]),
        blocked_json=json.dumps(decision.blocked),
        warning=decision.warning or "",
        data_time=as_utc_naive(decision.data_time) if decision.data_time else now,
        alert_only=True,
        instrument_token=int(option.token) if option and option.token else None,
    )
    db.add(row)
    db.flush()
    _log(db, decision.instrument, decision.option_type, "NOTIFY", NEW, "BUY alert stored (alert-only)", signal_id=row.id, score=decision.confidence)
    from app.services.engine import audit

    audit(db, "ALERT_SIGNAL", underlying=decision.instrument, instrument=decision.tradingsymbol, result=BUY, reason=reason_text[:500], input_close=decision.entry_price, input_rsi=None, input_atr=None)


def _advance_open(runtime, db, config: AlertConfig) -> None:
    clock = now_ist().time()
    expired_session = clock >= config.time_end
    for row in db.scalars(select(AlertSignal).where(AlertSignal.status.in_(OPEN_STATUSES))).all():
        premium = None
        if row.instrument_token and runtime:
            quote = runtime.cache.get(int(row.instrument_token)) or {}
            premium = quote.get("ltp")
        if premium is not None:
            row.last_premium = Decimal(str(premium))
        nxt = next_status(
            row.status,
            float(premium) if premium is not None else None,
            float(row.stop_loss) if row.stop_loss is not None else None,
            float(row.target1) if row.target1 is not None else None,
            float(row.target2) if row.target2 is not None else None,
            expired_session,
        )
        if nxt != row.status:
            row.status = nxt
            row.updated_at = as_utc_naive(now_utc())
            _log(db, row.instrument, row.option_type, "LIFECYCLE", nxt, f"Status {nxt}", signal_id=row.id, score=row.confidence)


def _live_snapshot(db, runtime, view, config: AlertConfig) -> dict:
    from app.services.engine import INTERVALS, load_bars

    evaluation = view.get("evaluation")
    params = view.get("params")
    future = view.get("future")
    interval = INTERVALS.get(params.timeframe_minutes, "minute") if params else "minute"
    bars = load_bars(db, int(future.instrument_token), interval) if future else []
    ema9, ema21 = ema_pair([bar.close for bar in bars]) if bars else (None, None)
    shared = (
        market_facts_from_bars(
            bars,
            ema_period=params.ema_period if params else 20,
            rsi_period=params.rsi_period if params else 14,
            atr_period=params.atr_period if params else 14,
            volume_ema_period=params.volume_ema_period if params else 20,
            timeframe=interval,
        )
        if bars
        else None
    )
    packs = view.get("candidates") or {}
    return {
        "instrument": view.get("symbol"),
        "trend": view.get("displayed_direction"),
        "stale": bool(view.get("stale")),
        "close": shared.close if shared and shared.close is not None else (evaluation.close if evaluation else view.get("futures_ltp")),
        "vwap": shared.vwap if shared and shared.vwap is not None else (evaluation.vwap if evaluation else view.get("vwap")),
        "ema9": ema9,
        "ema21": ema21,
        "ema20": shared.ema20 if shared and shared.ema20 is not None else (evaluation.ema if evaluation else view.get("ema")),
        "rsi": shared.rsi if shared and shared.rsi is not None else (evaluation.rsi if evaluation else view.get("rsi")),
        "atr": shared.atr if shared and shared.atr is not None else (evaluation.atr if evaluation else view.get("atr")),
        "volume": shared.volume if shared and shared.volume is not None else (evaluation.volume if evaluation else view.get("volume")),
        "ce": packs.get("ce"),
        "pe": packs.get("pe"),
        "threshold": config.min_confidence,
    }


def _row_dict(row: AlertSignal) -> dict:
    try:
        reasons = json.loads(row.reasons_json or "[]")
    except json.JSONDecodeError:
        reasons = []
    try:
        blocked = json.loads(row.blocked_json or "[]")
    except json.JSONDecodeError:
        blocked = []
    return {
        "id": row.id,
        "instrument": row.instrument,
        "option_type": row.option_type,
        "strike": float(row.strike) if row.strike is not None else None,
        "expiry": jsonable(row.expiry),
        "tradingsymbol": row.tradingsymbol,
        "signal": row.signal,
        "status": row.status,
        "status_code": public_status(row.status, blocked=bool(blocked)),
        "confidence": float(row.confidence),
        "ce_score": float(row.ce_score),
        "pe_score": float(row.pe_score),
        "buy_score": float(row.ce_score) if row.option_type == "CE" else float(row.pe_score),
        "sell_score": float(row.pe_score) if row.option_type == "PE" else float(row.ce_score),
        "strategy_name": "CE/PE option buy",
        "strategy_version": "",
        "source": "LIVE",
        "label": "LIVE ALERT — analytical only",
        "entry_price": float(row.entry_price) if row.entry_price is not None else None,
        "entry_low": float(row.entry_low) if row.entry_low is not None else None,
        "entry_high": float(row.entry_high) if row.entry_high is not None else None,
        "stop_loss": float(row.stop_loss) if row.stop_loss is not None else None,
        "target1": float(row.target1) if row.target1 is not None else None,
        "target2": float(row.target2) if row.target2 is not None else None,
        "risk_reward": float(row.risk_reward) if row.risk_reward is not None else None,
        "last_premium": float(row.last_premium) if row.last_premium is not None else None,
        "reasons": reasons,
        "blocked": blocked,
        "warning": row.warning,
        "data_time": jsonable(row.data_time),
        "signal_time": jsonable(row.created_at),
        "acknowledged": row.acknowledged,
        "alert_only": True,
        "note": "HYPOTHETICAL / PAPER signals only. No broker orders.",
    }


def _strategy_version(db: Session) -> str:
    try:
        from app.db.bootstrap import latest_version

        version = latest_version(db)
        return str(getattr(version, "version", None) or getattr(version, "id", "") or "")
    except Exception:
        return ""


def _historical_alerts(db: Session) -> list[dict]:
    from app.db.models import HistoricalPaperRun, HistoricalPaperTrade
    from app.services.historical_backtest import repair_index_proxy_charges

    try:
        repair_index_proxy_charges(db)
    except Exception:
        log.exception("historical paper charge repair failed")

    run = db.scalar(
        select(HistoricalPaperRun)
        .where(HistoricalPaperRun.status == "COMPLETED", HistoricalPaperRun.total_trades > 0)
        .order_by(HistoricalPaperRun.id.desc())
    )
    if run is None:
        run = db.scalar(select(HistoricalPaperRun).where(HistoricalPaperRun.status == "COMPLETED").order_by(HistoricalPaperRun.id.desc()))
    if run is None:
        return []
    rows = list(db.scalars(select(HistoricalPaperTrade).where(HistoricalPaperTrade.run_id == run.id).order_by(HistoricalPaperTrade.id.desc()).limit(40)).all())
    out = []
    for row in rows:
        indicators = {}
        try:
            indicators = json.loads(row.indicators_json or "{}")
        except json.JSONDecodeError:
            indicators = {}
        status = row.exit_reason or "CLOSED"
        mapped = {
            "TARGET_HIT": "TARGET_HIT",
            "TARGET": "TARGET_HIT",
            "STOP_LOSS_HIT": "STOP_LOSS_HIT",
            "STOP_LOSS": "STOP_LOSS_HIT",
            "STRATEGY_INVALIDATED": "STRATEGY_INVALIDATED",
            "FORCE_SQUARE_OFF": "CLOSED",
            "TIME_EXIT": "CLOSED",
            "END_OF_DATA": "EXPIRED",
            "MANUAL_EXIT": "CLOSED",
        }.get(status, status or "CLOSED")
        out.append(
            {
                "id": f"hist-{row.id}",
                "trade_id": row.id,
                "instrument": row.instrument,
                "option_type": row.option_type,
                "signal": row.option_type if row.option_type in {"BUY", "SELL"} else "BUY",
                "status": status,
                "status_code": mapped,
                "buy_score": float(row.buy_score) if row.buy_score is not None else None,
                "sell_score": float(row.sell_score) if row.sell_score is not None else None,
                "entry_price": float(row.entry_price),
                "exit_price": float(row.exit_price) if row.exit_price is not None else None,
                "stop_loss": None,
                "target1": None,
                "risk_reward": None,
                "indicators": indicators,
                "explanation": row.explanation,
                "data_time": jsonable(row.entry_time),
                "signal_time": jsonable(row.entry_time),
                "candle_timestamp": jsonable(row.entry_time),
                "strategy_name": row.strategy_name or "index paper",
                "strategy_version": str(run.id),
                "source": "HISTORICAL_PAPER",
                "label": "HISTORICAL PAPER ALERT — NOT LIVE",
                "note": "HYPOTHETICAL / PAPER signals only. No broker orders.",
                "quantity": row.quantity,
                "gross_pnl": float(row.gross_pnl),
                "charges": float(row.total_charges or 0),
                "net_pnl": float(row.net_pnl),
                "exit_reason": status,
                "paper_mode": "INDEX_FUTURES_PROXY" if str(row.option_type or "").upper() in {"BUY", "SELL"} and row.strike is None else "OPTIONS",
            }
        )
    return out


def _log(db, instrument, option_type, event_type, result, reason, close=None, score=None, signal_id=None) -> None:
    db.add(
        AlertLog(
            created_at=as_utc_naive(now_utc()),
            instrument=instrument or "",
            option_type=option_type or "",
            event_type=event_type,
            result=result or "",
            reason=(reason or "")[:500],
            close_price=Decimal(str(close)) if close is not None else None,
            score=Decimal(str(score)) if score is not None else None,
            signal_id=signal_id,
        )
    )


def _dec(value):
    if value is None:
        return None
    return Decimal(str(value))


def _expiry(value):
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])

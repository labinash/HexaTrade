import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.kite.auth import current_session, kite_client, load_access_token, session_status
from app.adapters.kite.instruments import expiries, front_future, instruments_for, latest_dump, refresh_instruments, spot_instrument
from app.adapters.kite.market import Ticker, fetch_historical, fetch_quotes, historical_chunks
from app.adapters.kite.orders import KiteOrderGateway
from app.core.clock import IST, as_utc_naive, now_ist, now_utc, to_ist
from app.core.config import Settings
from app.db.bootstrap import latest_version, params_for
from app.db.models import (
    AppSetting,
    AuditEvent,
    Candle,
    Fill,
    HistoricalPaperRun,
    HistoricalPaperTrade,
    OiReference,
    OrderRecord,
    PaperTrade,
    Position,
    PositionEvent,
    Signal,
    SignalCondition,
    UnderlyingProfile,
)
from app.db.session import session_factory
from app.domain.backtest import coverage_gaps, run_backtest
from app.domain.candles import Bar, CandleAggregator
from app.domain.chain import build_chain, select_contract
from app.domain.execution import LiveBroker, OrderRequest
from app.domain.exits import ExitContext, PositionState, advance_trail, choose_exit, initial_levels, trail_is_armed
from app.domain.indicators import ema
from app.domain.option_buy import observation_at
from app.domain.paper import simulate_fill
from app.domain.risk import RiskContext, evaluate_risk
from app.domain.signals import classify_signal
from app.domain.strategy import evaluate_snapshot, snapshot_from_bars
from app.services.cache import QuoteCache, RedisBus
from app.services.mapping import exit_of, f, params_of
from app.services.option_buy import evaluate_option_sides, exit_alerts_for

log = logging.getLogger("hextrade.engine")

INTERVALS = {1: "minute", 3: "3minute", 5: "5minute", 10: "10minute", 15: "15minute", 30: "30minute", 60: "60minute"}


class AppRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache = QuoteCache()
        self.bus = RedisBus(settings.redis_url)
        self.live_latch = False
        self.chart_live_session_ack = False
        self.zerodha_snapshot: dict | None = None
        self.zerodha_snapshot_at: float = 0.0
        self.db_ok = False
        self.ticker: Ticker | None = None
        self.subscribed: set[int] = set()
        self.pending_release: set[int] = set()
        self.previous_passed: dict[str, bool] = {}
        self.last_bar: dict[str, str] = {}
        self.aggregators: dict[int, CandleAggregator] = {}
        self.instruments_ready = False
        self.backfilled: set[int] = set()
        self.last_rest_poll = datetime.min.replace(tzinfo=timezone.utc)
        self.last_ticker: dict | None = None

    def on_quote(self, quote: dict) -> None:
        self.cache.put(quote)
        token = quote.get("token")
        price = quote.get("ltp")
        moment = quote.get("timestamp") or now_ist()
        if token is None or price is None:
            return
        if isinstance(moment, datetime):
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=IST)
        else:
            moment = now_ist()
        aggregator = self.aggregators.setdefault(int(token), CandleAggregator(1))
        closed = aggregator.update(moment, float(price), float(quote.get("volume") or 0), float(quote.get("oi") or 0))
        if closed is not None:
            self._persist_bar(int(token), closed)

    def _persist_bar(self, token: int, bar: Bar) -> None:
        if not self.db_ok:
            return
        db = session_factory()()
        try:
            start = as_utc_naive(bar.start)
            existing = db.scalar(
                select(Candle).where(
                    Candle.instrument_token == token,
                    Candle.interval == "minute",
                    Candle.start_time == start,
                )
            )
            if existing is None:
                db.add(
                    Candle(
                        instrument_token=token,
                        interval="minute",
                        start_time=start,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=int(bar.volume),
                        oi=int(bar.oi),
                    )
                )
                db.commit()
        finally:
            db.close()


def audit(db: Session, event_type: str, **fields) -> None:
    db.add(
        AuditEvent(
            created_at=as_utc_naive(now_utc()),
            event_type=event_type,
            strategy_version_id=fields.get("strategy_version_id"),
            underlying=fields.get("underlying") or "",
            instrument=fields.get("instrument") or "",
            user_label=fields.get("user_label") or "local",
            input_close=fields.get("input_close"),
            input_rsi=fields.get("input_rsi"),
            input_atr=fields.get("input_atr"),
            input_oi_change=fields.get("input_oi_change"),
            result=fields.get("result") or "",
            reason=fields.get("reason") or "",
        )
    )


def run_cycle(runtime: AppRuntime) -> None:
    if not runtime.db_ok:
        return
    runtime.bus.connect() if not runtime.bus.ok else None
    db = session_factory()()
    try:
        _sync_market(runtime, db)
        for symbol in ("NIFTY", "SENSEX"):
            _evaluate_and_maybe_enter(runtime, db, symbol)
        _manage_exits(runtime, db)
        from app.services.paper_engine import run_paper_auto

        run_paper_auto(runtime, db)
        try:
            from app.services.chart_service import capture_live_signals

            capture_live_signals(runtime, db)
        except Exception:
            log.exception("chart signal capture failed")
        try:
            from app.services.alert_engine import run_alerts

            run_alerts(runtime, db)
        except Exception:
            log.exception("alert engine failed")
        db.commit()
    except Exception:
        db.rollback()
        log.exception("market cycle failed")
    finally:
        db.close()


def _sync_market(runtime: AppRuntime, db: Session) -> None:
    token = load_access_token(db, runtime.settings)
    if not token:
        return
    if not runtime.instruments_ready or latest_dump(db) != date.today():
        kite = kite_client(runtime.settings, token)
        refresh_instruments(db, kite)
        runtime.instruments_ready = True
    _poll_or_stream(runtime, db, token)


def _poll_or_stream(runtime: AppRuntime, db: Session, access_token: str) -> None:
    desired = _desired_subscriptions(runtime, db)
    if runtime.ticker is None:
        runtime.ticker = Ticker(runtime.settings.kite_api_key, access_token, runtime.on_quote)
        runtime.ticker.start(sorted(desired))
        runtime.subscribed = set(desired)
    else:
        # Only the difference is sent, so a token already followed is never
        # subscribed twice.
        added = desired - runtime.subscribed
        if added:
            runtime.ticker.subscribe(sorted(added))
            runtime.subscribed |= added
        # Releasing needs two consecutive cycles of being unwanted. A cold cache
        # briefly yields a smaller desired set, and dropping on that alone would
        # unsubscribe strikes only to resubscribe them a second later.
        unwanted = runtime.subscribed - desired
        confirmed = unwanted & runtime.pending_release
        if confirmed:
            runtime.ticker.unsubscribe(sorted(confirmed))
            runtime.subscribed -= confirmed
        runtime.pending_release = unwanted - confirmed
    fresh = runtime.ticker.connected and runtime.cache.updated_at and (
        now_utc() - runtime.cache.updated_at.replace(tzinfo=timezone.utc)
    ) < timedelta(seconds=runtime.settings.stale_seconds)
    if fresh:
        return
    if now_utc() - runtime.last_rest_poll < timedelta(seconds=1):
        return
    keys = _quote_keys(db, desired)
    if not keys:
        return
    kite = kite_client(runtime.settings, access_token)
    payload = fetch_quotes(kite, keys)
    for item in payload.values():
        depth = item.get("depth") or {}
        buy = (depth.get("buy") or [{}])[0]
        sell = (depth.get("sell") or [{}])[0]
        ohlc = item.get("ohlc") or {}
        # Through on_quote, not cache.put, so the minute aggregator keeps
        # building OI history while the websocket is unavailable.
        runtime.on_quote(
            {
                "token": item.get("instrument_token"),
                "ltp": item.get("last_price"),
                "volume": item.get("volume"),
                "oi": item.get("oi"),
                "change": item.get("net_change"),
                "bid": buy.get("price"),
                "ask": sell.get("price"),
                "bid_qty": buy.get("quantity"),
                "ask_qty": sell.get("quantity"),
                "open": ohlc.get("open"),
                "high": ohlc.get("high"),
                "low": ohlc.get("low"),
                "close": ohlc.get("close"),
                "timestamp": item.get("timestamp") or now_ist(),
            }
        )
    runtime.last_rest_poll = now_utc()


def _desired_subscriptions(runtime: AppRuntime, db: Session) -> set[int]:
    tokens: set[int] = set()
    setting = db.get(AppSetting, 1)
    above = setting.strikes_above if setting else 10
    below = setting.strikes_below if setting else 10
    for symbol in ("NIFTY", "SENSEX"):
        spot = spot_instrument(db, symbol)
        future = front_future(db, symbol)
        if spot:
            tokens.add(int(spot.instrument_token))
        if future:
            tokens.add(int(future.instrument_token))
        spot_ltp = runtime.cache.get(int(spot.instrument_token)).get("ltp") if spot else None
        expiry = _nearest_expiry(db, symbol)
        if expiry is None or spot_ltp is None:
            continue
        contracts = _option_rows(db, symbol, expiry)
        from app.domain.chain import atm_strike

        strikes = sorted({float(row.strike) for row in contracts if row.strike is not None})
        center = atm_strike(strikes, float(spot_ltp))
        if center is None:
            continue
        if center in strikes:
            index = strikes.index(center)
            window = set(strikes[max(0, index - below) : index + above + 1])
        else:
            window = set()
        for row in contracts:
            if row.strike is not None and float(row.strike) in window:
                tokens.add(int(row.instrument_token))
    try:
        from app.db.models import PaperTrade

        for row in db.scalars(select(PaperTrade).where(PaperTrade.status == "OPEN")).all():
            tokens.add(int(row.instrument_token))
    except Exception:
        pass
    return tokens


def _quote_keys(db: Session, tokens: set[int]) -> list[str]:
    if not tokens:
        return []
    from app.db.models import Instrument

    rows = db.scalars(select(Instrument).where(Instrument.instrument_token.in_(tokens))).all()
    seen = set()
    keys = []
    for row in rows:
        if row.instrument_token in seen:
            continue
        seen.add(row.instrument_token)
        keys.append(f"{row.exchange}:{row.tradingsymbol}")
    return keys


def _nearest_expiry(db: Session, symbol: str) -> date | None:
    found = expiries(db, symbol)
    return found[0] if found else None


def _option_rows(db, symbol: str, expiry: date):
    rows = []
    for kind in ("CE", "PE"):
        rows.extend(instruments_for(db, symbol, kind, expiry))
    return rows


def load_bars(db: Session, token: int, interval: str, limit: int = 400) -> list[Bar]:
    rows = db.scalars(
        select(Candle)
        .where(Candle.instrument_token == token, Candle.interval == interval)
        .order_by(Candle.start_time.desc())
        .limit(limit)
    ).all()
    return [_bar_from_row(row) for row in reversed(rows)]


def load_bars_for_tokens(db: Session, tokens: list[int], interval: str) -> dict[int, list[Bar]]:
    """One query for today's-ish bars. Used to rank nearby strikes without 20+ round trips."""
    unique = sorted({int(token) for token in tokens if token})
    if not unique:
        return {}
    cutoff = now_utc().replace(tzinfo=None) - timedelta(hours=36)
    rows = db.scalars(
        select(Candle)
        .where(
            Candle.instrument_token.in_(unique),
            Candle.interval == interval,
            Candle.start_time >= cutoff,
        )
        .order_by(Candle.instrument_token, Candle.start_time)
    ).all()
    grouped: dict[int, list[Bar]] = {token: [] for token in unique}
    for row in rows:
        grouped[int(row.instrument_token)].append(_bar_from_row(row))
    return grouped


def _bar_from_row(row) -> Bar:
    return Bar(
        start=row.start_time.replace(tzinfo=timezone.utc),
        open=float(row.open),
        high=float(row.high),
        low=float(row.low),
        close=float(row.close),
        volume=float(row.volume),
        oi=float(row.oi),
    )


def _ensure_reference(runtime: AppRuntime, db: Session, token: int, ref: time) -> None:
    today = now_ist().date()
    existing = db.scalar(
        select(OiReference).where(
            OiReference.instrument_token == token,
            OiReference.session_date == today,
            OiReference.reference_time == ref,
        )
    )
    if existing or token in runtime.backfilled:
        return
    access = load_access_token(db, runtime.settings)
    if not access:
        return
    runtime.backfilled.add(token)
    start = datetime.combine(today, time(9, 15), tzinfo=IST)
    end = datetime.combine(today, ref, tzinfo=IST) + timedelta(minutes=1)
    kite = kite_client(runtime.settings, access)
    try:
        candles = fetch_historical(kite, token, start, end, "minute", True)
    except Exception:
        log.info("reference backfill failed for %s", token)
        return
    for item in candles:
        moment = item["date"]
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=IST)
        start_utc = as_utc_naive(moment)
        if db.scalar(select(Candle.id).where(Candle.instrument_token == token, Candle.interval == "minute", Candle.start_time == start_utc)) is None:
            db.add(
                Candle(
                    instrument_token=token,
                    interval="minute",
                    start_time=start_utc,
                    open=item["open"],
                    high=item["high"],
                    low=item["low"],
                    close=item["close"],
                    volume=item.get("volume") or 0,
                    oi=item.get("oi") or 0,
                )
            )
        if to_ist(moment).time().replace(second=0, microsecond=0) == ref:
            db.add(
                OiReference(
                    instrument_token=token,
                    session_date=today,
                    reference_time=ref,
                    oi=int(item.get("oi") or 0),
                    close=item["close"],
                )
            )
    db.flush()


def ensure_today_minutes(runtime: AppRuntime, db: Session, token: int) -> None:
    """Store this session's minute bars for one contract, including 09:20 and 11:00.

    Missing reference minutes stay missing. The current bar is never written in
    their place, and a failed fetch is not treated as a successful backfill.
    """
    today = now_ist().date()
    key = f"{token}:{today.isoformat()}:session"
    if key not in runtime.backfilled:
        access = load_access_token(db, runtime.settings)
        if access:
            start = datetime.combine(today, time(9, 15), tzinfo=IST)
            end = min(now_ist(), datetime.combine(today, time(15, 30), tzinfo=IST))
            if end > start:
                kite = kite_client(runtime.settings, access)
                try:
                    candles = fetch_historical(kite, token, start, end, "minute", True)
                except Exception:
                    log.info("session backfill failed for %s", token)
                else:
                    _persist_minutes(db, token, candles, today)
                    runtime.backfilled.add(key)
    _fill_exact_minute(runtime, db, token, time(9, 20))
    _fill_exact_minute(runtime, db, token, time(11, 0))


def _fill_exact_minute(runtime: AppRuntime, db: Session, token: int, ref: time) -> None:
    if now_ist().time().replace(second=0, microsecond=0) < ref.replace(second=0, microsecond=0):
        return
    bars = load_bars(db, token, "minute")
    if observation_at(bars, ref, now_ist().date()) is not None:
        return
    today = now_ist().date()
    key = f"{token}:{today.isoformat()}:{ref.strftime('%H%M')}"
    if key in runtime.backfilled:
        return
    access = load_access_token(db, runtime.settings)
    if not access:
        return
    start = datetime.combine(today, ref, tzinfo=IST)
    end = start + timedelta(minutes=1)
    kite = kite_client(runtime.settings, access)
    try:
        candles = fetch_historical(kite, token, start, end, "minute", True)
    except Exception:
        log.info("exact-minute backfill failed for %s at %s", token, ref)
        return
    _persist_minutes(db, token, candles, today, only=ref)
    if observation_at(load_bars(db, token, "minute"), ref, now_ist().date()) is not None:
        runtime.backfilled.add(key)


def _persist_minutes(db: Session, token: int, candles: list[dict], today, only: time | None = None) -> None:
    for item in candles:
        moment = item["date"]
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=IST)
        clock = to_ist(moment).time().replace(second=0, microsecond=0)
        if only is not None and clock != only.replace(second=0, microsecond=0):
            continue
        if to_ist(moment).date() != today:
            continue
        start_utc = as_utc_naive(moment)
        if db.scalar(select(Candle.id).where(Candle.instrument_token == token, Candle.interval == "minute", Candle.start_time == start_utc)) is None:
            db.add(
                Candle(
                    instrument_token=token,
                    interval="minute",
                    start_time=start_utc,
                    open=item["open"],
                    high=item["high"],
                    low=item["low"],
                    close=item["close"],
                    volume=item.get("volume") or 0,
                    oi=item.get("oi") or 0,
                )
            )
        existing_ref = db.scalar(
            select(OiReference).where(
                OiReference.instrument_token == token,
                OiReference.session_date == today,
                OiReference.reference_time == clock,
            )
        )
        if existing_ref is None:
            db.add(
                OiReference(
                    instrument_token=token,
                    session_date=today,
                    reference_time=clock,
                    oi=int(item.get("oi") or 0),
                    close=item["close"],
                )
            )
    db.flush()


def ticker_view(db: Session, runtime: AppRuntime) -> dict:
    """Light header feed. Reads the quote cache only, so every screen can poll it."""
    items = []
    for symbol in ("NIFTY", "SENSEX"):
        profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
        spot = spot_instrument(db, symbol)
        future = front_future(db, symbol)
        spot_quote = runtime.cache.get(int(spot.instrument_token)) if spot else {}
        fut_quote = runtime.cache.get(int(future.instrument_token)) if future else {}
        ltp = spot_quote.get("ltp")
        change = spot_quote.get("change")
        percent = None
        if ltp is not None and change is not None:
            previous = float(ltp) - float(change)
            percent = (float(change) / previous * 100) if previous else None
        items.append(
            {
                "symbol": symbol,
                "ltp": ltp,
                "change": change,
                "change_percent": percent,
                "futures_ltp": fut_quote.get("ltp"),
                "stale": _is_stale(runtime, profile) if profile else True,
                "subscribed": bool(spot),
            }
        )
    setting = db.get(AppSetting, 1)
    payload = {
        "items": items,
        "as_of": runtime.cache.updated_at,
        "session_open": _session_open(db),
        "broker": session_status(current_session(db)),
        "mode": setting.trading_mode if setting else "ANALYSIS",
        "kill_switch": bool(setting.kill_switch) if setting else False,
        "chart_live_enabled": bool(setting.chart_live_enabled) if setting else False,
        "live_trading_badge": "LIVE TRADING ENABLED" if setting and setting.chart_live_enabled else "PAPER TRADING",
        "redis": runtime.bus.ok,
        "database": True,
    }
    try:
        from app.services.paper_engine import created_buys

        payload["paper_buys"] = created_buys(db)
    except Exception:
        payload["paper_buys"] = []
    try:
        from app.services.paper_engine import live_index_decisions, load_config as load_paper_config, remember_index_signal_notices

        paper_cfg = load_paper_config(db)
        payload["index_signals"] = remember_index_signal_notices(runtime, live_index_decisions(db, runtime, paper_cfg))
    except Exception:
        log.exception("index signal notices failed")
        payload["index_signals"] = []
    try:
        from app.services.alert_engine import config_dict, load_config, ticker_notices

        payload["alert_notices"] = ticker_notices(db)
        payload["alert_config"] = config_dict(load_config(db))
    except Exception:
        log.exception("alert ticker notices failed")
        payload["alert_notices"] = []
        payload["alert_config"] = {
            "notifications_enabled": False,
            "sound_enabled": False,
            "browser_notify": False,
            "alert_only": True,
        }
    runtime.last_ticker = payload
    return payload


def _session_open(db: Session) -> bool:
    now = now_ist().time()
    for profile in db.scalars(select(UnderlyingProfile)).all():
        if profile.session_start <= now <= profile.session_end:
            return True
    return False


def market_view(db: Session, runtime: AppRuntime, symbol: str) -> dict:
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    version = latest_version(db)
    if profile is None or version is None:
        return {"symbol": symbol, "error": "not configured"}
    future = front_future(db, symbol)
    spot = spot_instrument(db, symbol)
    lot = int(future.lot_size) if future else 1
    row = params_for(version, symbol)
    if row is None:
        return {"symbol": symbol, "error": "strategy is not configured for this underlying"}
    params = params_of(row, lot)
    interval = INTERVALS.get(params.timeframe_minutes, "minute")
    price_token = None
    if params.price_source == "spot" and spot:
        price_token = int(spot.instrument_token)
    elif future:
        price_token = int(future.instrument_token)
    oi_token = price_token
    if params.oi_source == "front_futures" and future:
        oi_token = int(future.instrument_token)
    if price_token:
        _ensure_reference(runtime, db, price_token, params.momentum_time)
    bars = load_bars(db, price_token, interval) if price_token else []
    if oi_token and oi_token != price_token:
        oi_bars = load_bars(db, oi_token, interval)
        if oi_bars and bars and len(oi_bars) == len(bars):
            bars = [
                Bar(b.start, b.open, b.high, b.low, b.close, b.volume, o.oi)
                for b, o in zip(bars, oi_bars)
            ]
    evaluation = evaluate_snapshot(snapshot_from_bars(bars, params), params, f(profile.atr_threshold), live=False)
    spot_quote = runtime.cache.get(int(spot.instrument_token)) if spot else {}
    fut_quote = runtime.cache.get(int(future.instrument_token)) if future else {}
    payload = {
        "symbol": symbol,
        "ltp": spot_quote.get("ltp"),
        "change": spot_quote.get("change"),
        "futures_ltp": fut_quote.get("ltp"),
        "futures_oi": fut_quote.get("oi"),
        "vwap": evaluation.vwap,
        "ema": evaluation.ema,
        "rsi": evaluation.rsi,
        "atr": evaluation.atr,
        "atr_threshold": f(profile.atr_threshold),
        "oi_change": evaluation.oi_change,
        "volume": evaluation.volume,
        "volume_ema": evaluation.volume_ema,
        "price_source": params.price_source,
        "oi_source": params.oi_source,
        # Direction is what the strategy is configured to look for. Signal is
        # whether the conditions for it are all met right now. They are separate.
        "direction": params.direction,
        "signal": evaluation.direction if evaluation.passed else "NO TRADE",
        "overall_signal": evaluation.overall_signal(),
        "option_type": params.option_type(),
        "preferred_option": params.option_type(),
        "buy_signal": f"BUY {params.option_type()}" if evaluation.passed else "NO TRADE",
        "total_conditions": evaluation.total_conditions,
        "passed_conditions": evaluation.passed_conditions,
        "failed_conditions": evaluation.failed_conditions,
        "passed": evaluation.passed,
        "failed": evaluation.failed_names,
        "conditions": [c.__dict__ for c in evaluation.conditions],
        "stale": _is_stale(runtime, profile),
        "lot_size": lot,
        "future_symbol": future.tradingsymbol if future else None,
        "evaluation": evaluation,
        "params": params,
        "version_id": version.id,
        "profile": profile,
        "future": future,
        "spot": spot,
    }
    ce_strategy, pe_strategy = evaluate_option_sides(db, runtime, payload)
    payload["ce_strategy"] = ce_strategy
    payload["pe_strategy"] = pe_strategy
    return payload


def _is_stale(runtime: AppRuntime, profile: UnderlyingProfile) -> bool:
    now = now_ist().time()
    if not (profile.session_start <= now <= profile.session_end):
        return False
    updated = runtime.cache.updated_at
    if updated is None:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return now_utc() - updated > timedelta(seconds=runtime.settings.stale_seconds)


def _evaluate_and_maybe_enter(runtime: AppRuntime, db: Session, symbol: str) -> None:
    view = market_view(db, runtime, symbol)
    evaluation = view.get("evaluation")
    if evaluation is None:
        return
    version_id = view["version_id"]
    bar_key = f"{symbol}:{evaluation.close}:{evaluation.rsi}:{len(view['conditions'])}"
    bars_token = view["future"].instrument_token if view["future"] else None
    latest = load_bars(db, int(bars_token), "minute", 1) if bars_token else []
    bar_id = latest[-1].start.isoformat() if latest else ""
    if runtime.last_bar.get(symbol) == bar_id:
        return
    runtime.last_bar[symbol] = bar_id
    # Keyed by direction as well, so flipping the configured direction while the
    # conditions keep passing is a fresh signal rather than a duplicate.
    key = f"{symbol}:{view['params'].direction}"
    if key not in runtime.previous_passed:
        prior = db.scalar(
            select(Signal)
            .where(Signal.underlying == symbol, Signal.direction == view["params"].direction)
            .order_by(Signal.id.desc())
        )
        runtime.previous_passed[key] = bool(prior.passed) if prior else False
    decision = classify_signal(runtime.previous_passed[key], evaluation.passed)
    runtime.previous_passed[key] = evaluation.passed
    signal = Signal(
        strategy_version_id=version_id,
        underlying=symbol,
        created_at=as_utc_naive(now_utc()),
        direction=evaluation.direction,
        passed=evaluation.passed,
        actionable=decision.action == "NEW",
        close_price=evaluation.close,
        ema_value=evaluation.ema,
        momentum_value=evaluation.momentum,
        oi_change=evaluation.oi_change,
        volume_value=evaluation.volume,
        volume_ema=evaluation.volume_ema,
        vwap_value=evaluation.vwap,
        rsi_value=evaluation.rsi,
        atr_value=evaluation.atr,
        failed_reasons=", ".join(evaluation.failed_names),
    )
    db.add(signal)
    db.flush()
    for index, condition in enumerate(evaluation.conditions):
        db.add(
            SignalCondition(
                signal_id=signal.id,
                sort_order=index,
                name=condition.name,
                current_value=condition.current,
                required_value=condition.required,
                status=condition.status,
            )
        )
    audit(
        db,
        "STRATEGY_EVALUATED",
        strategy_version_id=version_id,
        underlying=symbol,
        result=signal.direction,
        reason=signal.failed_reasons,
        input_close=evaluation.close,
        input_rsi=evaluation.rsi,
        input_atr=evaluation.atr,
        input_oi_change=evaluation.oi_change,
    )
    if decision.action == "NEW":
        audit(db, "SIGNAL_GENERATED", strategy_version_id=version_id, underlying=symbol, result=evaluation.direction, reason=decision.reason)
        _try_enter(runtime, db, view, signal)
    elif decision.action == "DUPLICATE":
        audit(db, "SIGNAL_REJECTED", strategy_version_id=version_id, underlying=symbol, result="DUPLICATE", reason=decision.reason)


def _try_enter(runtime: AppRuntime, db: Session, view: dict, signal: Signal) -> None:
    setting = db.get(AppSetting, 1)
    if setting is None or setting.trading_mode == "ANALYSIS" or not setting.auto_entry_armed:
        return
    if not runtime.bus.ok:
        audit(db, "SIGNAL_REJECTED", underlying=view["symbol"], result="BLOCKED", reason="redis is unavailable")
        return
    chain = chain_snapshot(db, runtime, view["symbol"], None, setting.strikes_above, setting.strikes_below)
    rule = latest_version(db).option_rule
    # The strategy direction decides CE or PE. Option selection ranks contracts
    # within that type and never overrides it.
    option_type = view["params"].option_type()
    chosen, why = select_contract(
        chain.rows,
        option_type=option_type,
        method=rule.method,
        strike_offset=rule.strike_offset,
        spot=chain.spot,
        premium_min=f(rule.premium_min),
        premium_max=f(rule.premium_max),
        min_volume=int(rule.min_volume),
        min_oi=int(rule.min_oi),
        max_spread=f(rule.max_spread),
        min_liquidity=float(rule.min_liquidity),
    )
    if chosen is None:
        audit(db, "SIGNAL_REJECTED", underlying=view["symbol"], result="NO_CONTRACT", reason=why)
        return
    future = view["future"]
    profile = view["profile"]
    quantity = int(setting.lots) * int(chosen.lot_size)
    capital = (chosen.ltp or 0) * quantity
    open_positions = db.scalars(select(Position).where(Position.status == "OPEN")).all()
    today = now_ist().date()
    trades_today = [
        row
        for row in db.scalars(select(Position)).all()
        if to_ist(row.entry_time.replace(tzinfo=timezone.utc)).date() == today
    ]
    underlying_trades = [row for row in trades_today if row.underlying == view["symbol"]]
    last_entry = max((row.entry_time for row in underlying_trades), default=None)
    cooldown = False
    if last_entry is not None:
        elapsed = now_utc() - last_entry.replace(tzinfo=timezone.utc)
        cooldown = elapsed.total_seconds() < latest_version(db).risk_policy.cooldown_seconds
    risk = latest_version(db).risk_policy
    session_row = current_session(db)
    decision = evaluate_risk(
        RiskContext(
            mode=setting.trading_mode,
            kill_switch=setting.kill_switch,
            data_stale=view["stale"],
            token_valid=session_status(session_row) in {"connected", "expiring"},
            now=now_ist().time().replace(microsecond=0),
            session_start=profile.session_start,
            session_end=profile.session_end,
            entry_start=risk.entry_start,
            entry_stop=risk.entry_stop,
            square_off=risk.forced_square_off,
            mis_cutoff=setting.mis_cutoff,
            daily_pnl=_daily_pnl(db),
            max_daily_loss=float(risk.max_daily_loss),
            trades_today=len(trades_today),
            max_trades_per_day=risk.max_trades_per_day,
            open_positions=len(open_positions),
            max_open_positions=risk.max_open_positions,
            exposure=sum(float(row.entry_price) * row.quantity for row in open_positions),
            max_exposure=float(risk.max_exposure),
            quantity=quantity,
            lot_size=int(chosen.lot_size),
            max_quantity=risk.max_quantity,
            capital_required=capital,
            max_capital=float(risk.max_capital),
            spread=chosen.spread,
            max_spread=float(risk.max_spread),
            option_volume=int(chosen.volume or 0),
            min_option_volume=int(risk.min_option_volume),
            option_oi=int(chosen.oi or 0),
            min_option_oi=int(risk.min_option_oi),
            exchange=future.exchange if future else "",
            expected_exchange=profile.derivatives_exchange,
            product=setting.product,
            underlying_trades=len(underlying_trades),
            max_trades_per_underlying=risk.max_trades_per_underlying,
            cooldown_active=cooldown,
            duplicate_signal=False,
            redis_ok=runtime.bus.ok,
            database_ok=runtime.db_ok,
        )
    )
    if not decision.allowed:
        audit(db, "RISK_CHECK_FAILED", underlying=view["symbol"], instrument=chosen.tradingsymbol, result="REJECTED", reason="; ".join(decision.reasons))
        return
    if setting.trading_mode == "LIVE" and not runtime.live_latch:
        audit(db, "RISK_CHECK_FAILED", underlying=view["symbol"], result="REJECTED", reason="live trading is not armed")
        return
    audit(db, "RISK_CHECK_PASSED", underlying=view["symbol"], instrument=chosen.tradingsymbol, result="PASS", reason="")
    _place(runtime, db, setting, view, signal, chosen, quantity, future.exchange if future else profile.derivatives_exchange)


def _place(runtime, db, setting, view, signal, chosen, quantity, exchange) -> None:
    key = f"entry-{signal.id}"
    tag = f"H{signal.id}E"[:20]
    existing = db.scalar(select(OrderRecord).where(OrderRecord.idempotency_key == key))
    if existing:
        return
    order = OrderRecord(
        idempotency_key=key,
        signal_id=signal.id,
        execution_mode=setting.trading_mode,
        side="BUY",
        exchange=exchange,
        tradingsymbol=chosen.tradingsymbol,
        instrument_token=int(chosen.token),
        quantity=quantity,
        order_type=setting.order_type,
        price=chosen.ltp,
        status="ORDER_REQUESTED",
        tag=tag,
        created_at=as_utc_naive(now_utc()),
    )
    db.add(order)
    db.flush()
    audit(db, "ORDER_REQUESTED", underlying=view["symbol"], instrument=chosen.tradingsymbol, result=setting.trading_mode, reason=key)
    if setting.trading_mode == "PAPER":
        fill = simulate_fill(
            "BUY",
            chosen.ltp,
            quantity,
            float(setting.slippage_points),
            float(setting.brokerage_per_order),
            float(setting.statutory_charge_bps),
            data_stale=view["stale"],
        )
        if fill is None:
            order.status = "BLOCKED"
            order.rejection_reason = "stale or missing price"
            audit(db, "ORDER_REJECTED", underlying=view["symbol"], instrument=chosen.tradingsymbol, result="BLOCKED", reason=order.rejection_reason)
            return
        _open_position(db, view, signal, chosen, quantity, fill.price, fill.charges, "PAPER", order)
        return
    _live_submit(runtime, db, setting, order, view, signal, chosen, quantity)


def _live_submit(runtime, db, setting, order, view, signal, chosen, quantity) -> None:
    access = load_access_token(db, runtime.settings)
    if not access:
        order.status = "REJECTED"
        order.rejection_reason = "broker session is not valid"
        audit(db, "ORDER_REJECTED", underlying=view["symbol"], result="REJECTED", reason=order.rejection_reason)
        return
    kite = kite_client(runtime.settings, access)
    gateway = KiteOrderGateway(kite)
    broker = LiveBroker(
        armed=runtime.live_latch,
        kill_switch=setting.kill_switch,
        market_protection_percent=f(setting.market_protection_percent),
        supports_market_protection=gateway.supports_market_protection,
    )
    request = OrderRequest(
        idempotency_key=order.idempotency_key,
        side="BUY",
        exchange=order.exchange,
        tradingsymbol=order.tradingsymbol,
        token=order.instrument_token,
        quantity=quantity,
        order_type=order.order_type,
        price=float(order.price) if order.price is not None else None,
        product=setting.product,
        tag=order.tag,
        mode="LIVE",
    )
    try:
        result = broker.submit(request, gateway, now_utc().timestamp())
    except Exception as exc:
        found = None
        try:
            found = gateway.find_by_tag(order.tag)
        except Exception:
            found = None
        if found is None:
            order.status = "REJECTED"
            order.rejection_reason = exc.__class__.__name__
            audit(db, "ORDER_REJECTED", underlying=view["symbol"], instrument=order.tradingsymbol, result="REJECTED", reason=order.rejection_reason)
            return
        result_id = found.broker_order_id
        order.broker_order_id = result_id
        order.status = found.status
        audit(db, "ORDER_ACCEPTED", underlying=view["symbol"], instrument=order.tradingsymbol, result=found.status, reason="recovered by tag lookup")
        return
    order.broker_order_id = result.broker_order_id
    order.status = result.status
    audit(db, "ORDER_ACCEPTED", underlying=view["symbol"], instrument=order.tradingsymbol, result=result.status, reason="")
    if result.status in {"COMPLETE", "FILLED"}:
        _open_position(db, view, signal, chosen, quantity, float(order.price or chosen.ltp or 0), 0, "LIVE", order)


def _open_position(db, view, signal, chosen, quantity, price, charges, mode, order) -> None:
    policy = exit_of(latest_version(db).exit_policy)
    stop, target = initial_levels(price, view["evaluation"].atr, policy)
    position = Position(
        underlying=view["symbol"],
        exchange=order.exchange,
        tradingsymbol=chosen.tradingsymbol,
        instrument_token=int(chosen.token),
        option_type=view["params"].option_type(),
        strike=chosen and _strike_of(db, chosen.token),
        expiry=_expiry_of(db, chosen.token),
        entry_time=as_utc_naive(now_utc()),
        entry_price=price,
        quantity=quantity,
        stop_loss=stop,
        target=target,
        atr_at_entry=view["evaluation"].atr,
        strategy_version_id=view["version_id"],
        signal_id=signal.id,
        execution_mode=mode,
        status="OPEN",
        charges=charges,
        mfe_price=price,
    )
    db.add(position)
    db.flush()
    order.position_id = position.id
    order.status = "FILLED" if mode == "PAPER" else order.status
    db.add(Fill(order_id=order.id, quantity=quantity, price=price, filled_at=as_utc_naive(now_utc())))
    db.add(PositionEvent(position_id=position.id, created_at=as_utc_naive(now_utc()), event_type="ENTRY", reason="SIGNAL", price=price))
    audit(db, "ORDER_FILLED", underlying=view["symbol"], instrument=chosen.tradingsymbol, result=mode, reason=f"{price}")


def _strike_of(db, token: int):
    from app.db.models import Instrument

    row = db.scalar(select(Instrument).where(Instrument.instrument_token == token).order_by(Instrument.id.desc()))
    return row.strike if row else 0


def _expiry_of(db, token: int):
    from app.db.models import Instrument

    row = db.scalar(select(Instrument).where(Instrument.instrument_token == token).order_by(Instrument.id.desc()))
    return row.expiry if row else None


def _manage_exits(runtime: AppRuntime, db: Session) -> None:
    setting = db.get(AppSetting, 1)
    if setting is None or not setting.auto_exit_armed:
        return
    if not runtime.bus.ok:
        return
    for position in db.scalars(select(Position).where(Position.status == "OPEN")).all():
        _exit_position_if_needed(runtime, db, setting, position, manual=False)


def _exit_position_if_needed(runtime, db, setting, position: Position, manual: bool) -> str:
    version = latest_version(db)
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == position.underlying))
    view = market_view(db, runtime, position.underlying)
    quote = runtime.cache.get(int(position.instrument_token))
    premium = quote.get("ltp")
    stale = view.get("stale", True)
    if manual:
        reason = "MANUAL_EXIT"
    elif setting.kill_switch:
        reason = "KILL_SWITCH"
    else:
        policy = exit_of(version.exit_policy)
        # Read from the position rather than the current configuration, so editing
        # the direction mid-trade cannot flip the reversal test under an open
        # position. The option type is what the trade was actually taken on.
        exit_params = params_for(version, position.underlying)
        exit_direction = "BEARISH" if position.option_type == (exit_params.bearish_option_type if exit_params else "PE") else "BULLISH"
        exit_rsi_low, exit_rsi_high = (0.0, 100.0)
        if exit_params is not None:
            if exit_direction == "BEARISH":
                exit_rsi_low, exit_rsi_high = float(exit_params.rsi_min_bearish), float(exit_params.rsi_max_bearish)
            else:
                exit_rsi_low, exit_rsi_high = float(exit_params.rsi_min), float(exit_params.rsi_max)
        state = PositionState(
            entry_price=float(position.entry_price),
            entry_time=position.entry_time.replace(tzinfo=timezone.utc),
            stop_loss=f(position.stop_loss),
            target=f(position.target),
            trail_price=f(position.trail_price),
            atr_at_entry=f(position.atr_at_entry),
            mfe_price=f(position.mfe_price),
        )
        if premium is not None:
            live_atr = view["evaluation"].atr if view.get("evaluation") else None
            if trail_is_armed(state, float(premium), policy):
                trail = advance_trail(state, float(premium), policy, live_atr)
                position.trail_price = trail
                state.trail_price = trail
            position.mfe_price = max(float(position.mfe_price or premium), float(premium))
        evaluation = view.get("evaluation")
        option_bars = load_bars(db, int(position.instrument_token), INTERVALS.get(view["params"].timeframe_minutes, "minute")) if view.get("params") else []
        premium_ema = None
        if option_bars and view.get("params"):
            premium_ema = ema([bar.close for bar in option_bars], view["params"].ema_period)[-1]
        reason = choose_exit(
            state,
            ExitContext(
                premium=float(premium) if premium is not None else None,
                now=now_utc(),
                clock=now_ist().time().replace(microsecond=0),
                close=evaluation.close if evaluation else None,
                ema=evaluation.ema if evaluation else None,
                vwap=evaluation.vwap if evaluation else None,
                rsi=evaluation.rsi if evaluation else None,
                rsi_min=exit_rsi_low,
                rsi_max=exit_rsi_high,
                direction=exit_direction,
                live_atr=evaluation.atr if evaluation else None,
                kill_switch=False,
                daily_loss_hit=_daily_pnl(db) <= -float(version.risk_policy.max_daily_loss) or (
                    premium is not None and (float(premium) - float(position.entry_price)) * position.quantity <= -float(version.risk_policy.max_loss_per_position)
                ),
                data_stale=stale,
                premium_ema=premium_ema,
            ),
            policy,
        )
        if reason == "EMERGENCY":
            reason = "EMERGENCY"
    if reason is None:
        if stale and (manual or setting.kill_switch):
            audit(db, "SIGNAL_REJECTED", underlying=position.underlying, instrument=position.tradingsymbol, result="BLOCKED", reason="stale data blocked exit")
        return "open"
    if stale:
        audit(db, reason, underlying=position.underlying, instrument=position.tradingsymbol, result="BLOCKED", reason="stale data blocked exit")
        return "blocked"
    if position.execution_mode == "PAPER" or setting.trading_mode == "PAPER":
        fill = simulate_fill("SELL", premium, position.quantity, float(setting.slippage_points), float(setting.brokerage_per_order), float(setting.statutory_charge_bps), data_stale=False)
        if fill is None:
            return "blocked"
        _close_position(db, position, fill.price, reason, fill.charges)
        return reason
    if position.execution_mode == "LIVE":
        if not runtime.live_latch:
            audit(db, reason, underlying=position.underlying, result="BLOCKED", reason="live trading is not armed")
            return "blocked"
        _live_exit(runtime, db, setting, position, premium, reason)
    return reason


def _close_position(db, position: Position, price: float, reason: str, charges: float) -> None:
    gross = (price - float(position.entry_price)) * position.quantity
    position.exit_time = as_utc_naive(now_utc())
    position.exit_price = price
    position.exit_reason = reason
    position.charges = float(position.charges or 0) + charges
    position.realized_pnl = gross - float(position.charges)
    position.status = "CLOSED"
    db.add(PositionEvent(position_id=position.id, created_at=as_utc_naive(now_utc()), event_type="EXIT", reason=reason, price=price))
    audit(db, reason, underlying=position.underlying, instrument=position.tradingsymbol, result="CLOSED", reason=reason, input_close=price)


def _live_exit(runtime, db, setting, position, premium, reason: str) -> None:
    key = f"exit-{position.id}-{reason}"
    if db.scalar(select(OrderRecord).where(OrderRecord.idempotency_key == key)):
        return
    order = OrderRecord(
        idempotency_key=key,
        position_id=position.id,
        execution_mode="LIVE",
        side="SELL",
        exchange=position.exchange,
        tradingsymbol=position.tradingsymbol,
        instrument_token=position.instrument_token,
        quantity=position.quantity,
        order_type=setting.order_type,
        price=premium,
        status="ORDER_REQUESTED",
        tag=f"X{position.id}"[:20],
        created_at=as_utc_naive(now_utc()),
    )
    db.add(order)
    db.flush()
    access = load_access_token(db, runtime.settings)
    if not access:
        order.status = "REJECTED"
        return
    kite = kite_client(runtime.settings, access)
    gateway = KiteOrderGateway(kite)
    broker = LiveBroker(
        armed=runtime.live_latch,
        kill_switch=False,
        market_protection_percent=f(setting.market_protection_percent),
        supports_market_protection=gateway.supports_market_protection,
    )
    request = OrderRequest(key, "SELL", position.exchange, position.tradingsymbol, position.instrument_token, position.quantity, setting.order_type, float(premium or 0), setting.product, order.tag, "LIVE")
    try:
        result = broker.submit(request, gateway, now_utc().timestamp())
    except Exception as exc:
        found = gateway.find_by_tag(order.tag)
        if found is None:
            order.status = "REJECTED"
            order.rejection_reason = exc.__class__.__name__
            return
        result_status = found.status
        order.broker_order_id = found.broker_order_id
        order.status = result_status
    else:
        order.broker_order_id = result.broker_order_id
        order.status = result.status
    if premium is not None:
        _close_position(db, position, float(premium), reason, 0)


def _daily_pnl(db: Session) -> float:
    today = now_ist().date()
    total = 0.0
    for row in db.scalars(select(Position)).all():
        if row.realized_pnl is not None and row.exit_time is not None:
            stamp = row.exit_time
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if to_ist(stamp).date() == today:
                total += float(row.realized_pnl)
    for row in db.scalars(select(PaperTrade).where(PaperTrade.status == "CLOSED")).all():
        if row.exit_time is None:
            continue
        stamp = row.exit_time
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if to_ist(stamp).date() == today:
            total += float(row.net_pnl or 0)
    return round(total, 2)


def _archive_and_paper_metrics(db: Session) -> dict:
    from app.domain.index_strategy import index_paper_net, performance_from_trades
    from app.domain.paper_auto import OPTION_HISTORY_UNAVAILABLE, PNL_FORMULA
    from app.services.historical_repo import quality_for, stored_coverage

    profiles = {row.symbol: row for row in db.scalars(select(UnderlyingProfile)).all()}
    quality = [quality_for(db, symbol, "1Minute", profiles.get(symbol)) for symbol in ("NIFTY", "SENSEX")]
    coverage = stored_coverage(db)
    paper_rows = []
    for row in db.scalars(select(PaperTrade)).all():
        item = {
            "id": row.id,
            "status": row.status,
            "option_type": row.option_type,
            "side": row.option_type,
            "instrument": row.underlying,
            "underlying": row.underlying,
            "quantity": row.quantity,
            "entry_price": float(row.entry_price),
            "exit_price": float(row.exit_price) if row.exit_price is not None else None,
            "gross_pnl": float(row.gross_pnl or 0),
            "total_charges": float(row.total_charges or 0),
            "brokerage": float(row.brokerage or 0),
            "slippage": float(row.other_charges or 0) if str(row.option_type) in {"BUY", "SELL"} else 0,
            "net_pnl": float(row.net_pnl or 0),
            "exit_reason": row.exit_reason,
            "exit_time": row.exit_time.isoformat() if row.exit_time else "",
            "date": to_ist(row.exit_time.replace(tzinfo=timezone.utc) if row.exit_time and row.exit_time.tzinfo is None else row.exit_time).date().isoformat() if row.exit_time else "",
        }
        if str(row.option_type) in {"BUY", "SELL"} and row.exit_price is not None and row.status != "OPEN":
            gross, slip, net = index_paper_net(row.option_type, row.entry_price, row.exit_price, row.quantity, slippage_points=0.5)
            item["gross_pnl"] = float(gross)
            item["slippage"] = float(slip)
            item["total_charges"] = float(slip)
            item["brokerage"] = 0
            item["net_pnl"] = float(net)
        paper_rows.append(item)
    hist_rows = []
    latest_run = db.scalar(
        select(HistoricalPaperRun)
        .where(HistoricalPaperRun.status == "COMPLETED", HistoricalPaperRun.total_trades > 0)
        .order_by(HistoricalPaperRun.id.desc())
    )
    if latest_run is None:
        latest_run = db.scalar(select(HistoricalPaperRun).where(HistoricalPaperRun.status == "COMPLETED").order_by(HistoricalPaperRun.id.desc()))
    if latest_run is not None:
        for row in db.scalars(select(HistoricalPaperTrade).where(HistoricalPaperTrade.run_id == latest_run.id)).all():
            item = {
                "id": row.id,
                "status": "CLOSED",
                "option_type": row.option_type,
                "side": row.option_type,
                "instrument": row.instrument,
                "quantity": row.quantity,
                "entry_price": float(row.entry_price),
                "exit_price": float(row.exit_price) if row.exit_price is not None else None,
                "gross_pnl": float(row.gross_pnl or 0),
                "total_charges": float(row.total_charges or 0),
                "brokerage": float(row.brokerage or 0),
                "slippage": float(row.slippage or 0),
                "net_pnl": float(row.net_pnl or 0),
                "exit_reason": row.exit_reason,
                "exit_time": row.exit_time.isoformat() if row.exit_time else "",
                "date": row.trade_date.isoformat() if row.trade_date else "",
                "buy_score": float(row.buy_score) if row.buy_score is not None else None,
                "sell_score": float(row.sell_score) if row.sell_score is not None else None,
                "strategy_name": row.strategy_name,
            }
            if str(row.option_type) in {"BUY", "SELL"} and row.exit_price is not None:
                gross, slip, net = index_paper_net(row.option_type, row.entry_price, row.exit_price, row.quantity, slippage_points=0.5)
                item["gross_pnl"] = float(gross)
                item["slippage"] = float(slip)
                item["total_charges"] = float(slip)
                item["brokerage"] = 0
                item["net_pnl"] = float(net)
            hist_rows.append(item)
    live_perf = performance_from_trades(paper_rows)
    hist_perf = performance_from_trades(hist_rows)
    return {
        "timezone": "Asia/Kolkata",
        "data_source": "market_historical_data INDEX 1Minute",
        "option_history_available": False,
        "proxy_note": OPTION_HISTORY_UNAVAILABLE,
        "pnl_formula": PNL_FORMULA,
        "coverage": coverage,
        "data_quality": quality,
        "paper_performance": live_perf,
        "historical_paper_performance": hist_perf,
        "historical_run_id": latest_run.id if latest_run else None,
        "note": "HYPOTHETICAL / PAPER signals only. No broker orders.",
    }


def chain_snapshot(db, runtime: AppRuntime, symbol: str, expiry: date | None, above: int, below: int):
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    expiry = expiry or _nearest_expiry(db, symbol)
    future = front_future(db, symbol)
    spot = spot_instrument(db, symbol)
    contracts = []
    if expiry:
        for row in _option_rows(db, symbol, expiry):
            contracts.append(
                {
                    "strike": float(row.strike),
                    "instrument_type": row.instrument_type,
                    "instrument_token": int(row.instrument_token),
                    "tradingsymbol": row.tradingsymbol,
                    "lot_size": int(row.lot_size),
                }
            )
    tokens = [item["instrument_token"] for item in contracts]
    if spot:
        tokens.append(int(spot.instrument_token))
    if future:
        tokens.append(int(future.instrument_token))
    quotes = runtime.cache.many(tokens)
    spot_ltp = quotes.get(int(spot.instrument_token), {}).get("ltp") if spot else None
    fut_ltp = quotes.get(int(future.instrument_token), {}).get("ltp") if future else None
    years = 0.0
    if expiry:
        years = max((expiry - now_ist().date()).days, 0) / 365
    setting = db.get(AppSetting, 1)
    rate = float(setting.risk_free_rate) if setting else runtime.settings.risk_free_rate
    stale = _is_stale(runtime, profile) if profile else True
    return build_chain(
        underlying=symbol,
        expiry=expiry,
        spot=float(spot_ltp) if spot_ltp is not None else None,
        futures_ltp=float(fut_ltp) if fut_ltp is not None else None,
        contracts=contracts,
        quotes=quotes,
        prev_oi={},
        strikes_above=above,
        strikes_below=below,
        as_of=runtime.cache.updated_at,
        stale=stale,
        rate=rate,
        years_to_expiry=years,
    )


def public_chain(db, runtime, symbol, expiry, above, below) -> dict:
    snap = chain_snapshot(db, runtime, symbol, expiry, above, below)
    def side(item):
        if item is None:
            return None
        return {
            "symbol": item.tradingsymbol,
            "ltp": item.ltp,
            "oi": item.oi,
            "oi_change": item.oi_change,
            "volume": item.volume,
            "iv": None if item.iv is None else round(item.iv * 100, 2),
            "change": item.change,
            "bid": item.bid,
            "ask": item.ask,
            "spread": item.spread,
        }
    return {
        "underlying": snap.underlying,
        "expiry": snap.expiry.isoformat() if snap.expiry else None,
        "spot": snap.spot,
        "futures_ltp": snap.futures_ltp,
        "atm": snap.atm,
        "stale": snap.stale,
        "total_ce_oi": snap.total_ce_oi,
        "total_pe_oi": snap.total_pe_oi,
        "pcr": snap.pcr,
        "max_pain": snap.max_pain,
        "max_pain_scope": snap.max_pain_scope,
        "highest_oi_strike": snap.highest_oi_strike,
        "highest_oi_change_strike": snap.highest_oi_change_strike,
        "strike_interval": snap.strike_interval,
        "window_limited": snap.window_limited,
        "rows": [
            {"strike": row.strike, "moneyness": row.moneyness, "ce": side(row.ce), "pe": side(row.pe)}
            for row in snap.rows
        ],
        "note": "An increase in open interest is shown as OI evidence. It is not treated as proof of long buildup.",
    }


def dashboard(db, runtime: AppRuntime) -> dict:
    setting = db.get(AppSetting, 1)
    cards = []
    for symbol in ("NIFTY", "SENSEX"):
        view = market_view(db, runtime, symbol)
        view.pop("evaluation", None)
        view.pop("params", None)
        view.pop("profile", None)
        view.pop("future", None)
        view.pop("spot", None)
        cards.append(view)
    exit_alerts = exit_alerts_for(db, runtime)
    positions = []
    for row in db.scalars(select(Position).where(Position.status == "OPEN")).all():
        quote = runtime.cache.get(int(row.instrument_token))
        ltp = quote.get("ltp")
        pnl = None if ltp is None else (float(ltp) - float(row.entry_price)) * row.quantity
        positions.append(
            {
                "id": row.id,
                "mode": row.execution_mode,
                "underlying": row.underlying,
                "symbol": row.tradingsymbol,
                "option_type": row.option_type,
                "strike": f(row.strike),
                "entry_price": f(row.entry_price),
                "ltp": ltp,
                "quantity": row.quantity,
                "pnl": pnl,
                "stop_loss": f(row.stop_loss),
                "target": f(row.target),
                "exit_reason": row.exit_reason,
            }
        )
    open_rows = db.scalars(select(Position).where(Position.status == "OPEN")).all()
    exposure = sum(float(row.entry_price) * row.quantity for row in open_rows)
    version = latest_version(db)
    risk = version.risk_policy if version else None
    daily = _daily_pnl(db)
    extras = {}
    try:
        extras = _archive_and_paper_metrics(db)
    except Exception:
        log.exception("dashboard archive metrics failed")
        extras = {"data_quality": [], "paper_performance": None, "note": "Archive metrics unavailable."}
    return {
        "markets": cards,
        "positions": positions,
        "exit_alerts": exit_alerts,
        "alert_history": _dashboard_alert_history(db, exit_alerts),
        "risk": {
            "daily_pnl": daily,
            "daily_loss_remaining": None if risk is None else float(risk.max_daily_loss) + daily,
            "trades_today": _trades_today(db),
            "open_exposure": exposure,
            "kill_switch": setting.kill_switch if setting else False,
            "mode": setting.trading_mode if setting else "ANALYSIS",
            "auto_entry_armed": setting.auto_entry_armed if setting else False,
            "auto_exit_armed": setting.auto_exit_armed if setting else False,
            "live_armed": runtime.live_latch,
            "redis": runtime.bus.ok,
            "database": runtime.db_ok,
        },
        "paper_auto": _paper_auto_status(db),
        "paper_buys": _paper_buys(db),
        **extras,
    }


def _paper_buys(db) -> list[dict]:
    try:
        from app.services.paper_engine import created_buys

        return created_buys(db)
    except Exception:
        return []


def _dashboard_alert_history(db, exit_alerts: list[dict]) -> list[dict]:
    """Recent option alerts and exit watches. Analytical only — no orders."""
    out = []
    for row in exit_alerts or []:
        out.append(
            {
                "id": f"exit-{row.get('position_id')}",
                "source": "exit_watch",
                "instrument": row.get("underlying"),
                "option_type": row.get("option_type"),
                "signal": row.get("reason") or "WATCH",
                "status": "ACTIVE" if row.get("reason") or row.get("trail_armed") else "QUIET",
                "symbol": row.get("symbol"),
                "note": row.get("note") or row.get("message") or "Exit alert only. No order was sent.",
                "at": None,
            }
        )
    try:
        from app.db.models import AlertSignal

        rows = db.scalars(select(AlertSignal).order_by(AlertSignal.id.desc()).limit(20)).all()
        for row in rows:
            out.append(
                {
                    "id": f"alert-{row.id}",
                    "source": "option_alert",
                    "instrument": row.instrument,
                    "option_type": row.option_type,
                    "signal": row.signal,
                    "status": row.status,
                    "confidence": float(row.confidence or 0),
                    "symbol": row.tradingsymbol,
                    "note": row.warning or "Alert only. No broker order.",
                    "at": row.created_at.isoformat() if row.created_at else None,
                }
            )
    except Exception:
        log.exception("dashboard alert history unavailable")
    return out[:30]


def _paper_auto_status(db) -> dict:
    try:
        from app.services.paper_engine import dashboard_status

        return dashboard_status(db)
    except Exception:
        return {"enabled": False, "note": "PAPER TRADING — NO REAL ORDERS", "label": "OFF"}


def _trades_today(db) -> int:
    today = now_ist().date()
    count = 0
    for row in db.scalars(select(Position)).all():
        if to_ist(row.entry_time.replace(tzinfo=timezone.utc)).date() == today:
            count += 1
    return count


def ingest_history(db, runtime: AppRuntime, symbol: str, start: datetime, end: datetime, interval: str) -> dict:
    access = load_access_token(db, runtime.settings)
    if not access:
        return {"stored": 0, "error": "broker session is not connected"}
    future = front_future(db, symbol)
    if future is None:
        return {"stored": 0, "error": "instrument master has no front future"}
    kite = kite_client(runtime.settings, access)
    stored = 0
    for chunk_start, chunk_end in historical_chunks(start, end, interval):
        rows = fetch_historical(kite, int(future.instrument_token), chunk_start, chunk_end, interval, True)
        for item in rows:
            moment = item["date"]
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=IST)
            start_utc = as_utc_naive(moment)
            exists = db.scalar(
                select(Candle.id).where(
                    Candle.instrument_token == int(future.instrument_token),
                    Candle.interval == interval,
                    Candle.start_time == start_utc,
                )
            )
            if exists:
                continue
            db.add(
                Candle(
                    instrument_token=int(future.instrument_token),
                    interval=interval,
                    start_time=start_utc,
                    open=item["open"],
                    high=item["high"],
                    low=item["low"],
                    close=item["close"],
                    volume=item.get("volume") or 0,
                    oi=item.get("oi") or 0,
                )
            )
            stored += 1
        db.commit()
    return {"stored": stored, "token": int(future.instrument_token)}


def execute_backtest(db, symbol: str, start: datetime, end: datetime, slippage: float, brokerage: float, statutory_bps: float) -> dict:
    version = latest_version(db)
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    future = front_future(db, symbol)
    if version is None or profile is None or future is None:
        return {"error": "missing strategy, profile, or instrument"}
    row = params_for(version, symbol)
    if row is None:
        return {"error": "strategy is not configured for this underlying"}
    params = params_of(row, int(future.lot_size))
    interval = INTERVALS.get(params.timeframe_minutes, "minute")
    bars = load_bars_between(db, int(future.instrument_token), interval, start, end)
    if coverage_gaps(start, end, bars, params.timeframe_minutes):
        from app.services.historical_repo import bars_from_rows, get_historical_candles

        bars = bars_from_rows(get_historical_candles(db, symbol, "1Minute", to_ist(start).date(), to_ist(end).date()))
        if not bars:
            return {
                "error": "No stored NIFTY/SENSEX candles cover this range. Import historical index data first. Backdated analysis uses the index itself when CE/PE history is missing.",
            }
    result = run_backtest(
        bars,
        params,
        exit_of(version.exit_policy),
        atr_threshold=f(profile.atr_threshold),
        slippage_points=slippage,
        brokerage_per_order=brokerage,
        statutory_bps=statutory_bps,
        quantity=int(future.lot_size),
        option_bars=None,
    )
    from app.db.models import BacktestRun, BacktestTrade

    run = BacktestRun(
        created_at=as_utc_naive(now_utc()),
        underlying=symbol,
        strategy_version_id=version.id,
        date_from=as_utc_naive(start),
        date_to=as_utc_naive(end),
        timeframe=interval,
        slippage_points=slippage,
        brokerage_per_order=brokerage,
        statutory_charge_bps=statutory_bps,
        trade_count=result.trade_count,
        wins=result.wins,
        losses=result.losses,
        gross_pnl=result.gross_pnl,
        net_pnl=result.net_pnl,
        max_drawdown=result.max_drawdown,
        average_win=result.average_win,
        average_loss=result.average_loss,
        win_rate=result.win_rate,
        profit_factor=result.profit_factor,
        max_consecutive_losses=result.max_consecutive_losses,
        average_holding_minutes=result.average_holding_minutes,
        option_pnl_available=False,
        note=result.note,
    )
    db.add(run)
    db.flush()
    for trade in result.trades:
        db.add(
            BacktestTrade(
                backtest_run_id=run.id,
                entry_time=as_utc_naive(trade.entry_time),
                exit_time=as_utc_naive(trade.exit_time),
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                quantity=trade.quantity,
                gross_pnl=trade.gross_pnl,
                net_pnl=trade.net_pnl,
                exit_reason=trade.exit_reason,
                holding_minutes=trade.holding_minutes,
            )
        )
    db.commit()
    return {"id": run.id, "note": result.note, "trade_count": result.trade_count, "net_pnl": result.net_pnl, "option_pnl_available": False}


def load_bars_between(db, token: int, interval: str, start: datetime, end: datetime) -> list[Bar]:
    rows = db.scalars(
        select(Candle)
        .where(
            Candle.instrument_token == token,
            Candle.interval == interval,
            Candle.start_time >= as_utc_naive(start),
            Candle.start_time <= as_utc_naive(end),
        )
        .order_by(Candle.start_time)
    ).all()
    return [
        Bar(
            start=row.start_time.replace(tzinfo=timezone.utc),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            oi=float(row.oi),
        )
        for row in rows
    ]

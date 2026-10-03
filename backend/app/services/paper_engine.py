"""Automatic PAPER trades from existing CE/PE scores. Never calls a broker."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import as_utc_naive, now_ist, now_utc, to_ist
from app.db.bootstrap import latest_version
from app.db.models import ChargeRateRow, PaperAutoSetting, PaperTrade, PaperTradeCondition, PaperTradeEvent
from app.domain.charges import ChargeBreakdown, ChargeRate, ZerodhaChargeCalculator, default_calculator, money
from app.domain.dashboard_brief import implied_market_direction, score_percent
from app.domain.exits import ExitContext, PositionState, advance_trail, choose_exit, initial_levels, trail_is_armed
from app.domain.indicators import ema
from app.domain.paper_auto import (
    PaperAutoConfig,
    aligned_with_tape,
    choose_eligible_candidate,
    complete_setup,
    daily_summary,
    gated_sides,
    gross_pnl,
    idempotency_key,
    index_side_for_tape,
    net_pnl,
    pick_entry_price,
    side_enabled,
    signed_gross,
)
from app.services.mapping import exit_of, f, jsonable

log = logging.getLogger("hextrade.paper")
_coverage_memo: dict = {"at": 0.0, "rows": None}
INDEX_PAPER_MINUTES = 3
INDEX_CONFIRM_MINUTES = 5
INDEX_SIGNAL_NOTICE_TTL = 45.0


def load_index_tf_bars(db: Session, token: int, minutes: int, limit: int = 400, *, closed: bool = True):
    """Resample live 1-minute futures candles to the paper walk. Does not read the historical archive."""
    from app.domain.candles import resample_bars
    from app.domain.index_strategy import closed_bars_asof
    from app.services.engine import load_bars

    width = max(1, int(minutes or 1))
    one = load_bars(db, int(token), "minute", max(limit * width, 1500))
    bars = one if width <= 1 else resample_bars(one, width)
    if closed:
        return closed_bars_asof(bars, now_utc(), width)
    return bars


def remember_index_signal_notices(runtime, decisions: dict) -> list[dict]:
    """Rising-edge BUY/SELL for toasts. Sticky for a short window so a poll cannot miss it."""
    import time as clock_mod

    prev = getattr(runtime, "last_index_side", None) or {}
    sticky = [row for row in list(getattr(runtime, "index_signal_notices", None) or []) if clock_mod.monotonic() - float(row.get("_ts") or 0) < INDEX_SIGNAL_NOTICE_TTL]
    now = clock_mod.monotonic()
    next_side = dict(prev)
    for symbol, payload in (decisions or {}).items():
        side = str((payload or {}).get("decision") or "HOLD").upper()
        last = str(prev.get(symbol) or "HOLD").upper()
        next_side[symbol] = side
        if side not in {"BUY", "SELL"} or side == last:
            continue
        sticky.append(
            {
                "id": f"{symbol}|{side}|{(payload or {}).get('bar_unix') or ''}",
                "symbol": symbol,
                "side": side,
                "buy_score": (payload or {}).get("buy_score"),
                "sell_score": (payload or {}).get("sell_score"),
                "reason": (payload or {}).get("reason") or "",
                "timeframe": (payload or {}).get("timeframe") or f"{INDEX_PAPER_MINUTES}m",
                "entry_price": (payload or {}).get("close"),
                "_ts": now,
            }
        )
    if runtime is not None:
        runtime.last_index_side = next_side
        runtime.index_signal_notices = sticky
    return [{key: value for key, value in row.items() if key != "_ts"} for row in sticky]


def load_config(db: Session) -> PaperAutoConfig:
    row = db.get(PaperAutoSetting, 1)
    if row is None:
        return PaperAutoConfig()
    return PaperAutoConfig(
        enabled=bool(row.enabled),
        min_signal_score=Decimal(str(row.min_signal_score)),
        candidate_strike_range=int(row.candidate_strike_range),
        max_open_positions=int(row.max_open_positions),
        max_trades_per_day=int(row.max_trades_per_day),
        max_daily_loss=Decimal(str(row.max_daily_loss)),
        max_loss_per_trade=Decimal(str(row.max_loss_per_trade)),
        square_off=row.square_off,
        allow_reentry=bool(row.allow_reentry),
        cooldown_minutes=int(row.cooldown_minutes),
        entry_price_source=row.entry_price_source or "LTP",
        lots=max(1, int(row.lots or 1)),
        min_hold_minutes=max(0, int(getattr(row, "min_hold_minutes", 5) or 5)),
        ce_enabled=bool(getattr(row, "ce_strategy_enabled", True)),
        pe_enabled=bool(getattr(row, "pe_strategy_enabled", True)),
        paper_instrument=_paper_instrument_of(row),
        paper_mode=str(getattr(row, "paper_mode", None) or "MANUAL").upper(),
        auto_paused=bool(getattr(row, "auto_paused", False)),
        index_strategy=_index_strategy_dict(row),
    )


def load_calculator(db: Session, as_of=None) -> ZerodhaChargeCalculator:
    today = as_of or now_ist().date()
    rows = db.scalars(select(ChargeRateRow).where(ChargeRateRow.segment == "NFO")).all()
    rows = [row for row in rows if row.is_active]
    active = []
    for row in rows:
        if row.effective_from and row.effective_from > today:
            continue
        if row.effective_to and row.effective_to < today:
            continue
        active.append(
            ChargeRate(
                charge_type=row.charge_type,
                side=row.side,
                rate_percent=Decimal(str(row.rate_percent or 0)),
                flat_amount=Decimal(str(row.flat_amount or 0)),
                cap_amount=Decimal(str(row.cap_amount)) if row.cap_amount is not None else None,
                taxable=bool(row.taxable),
            )
        )
    return ZerodhaChargeCalculator(active) if active else default_calculator()


def run_paper_auto(runtime, db: Session) -> None:
    """Called from the market loop. Dashboard does not need to be open."""
    try:
        config = load_config(db)
    except Exception:
        log.exception("paper auto settings are unavailable")
        return
    if not config.enabled:
        return
    nested = db.begin_nested()
    try:
        calculator = load_calculator(db)
        _manage_open(runtime, db, config, calculator)
        auto_entries = config.paper_mode in {"AUTOMATIC", "AUTO", "AUTOMATIC_PAPER"} and not config.auto_paused
        if auto_entries and not _halted(db, config):
            from app.services.engine import market_view

            for symbol in ("NIFTY", "SENSEX"):
                view = market_view(db, runtime, symbol)
                if config.paper_instrument == "INDEX":
                    _consider_index_entry(runtime, db, config, calculator, view)
                else:
                    _consider_entry(runtime, db, config, calculator, view)
        nested.commit()
    except Exception:
        nested.rollback()
        log.exception("paper auto cycle failed")


def _manage_open(runtime, db, config: PaperAutoConfig, calculator: ZerodhaChargeCalculator) -> None:
    from app.services.engine import INTERVALS, load_bars, market_view
    from app.db.bootstrap import latest_version

    version = latest_version(db)
    policy = exit_of(version.exit_policy) if version else None
    clock = now_ist().time().replace(microsecond=0)
    for trade in db.scalars(select(PaperTrade).where(PaperTrade.status == "OPEN")).all():
        quote = runtime.cache.get(int(trade.instrument_token)) or {}
        premium = quote.get("ltp")
        view = market_view(db, runtime, trade.underlying)
        stale = bool(view.get("stale"))
        if premium is None and _paper_kind(trade) == "INDEX":
            premium = view.get("futures_ltp") or view.get("ltp")
        short = _index_short(trade)
        mark = None
        if premium is not None:
            mark = _exit_mark(float(trade.entry_price), float(premium), short)
            trade.mfe_price = max(Decimal(str(trade.mfe_price or mark)), Decimal(str(mark)))
        reason = None
        if clock >= config.square_off:
            reason = "END_OF_SESSION"
        elif _paper_kind(trade) == "INDEX" and premium is not None and not stale:
            from app.domain.index_exits import IndexExitMarket, IndexExitState, evaluate_index_exit
            from app.domain.index_strategy import config_from_dict, is_index_expiry

            idx = config_from_dict(config.index_strategy)
            evaluation = view.get("evaluation")
            atr_now = evaluation.atr if evaluation else None
            risk = abs(float(trade.entry_price) - float(trade.stop_loss or trade.entry_price))
            expiry = is_index_expiry(trade.underlying, now_ist().date())
            square = idx.expiry_square_off if expiry else idx.square_off
            from app.domain.index_strategy import parse_clock
            from datetime import time as time_cls

            reason, extras = evaluate_index_exit(
                IndexExitState(
                    side=str(trade.option_type),
                    entry=float(trade.entry_price),
                    stop=float(trade.stop_loss) if trade.stop_loss is not None else None,
                    target=float(trade.target) if trade.target is not None else None,
                    trail=float(trade.trail_price) if trade.trail_price is not None else None,
                    atr=atr_now,
                    risk=risk,
                    bars_held=max(0, int(((now_ist() - to_ist(trade.entry_time.replace(tzinfo=trade.entry_time.tzinfo or timezone.utc))).total_seconds()) // 180)),
                    remaining_qty=int(trade.quantity),
                    initial_qty=int(trade.quantity),
                    partial_taken=False,
                    breakout_level=None,
                    mfe=float(trade.mfe_price or trade.entry_price),
                ),
                IndexExitMarket(
                    high=float(premium),
                    low=float(premium),
                    close=float(premium),
                    ema20=evaluation.ema if evaluation else None,
                    vwap=evaluation.vwap if evaluation else None,
                    premium=None,
                    premium_entry=None,
                    clock=clock,
                    square=parse_clock(idx.square_off, time_cls(15, 15)),
                    expiry_square=parse_clock(idx.expiry_square_off, time_cls(14, 30)),
                    is_expiry=expiry,
                    is_entry_bar=False,
                    end_of_walk=False,
                    end_of_session=clock >= parse_clock(square, time_cls(15, 15)),
                    opposite_score=None,
                    opposite_threshold=float(idx.buy_threshold),
                ),
                variant=idx.exit_variant,
                premium_stop_percent=idx.premium_stop_percent,
                partial_at_r=idx.partial_at_r,
                chandelier_mult=idx.chandelier_atr_mult,
                time_stop_bars=idx.time_stop_bars,
                time_stop_min_r=idx.time_stop_min_r,
                failed_breakout_bars=idx.failed_breakout_bars,
            )
            if extras.get("new_stop") is not None:
                trade.stop_loss = Decimal(str(extras["new_stop"]))
            if extras.get("new_trail") is not None:
                trade.trail_price = Decimal(str(extras["new_trail"]))
        elif policy is not None and premium is not None and not stale:
            evaluation = view.get("evaluation")
            params = view.get("params")
            interval = INTERVALS.get(params.timeframe_minutes, "minute") if params else "minute"
            option_bars = load_bars(db, int(trade.instrument_token), interval)
            premium_ema = ema([bar.close for bar in option_bars], params.ema_period)[-1] if option_bars and params else None
            state = PositionState(
                entry_price=float(trade.entry_price),
                entry_time=trade.entry_time.replace(tzinfo=trade.entry_time.tzinfo or timezone.utc),
                stop_loss=f(trade.stop_loss),
                target=f(trade.target),
                trail_price=f(trade.trail_price),
                atr_at_entry=None,
                mfe_price=f(trade.mfe_price),
            )
            if trail_is_armed(state, mark, policy):
                trade.trail_price = advance_trail(state, mark, policy, evaluation.atr if evaluation else None)
            reason = choose_exit(
                state,
                ExitContext(
                    premium=mark,
                    now=now_utc(),
                    clock=clock,
                    close=evaluation.close if evaluation else None,
                    ema=evaluation.ema if evaluation else None,
                    vwap=evaluation.vwap if evaluation else None,
                    rsi=evaluation.rsi if evaluation else None,
                    rsi_min=0,
                    rsi_max=100,
                    live_atr=evaluation.atr if evaluation else None,
                    kill_switch=False,
                    daily_loss_hit=_session_net(db) <= -config.max_daily_loss,
                    data_stale=stale,
                    direction="BEARISH" if trade.option_type in {"PE", "SELL"} else "BULLISH",
                    premium_ema=premium_ema,
                ),
                policy,
            )
            if reason == "STRATEGY_INVALIDATED" and not _held_long_enough(trade, config.min_hold_minutes):
                reason = None
        if reason and premium is not None and not stale:
            _close(db, trade, premium, view.get("futures_ltp") or view.get("ltp"), reason, calculator)
            continue
        if premium is not None:
            _mark(trade, premium, calculator)


def _consider_index_entry(runtime, db, config: PaperAutoConfig, calculator: ZerodhaChargeCalculator, view: dict) -> None:
    symbol = view.get("symbol") or ""
    if view.get("stale"):
        _event(db, None, symbol, "PAPER ENTRY BLOCKED: live quotes are stale.")
        return
    from app.adapters.kite.instruments import front_future
    from app.domain.index_strategy import (
        config_from_dict,
        confirm_with_higher_tf,
        decide,
        features_from_bars,
        higher_tf_decision,
        validate_index_entry,
    )

    cfg = config_from_dict(config.index_strategy)
    future = front_future(db, symbol)
    if future is None:
        _event(db, None, symbol, f"PAPER ENTRY BLOCKED: front-month {symbol} future is not in the instrument dump.")
        return
    walk_minutes = INDEX_PAPER_MINUTES
    bars = load_index_tf_bars(db, int(future.instrument_token), walk_minutes)
    if len(bars) < max(cfg.ema_period, cfg.breakout_lookback) + 2:
        _event(db, None, symbol, f"PAPER ENTRY BLOCKED: not enough {symbol} {walk_minutes}m candles for indicators.")
        return
    evaluation = view.get("evaluation")
    live = view.get("displayed_direction")
    if not live:
        live = implied_market_direction(
            close=evaluation.close if evaluation else None,
            ema=evaluation.ema if evaluation else None,
            vwap=evaluation.vwap if evaluation else None,
            momentum=evaluation.momentum if evaluation else None,
        )
    facts = features_from_bars(bars, cfg, tape=live)
    verdict = decide(facts, cfg)
    if verdict.decision == "HOLD":
        return
    if not side_enabled(config.ce_enabled, config.pe_enabled, verdict.decision):
        return
    clock = now_ist()
    stamp = clock.time().replace(microsecond=0)
    today = clock.date()
    todays = [row for row in db.scalars(select(PaperTrade)).all() if _entry_date(row) == today and str(getattr(row, "instrument_kind", "") or "").upper() == "INDEX"]
    stopped_today = any(
        str(row.status or "").upper() == "CLOSED" and str(row.exit_reason or "").upper() in {"STOP_LOSS_HIT", "STOP_LOSS"}
        for row in todays
    )
    last_entry = None
    for row in todays:
        moment = row.entry_time
        if moment is None:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if last_entry is None or moment > last_entry:
            last_entry = moment
    confirm_side = None
    if confirm_with_higher_tf(walk_minutes, cfg):
        five = load_index_tf_bars(db, int(future.instrument_token), int(cfg.confirm_minutes or INDEX_CONFIRM_MINUTES), closed=False)
        asof = bars[-1].start + timedelta(minutes=walk_minutes)
        confirm_side = higher_tf_decision(five, asof, cfg, int(cfg.confirm_minutes or INDEX_CONFIRM_MINUTES))
    ok, why = validate_index_entry(
        side=verdict.decision,
        clock=stamp,
        config=cfg,
        day_trades=len(todays),
        day_pnl=_session_net(db),
        stopped_today=stopped_today,
        last_entry_at=to_ist(last_entry) if last_entry is not None else None,
        now=clock,
        higher_tf_side=confirm_side,
        score=verdict.buy.score if verdict.decision == "BUY" else verdict.sell.score,
        walk_minutes=walk_minutes,
    )
    if not ok:
        _event(db, None, symbol, f"PAPER ENTRY BLOCKED: {why}")
        return
    try:
        open_index_paper(
            db,
            runtime,
            symbol,
            verdict.decision,
            score=verdict.buy.score if verdict.decision == "BUY" else verdict.sell.score,
            passed=sum(1 for item in (verdict.buy if verdict.decision == "BUY" else verdict.sell).conditions if item.passed),
            total=len((verdict.buy if verdict.decision == "BUY" else verdict.sell).conditions),
            conditions=[item.as_dict() for item in (verdict.buy if verdict.decision == "BUY" else verdict.sell).conditions],
            auto=True,
            config=config,
            calculator=calculator,
            view=view,
        )
    except ValueError as error:
        _event(db, None, symbol, str(error))


def open_index_paper(
    db: Session,
    runtime,
    instrument: str,
    side: str,
    *,
    score: float | None = None,
    passed: int | None = None,
    total: int | None = None,
    conditions=None,
    auto: bool = False,
    config: PaperAutoConfig | None = None,
    calculator: ZerodhaChargeCalculator | None = None,
    view: dict | None = None,
) -> PaperTrade:
    """Paper BUY/SELL the front-month index future. Never calls a broker."""
    symbol = (instrument or "").upper()
    if symbol not in {"NIFTY", "SENSEX"}:
        raise ValueError("instrument must be NIFTY or SENSEX")
    side = (side or "").upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    config = config or load_config(db)
    calculator = calculator or load_calculator(db)
    if _halted(db, config):
        raise ValueError("PAPER TRADING HALTED. Daily loss limit reached.")
    from app.adapters.kite.instruments import front_future
    from app.services.engine import market_view

    view = view if view is not None else market_view(db, runtime, symbol)
    if view.get("stale"):
        raise ValueError("PAPER ENTRY BLOCKED: live quotes are stale.")
    future = front_future(db, symbol)
    if future is None or not future.instrument_token:
        raise ValueError(f"PAPER ENTRY BLOCKED: front-month {symbol} future is not in the instrument dump.")
    quote = runtime.cache.get(int(future.instrument_token)) if runtime else {}
    price = pick_entry_price(quote or {}, config.entry_price_source)
    if price is None:
        price = view.get("futures_ltp") or view.get("ltp")
    if price is None:
        raise ValueError(f"PAPER ENTRY BLOCKED: {symbol} futures LTP is unavailable.")
    lot_size = int(future.lot_size or 0)
    quantity = config.lots * lot_size
    if quantity <= 0:
        raise ValueError("PAPER ENTRY BLOCKED: lot size is unavailable.")
    evaluation = view.get("evaluation")
    version = latest_version(db)
    policy = exit_of(version.exit_policy) if version else None
    stop, target = initial_levels(float(price), evaluation.atr if evaluation else None, policy) if policy else (None, None)
    if stop is not None:
        risk = money((Decimal(str(price)) - Decimal(str(stop))) * quantity)
        if risk > config.max_loss_per_trade:
            raise ValueError(f"Auto entry blocked: per-trade risk {risk} exceeds {config.max_loss_per_trade}.")
    chosen = {"option_type": side, "strike": 0, "tradingsymbol": future.tradingsymbol}
    block = _entry_block(db, config, symbol, chosen)
    if block:
        raise ValueError(f"Auto entry blocked: {block}" if auto else block)
    session = now_ist().date()
    key = idempotency_key(session, symbol, f"INDEX-{side}", 0, future.expiry)
    if db.scalar(select(PaperTrade).where(PaperTrade.idempotency_key == key)):
        raise ValueError(f"A {symbol} {side} index paper fill already exists for this session.")
    buy = calculator.calculate_buy(price, quantity)
    sell_est = calculator.calculate_sell(price, quantity)
    estimated = calculator.combine(buy, sell_est)
    now = as_utc_naive(now_utc())
    enabled = total
    passed_n = passed
    if evaluation is not None and passed_n is None:
        enabled = max(evaluation.total_conditions - evaluation.disabled_conditions, 0)
        passed_n = evaluation.passed_conditions
        score = score_percent(passed_n, enabled) if score is None else score
    trade = PaperTrade(
        idempotency_key=key,
        signal_id=key,
        underlying=symbol,
        option_type=side,
        instrument_kind="INDEX",
        tradingsymbol=future.tradingsymbol or f"{symbol} FUT",
        exchange=_exchange(view, symbol),
        expiry=future.expiry,
        strike=Decimal("0"),
        instrument_token=int(future.instrument_token),
        quantity=quantity,
        lot_size=lot_size,
        lots=config.lots,
        entry_time=now,
        entry_price=Decimal(str(price)),
        entry_underlying_price=_dec_or_none(price),
        entry_option_ltp=_dec_or_none(price),
        entry_signal_score=Decimal(str(score or 0)),
        entry_passed=int(passed_n or 0),
        entry_total=int(enabled or 0),
        stop_loss=Decimal(str(stop)) if stop is not None else None,
        target=Decimal(str(target)) if target is not None else None,
        mfe_price=Decimal(str(price)),
        charges_estimated=True,
        status="OPEN",
    )
    _apply_charges(trade, estimated, money(0), estimated.total)
    db.add(trade)
    db.flush()
    rows = conditions if conditions is not None else (evaluation.conditions if evaluation is not None else [])
    for item in rows:
        name = getattr(item, "name", None)
        if name is None and isinstance(item, dict):
            name = item.get("name")
        status = getattr(item, "status", None)
        passed_flag = status == "PASS" if status is not None else bool(isinstance(item, dict) and item.get("passed"))
        current = getattr(item, "current", None)
        if current is None and isinstance(item, dict):
            current = item.get("current")
        required = getattr(item, "required", None)
        if required is None and isinstance(item, dict):
            required = item.get("required")
        db.add(
            PaperTradeCondition(
                trade_id=trade.id,
                name=str(name or ""),
                passed=bool(passed_flag),
                current_value=str(current or "")[:80],
                required_value=str(required or "")[:80],
            )
        )
    verb = "BUY" if side == "BUY" else "SELL"
    _event(db, trade.id, symbol, f"Index futures paper {verb}: {trade.tradingsymbol}")
    _event(
        db,
        trade.id,
        symbol,
        f"Paper {verb}: {quantity} qty @ ₹{money(price)} · {trade.entry_passed}/{trade.entry_total} · {trade.entry_signal_score}% · NO REAL ORDER",
    )
    return trade


def _consider_entry(runtime, db, config: PaperAutoConfig, calculator: ZerodhaChargeCalculator, view: dict) -> None:
    symbol = view.get("symbol") or ""
    if view.get("stale"):
        _event(db, None, symbol, "PAPER ENTRY BLOCKED: live quotes are stale.")
        return
    packs = view.get("candidates") or {}
    live = view.get("displayed_direction")
    if not live:
        evaluation = view.get("evaluation")
        live = implied_market_direction(
            close=evaluation.close if evaluation else None,
            ema=evaluation.ema if evaluation else None,
            vwap=evaluation.vwap if evaluation else None,
            momentum=evaluation.momentum if evaluation else None,
        )
    if live not in ("BULLISH", "BEARISH"):
        return
    ce, pe = gated_sides(packs.get("ce"), packs.get("pe"), config.ce_enabled, config.pe_enabled)
    ce_ok = complete_setup(ce) and aligned_with_tape("CE", live)
    pe_ok = complete_setup(pe) and aligned_with_tape("PE", live)
    if ce and ce_ok:
        _event(db, None, symbol, f"{symbol} CE setup is complete at {score_percent(int(ce['passed_count']), int(ce['total_count']))}%")
    if pe and pe_ok:
        _event(db, None, symbol, f"{symbol} PE setup is complete at {score_percent(int(pe['passed_count']), int(pe['total_count']))}%")
    chosen, why = choose_eligible_candidate(ce if ce_ok else None, pe if pe_ok else None, 0)
    if chosen is None:
        return
    if not aligned_with_tape(chosen.get("option_type"), live):
        _event(db, None, symbol, f"Auto entry blocked: {chosen.get('option_type')} does not match live {live} tape.")
        return
    block = _entry_block(db, config, symbol, chosen)
    if block:
        _event(db, None, symbol, f"Auto entry blocked: {block}")
        return
    token = chosen.get("token")
    if token is None:
        from app.db.models import Instrument

        row = db.scalar(select(Instrument).where(Instrument.tradingsymbol == chosen.get("tradingsymbol")))
        token = int(row.instrument_token) if row else None
    if token is None:
        _event(db, None, symbol, "PAPER ENTRY BLOCKED: selected contract token is unavailable.")
        return
    quote = runtime.cache.get(int(token)) or {}
    price = pick_entry_price(quote, config.entry_price_source)
    if price is None:
        _event(db, None, symbol, "PAPER ENTRY BLOCKED: Option price data unavailable/stale.")
        return
    lot_size = _lot_size(db, int(token), chosen)
    quantity = config.lots * lot_size
    if quantity <= 0:
        _event(db, None, symbol, "PAPER ENTRY BLOCKED: lot size is unavailable.")
        return
    version = latest_version(db)
    policy = exit_of(version.exit_policy) if version else None
    stop, target = initial_levels(float(price), None, policy) if policy else (None, None)
    if stop is not None:
        risk = money((Decimal(str(price)) - Decimal(str(stop))) * quantity)
        if risk > config.max_loss_per_trade:
            _event(db, None, symbol, f"Auto entry blocked: per-trade risk {risk} exceeds {config.max_loss_per_trade}.")
            return
    session = now_ist().date()
    expiry = chosen.get("expiry")
    key = idempotency_key(session, symbol, chosen["option_type"], chosen.get("strike"), expiry)
    if db.scalar(select(PaperTrade).where(PaperTrade.idempotency_key == key)):
        return
    buy = calculator.calculate_buy(price, quantity)
    sell_est = calculator.calculate_sell(price, quantity)
    estimated = calculator.combine(buy, sell_est)
    now = as_utc_naive(now_utc())
    trade = PaperTrade(
        idempotency_key=key,
        signal_id=key,
        underlying=symbol,
        option_type=chosen["option_type"],
        tradingsymbol=chosen.get("tradingsymbol") or f"{symbol} {chosen.get('strike')} {chosen['option_type']}",
        exchange=_exchange(view, symbol),
        expiry=_expiry(expiry),
        strike=Decimal(str(chosen.get("strike") or 0)),
        instrument_token=int(token),
        quantity=quantity,
        lot_size=lot_size,
        lots=config.lots,
        entry_time=now,
        entry_price=Decimal(str(price)),
        entry_underlying_price=_dec_or_none(view.get("futures_ltp") or view.get("ltp")),
        entry_option_ltp=_dec_or_none(quote.get("ltp")),
        entry_signal_score=Decimal(str(chosen.get("score_percent") or 0)),
        entry_passed=int(chosen.get("passed_count") or 0),
        entry_total=int(chosen.get("total_count") or 0),
        stop_loss=Decimal(str(stop)) if stop is not None else None,
        target=Decimal(str(target)) if target is not None else None,
        mfe_price=Decimal(str(price)),
        charges_estimated=True,
        status="OPEN",
    )
    _apply_charges(trade, estimated, money(0), estimated.total)
    db.add(trade)
    db.flush()
    for item in chosen.get("conditions") or []:
        db.add(
            PaperTradeCondition(
                trade_id=trade.id,
                name=str(item.get("name") or ""),
                passed=bool(item.get("passed")),
                current_value=str(item.get("current") or "")[:80],
                required_value=str(item.get("required") or "")[:80],
            )
        )
    _event(db, trade.id, symbol, f"Candidate selected: {trade.tradingsymbol}")
    _event(
        db,
        trade.id,
        symbol,
        f"Paper BUY: {quantity} qty @ ₹{money(price)} · {trade.entry_passed}/{trade.entry_total} · {trade.entry_signal_score}%",
    )


def _paper_result(db, trade: PaperTrade, premium, calculator: ZerodhaChargeCalculator) -> tuple[Decimal, ChargeBreakdown]:
    if _paper_kind(trade) == "INDEX":
        from app.domain.index_strategy import IndexStrategyConfig, index_paper_net

        points = IndexStrategyConfig().slippage_points
        if db is not None:
            points = float((load_config(db).index_strategy or {}).get("slippage_points") or points)
        gross, slip, _net = index_paper_net(
            trade.option_type, trade.entry_price, premium, trade.quantity, slippage_points=points
        )
        zero = money(0)
        return gross, ChargeBreakdown(zero, zero, zero, zero, zero, zero, slip)
    buy = calculator.calculate_buy(trade.entry_price, trade.quantity)
    sell = calculator.calculate_sell(premium, trade.quantity)
    charges = calculator.combine(buy, sell)
    gross = signed_gross(premium, trade.entry_price, trade.quantity, short=_index_short(trade))
    return gross, charges


def _close(db, trade: PaperTrade, premium, underlying_price, reason: str, calculator: ZerodhaChargeCalculator) -> None:
    gross, charges = _paper_result(db, trade, premium, calculator)
    trade.exit_time = as_utc_naive(now_utc())
    trade.exit_price = Decimal(str(premium))
    trade.exit_underlying_price = _dec_or_none(underlying_price)
    trade.exit_reason = reason
    trade.charges_estimated = False
    trade.status = "CLOSED"
    _apply_charges(trade, charges, gross, charges.total)
    _event(db, trade.id, trade.underlying, f"{reason} reached")
    close_side = "BUY" if _index_short(trade) else "SELL"
    _event(db, trade.id, trade.underlying, f"Paper {close_side}: {trade.quantity} qty @ ₹{money(premium)}")
    _event(db, trade.id, trade.underlying, f"Gross P&L: ₹{trade.gross_pnl}")
    _event(db, trade.id, trade.underlying, f"Charges: ₹{trade.total_charges}")
    _event(db, trade.id, trade.underlying, f"NET P&L: ₹{trade.net_pnl}")


def _mark(trade: PaperTrade, premium, calculator: ZerodhaChargeCalculator) -> None:
    gross, charges = _paper_result(None, trade, premium, calculator)
    trade.charges_estimated = True
    _apply_charges(trade, charges, gross, charges.total)


def _apply_charges(trade: PaperTrade, charges: ChargeBreakdown, gross: Decimal, total: Decimal) -> None:
    trade.gross_pnl = money(gross)
    trade.brokerage = charges.brokerage
    trade.stt = charges.stt
    trade.transaction_charges = charges.transaction
    trade.sebi_charges = charges.sebi
    trade.gst = charges.gst
    trade.stamp_duty = charges.stamp
    trade.other_charges = charges.other
    trade.total_charges = money(total)
    trade.net_pnl = net_pnl(trade.gross_pnl, trade.total_charges)


def _entry_block(db, config: PaperAutoConfig, underlying: str, chosen: dict) -> str | None:
    open_rows = db.scalars(select(PaperTrade).where(PaperTrade.status == "OPEN")).all()
    if len(open_rows) >= config.max_open_positions:
        first = open_rows[0]
        return f"Maximum open paper positions reached ({first.underlying} {first.strike} {first.option_type})."
    if any(row.underlying == underlying for row in open_rows):
        return f"An open paper position already exists on {underlying}."
    today = now_ist().date()
    todays = [row for row in db.scalars(select(PaperTrade)).all() if _entry_date(row) == today]
    if len(todays) >= config.max_trades_per_day:
        return "Maximum paper trades for today reached."
    session_net = _session_net(db)
    if session_net <= -config.max_daily_loss:
        return f"Daily loss limit reached. Net P&L: ₹{session_net}. Daily limit: ₹{-config.max_daily_loss}."
    from app.domain.index_strategy import config_from_dict

    idx_cfg = config_from_dict(config.index_strategy)
    if idx_cfg.no_reentry_after_stop:
        today = now_ist().date()
        for row in db.scalars(select(PaperTrade).where(PaperTrade.status == "CLOSED")).all():
            if _entry_date(row) != today:
                continue
            if str(row.exit_reason or "").upper() in {"STOP_LOSS_HIT", "STOP_LOSS"}:
                return "No re-entry after a stop today."
    if not config.allow_reentry:
        same = [row for row in todays if row.underlying == underlying]
        if same:
            return f"Re-entry is disabled for {underlying} today."
    last_close = None
    for row in db.scalars(select(PaperTrade).where(PaperTrade.status == "CLOSED")).all():
        if row.underlying == underlying and row.exit_time:
            if last_close is None or row.exit_time > last_close:
                last_close = row.exit_time
    if last_close is not None:
        closed = last_close.replace(tzinfo=last_close.tzinfo)
        if closed.tzinfo is None:
            closed = closed.replace(tzinfo=timezone.utc)
        if now_utc() - closed < timedelta(minutes=config.cooldown_minutes):
            return f"Re-entry cooldown ({config.cooldown_minutes} minutes) is still active on {underlying}."
    return None


def _held_long_enough(trade: PaperTrade, minutes: int) -> bool:
    if minutes <= 0:
        return True
    entry = trade.entry_time
    if entry.tzinfo is None:
        entry = entry.replace(tzinfo=timezone.utc)
    return now_utc() - entry >= timedelta(minutes=minutes)


def _halted(db, config: PaperAutoConfig) -> bool:
    return _session_net(db) <= -config.max_daily_loss


def _session_net(db) -> Decimal:
    today = now_ist().date()
    cutoff = as_utc_naive(now_utc() - timedelta(hours=36))
    total = Decimal("0")
    for row in db.scalars(select(PaperTrade).where(PaperTrade.entry_time >= cutoff)).all():
        if _entry_date(row) == today:
            total += Decimal(str(row.net_pnl or 0))
    return money(total)


def _entry_date(row: PaperTrade):
    moment = row.entry_time
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return to_ist(moment).date()


def _event(db, trade_id, underlying: str, message: str) -> None:
    db.add(
        PaperTradeEvent(
            trade_id=trade_id,
            created_at=as_utc_naive(now_utc()),
            message=message[:400],
            underlying=underlying or "",
        )
    )


def _lot_size(db, token: int, chosen: dict) -> int:
    from app.db.models import Instrument

    row = db.scalar(select(Instrument).where(Instrument.instrument_token == token))
    if row and row.lot_size:
        return int(row.lot_size)
    return int(chosen.get("lot_size") or 0)


def _exchange(view: dict, symbol: str) -> str:
    profile = view.get("profile")
    if profile is not None:
        return profile.derivatives_exchange
    return "NFO" if symbol == "NIFTY" else "BFO"


def _expiry(value):
    if value is None or value == "":
        return None
    if hasattr(value, "year"):
        return value
    return date.fromisoformat(str(value)[:10])


def _dec_or_none(value):
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _paper_instrument_of(row) -> str:
    value = str(getattr(row, "paper_instrument", None) or "OPTIONS").upper()
    return value if value in {"OPTIONS", "INDEX"} else "OPTIONS"


def _index_strategy_dict(row) -> dict:
    raw = getattr(row, "index_strategy_json", None)
    if not raw:
        from app.domain.index_strategy import IndexStrategyConfig

        return IndexStrategyConfig().as_dict()
    try:
        from app.domain.index_strategy import config_from_dict

        return config_from_dict(json.loads(raw)).as_dict()
    except (json.JSONDecodeError, TypeError, ValueError):
        from app.domain.index_strategy import IndexStrategyConfig

        return IndexStrategyConfig().as_dict()


def _index_presets() -> dict:
    from app.domain.index_strategy import PRESETS

    return {key: value.as_dict() for key, value in PRESETS.items()}


def _paper_kind(trade: PaperTrade) -> str:
    kind = str(getattr(trade, "instrument_kind", None) or "").upper()
    if kind in {"INDEX", "OPTION"}:
        return kind
    if trade.option_type in {"BUY", "SELL"}:
        return "INDEX"
    return "OPTION"


def _index_short(trade: PaperTrade) -> bool:
    return _paper_kind(trade) == "INDEX" and trade.option_type == "SELL"


def _exit_mark(entry: float, last: float, short: bool) -> float:
    if short:
        return 2 * entry - last
    return last


def index_quotes(db: Session, runtime) -> dict:
    from app.adapters.kite.instruments import front_future

    out = {}
    for symbol in ("NIFTY", "SENSEX"):
        future = front_future(db, symbol)
        if future is None:
            out[symbol] = {
                "available": False,
                "ltp": None,
                "tradingsymbol": "",
                "lot_size": None,
                "expiry": None,
                "reason": f"Front-month {symbol} future is not in the instrument dump.",
            }
            continue
        quote = runtime.cache.get(int(future.instrument_token)) if runtime else {}
        ltp = (quote or {}).get("ltp")
        out[symbol] = {
            "available": ltp is not None,
            "ltp": float(ltp) if ltp is not None else None,
            "tradingsymbol": future.tradingsymbol,
            "lot_size": int(future.lot_size or 0),
            "expiry": future.expiry.isoformat() if future.expiry else None,
            "reason": None if ltp is not None else f"{symbol} futures LTP is unavailable.",
        }
    return out


def live_index_decisions(db: Session, runtime, config: PaperAutoConfig) -> dict:
    from app.adapters.kite.instruments import front_future
    from app.domain.index_strategy import config_from_dict, decide, features_from_bars

    cfg = config_from_dict(config.index_strategy)
    out = {}
    for symbol in ("NIFTY", "SENSEX"):
        future = front_future(db, symbol)
        if future is None:
            out[symbol] = {"decision": "HOLD", "reason": "Front-month future missing.", "buy_score": 0, "sell_score": 0, "timeframe": f"{INDEX_PAPER_MINUTES}m"}
            continue
        quote = runtime.cache.get(int(future.instrument_token)) if runtime else {}
        if not (quote or {}).get("ltp"):
            out[symbol] = {"decision": "HOLD", "reason": "Live futures LTP is unavailable.", "buy_score": 0, "sell_score": 0, "stale": True, "timeframe": f"{INDEX_PAPER_MINUTES}m"}
            continue
        bars = load_index_tf_bars(db, int(future.instrument_token), INDEX_PAPER_MINUTES)
        if len(bars) < max(cfg.ema_period, 5):
            out[symbol] = {"decision": "HOLD", "reason": f"Not enough {INDEX_PAPER_MINUTES}m candles for indicators.", "buy_score": 0, "sell_score": 0, "timeframe": f"{INDEX_PAPER_MINUTES}m"}
            continue
        facts = features_from_bars(bars, cfg)
        tape = implied_market_direction(
            close=facts.get("close"),
            ema=facts.get("ema"),
            vwap=facts.get("vwap"),
            momentum=facts.get("momentum"),
        )
        verdict = decide(features_from_bars(bars, cfg, tape=tape), cfg)
        payload = verdict.as_dict()
        last = bars[-1]
        payload["stale"] = False
        payload["timeframe"] = f"{INDEX_PAPER_MINUTES}m"
        payload["walk_minutes"] = INDEX_PAPER_MINUTES
        payload["bar_unix"] = int(last.start.timestamp())
        payload["close"] = float(last.close)
        out[symbol] = payload
    return out


def dashboard_status(db: Session) -> dict:
    config = load_config(db)
    today = now_ist().date()
    cutoff = as_utc_naive(now_utc() - timedelta(hours=36))
    trades = [row for row in db.scalars(select(PaperTrade).where(PaperTrade.entry_time >= cutoff)).all() if _entry_date(row) == today]
    summary = daily_summary([_trade_dict(row, None) for row in trades])
    open_count = sum(1 for row in trades if row.status == "OPEN")
    halted = money(sum((Decimal(str(row.net_pnl or 0)) for row in trades), Decimal("0"))) <= -config.max_daily_loss
    return {
        "enabled": config.enabled,
        "threshold": float(config.min_signal_score),
        "open_positions": open_count,
        "max_open_positions": config.max_open_positions,
        "today_net_pnl": float(summary["net_pnl"]),
        "halted": halted,
        "note": "PAPER TRADING — NO REAL ORDERS",
        "ce_strategy_enabled": config.ce_enabled,
        "pe_strategy_enabled": config.pe_enabled,
        "paper_instrument": config.paper_instrument,
        "label": "ON" if config.enabled else "OFF",
        "message": (
            "PAPER TRADING HALTED. Daily loss limit reached."
            if halted
            else (
                "Signal monitoring active. No automatic paper entries."
                if not config.enabled
                else (
                    "Automatic index-futures paper: BUY on a bullish tape, SELL on a bearish tape. No broker orders."
                    if config.paper_instrument == "INDEX"
                    else "Automatic paper entries require a complete CE/PE setup aligned with the live tape."
                )
            )
        ),
    }


def created_buys(db: Session, limit: int = 30) -> list[dict]:
    """Successfully created paper BUY rows for the toast. Read-only; does not trade."""
    today = now_ist().date()
    rows = db.scalars(select(PaperTrade).order_by(PaperTrade.id.desc()).limit(limit)).all()
    out = []
    for row in rows:
        if _entry_date(row) != today:
            continue
        out.append(
            {
                "trade_id": row.id,
                "underlying": row.underlying,
                "option_type": row.option_type,
                "instrument_kind": _paper_kind(row),
                "strike": float(row.strike),
                "quantity": row.quantity,
                "entry_price": float(row.entry_option_ltp if row.entry_option_ltp is not None else row.entry_price),
                "tradingsymbol": row.tradingsymbol,
                "expiry": jsonable(row.expiry),
                "timestamp": jsonable(row.entry_time),
            }
        )
    out.reverse()
    return out


def workspace(db: Session, runtime) -> dict:
    config = load_config(db)
    calculator = load_calculator(db)
    today = now_ist().date()
    cutoff = as_utc_naive(now_utc() - timedelta(hours=36))
    rows = list(
        db.scalars(select(PaperTrade).where(PaperTrade.entry_time >= cutoff).order_by(PaperTrade.entry_time.desc())).all()
    )
    todays = [row for row in rows if _entry_date(row) == today]
    payload = []
    for row in todays:
        quote = runtime.cache.get(int(row.instrument_token)) if runtime else {}
        ltp = (quote or {}).get("ltp")
        if row.status == "OPEN" and ltp is not None:
            _mark(row, ltp, calculator)
        payload.append(_trade_dict(row, ltp))
    events = [
        {"id": item.id, "time": jsonable(item.created_at), "message": item.message, "underlying": item.underlying, "trade_id": item.trade_id}
        for item in db.scalars(select(PaperTradeEvent).order_by(PaperTradeEvent.created_at.desc()).limit(80)).all()
    ]
    summary = daily_summary(payload)
    curve = _curve(payload)
    net = money(sum((Decimal(str(row.get("net_pnl") or 0)) for row in payload), Decimal("0")))
    halted = net <= -config.max_daily_loss
    status = {
        "enabled": config.enabled,
        "threshold": float(config.min_signal_score),
        "open_positions": sum(1 for row in payload if row.get("status") == "OPEN"),
        "max_open_positions": config.max_open_positions,
        "today_net_pnl": float(summary["net_pnl"]),
        "halted": halted,
        "note": "PAPER TRADING — NO REAL ORDERS",
        "ce_strategy_enabled": config.ce_enabled,
        "pe_strategy_enabled": config.pe_enabled,
        "paper_instrument": config.paper_instrument,
        "label": "ON" if config.enabled else "OFF",
        "message": (
            "PAPER TRADING HALTED. Daily loss limit reached."
            if halted
            else (
                "Automatic paper is paused."
                if config.auto_paused
                else (
                    "Signal monitoring active. No automatic paper entries."
                    if not config.enabled or config.paper_mode != "AUTOMATIC"
                    else "Automatic index-futures paper is running. No broker orders."
                )
            )
        ),
    }
    coverage = []
    try:
        import time as clock_mod
        from app.services.historical_repo import stored_coverage

        now = clock_mod.monotonic()
        if _coverage_memo["rows"] is not None and now - _coverage_memo["at"] < 60:
            coverage = _coverage_memo["rows"]
        else:
            coverage = stored_coverage(db)
            _coverage_memo["at"] = now
            _coverage_memo["rows"] = coverage
    except Exception:
        log.exception("historical coverage lookup failed")
    decisions = {}
    try:
        decisions = live_index_decisions(db, runtime, config)
    except Exception:
        log.exception("live index scores failed")

    return {
        "note": "PAPER TRADING — NO REAL ORDERS",
        "config": _config_dict(config),
        "status": status,
        "summary": {key: jsonable(value) for key, value in summary.items()},
        "trades": payload,
        "events": events,
        "curve": curve,
        "halted": halted,
        "index_quotes": index_quotes(db, runtime),
        "index_strategy": config.index_strategy,
        "index_presets": _index_presets(),
        "live_decisions": decisions,
        "note_hypo": "HYPOTHETICAL / PAPER signals only. No broker orders.",
        "historical": {
            "coverage": coverage,
            "note": "Choose a stored historical date range to replay paper trades. Live auto-paper is unchanged.",
        },
    }


def trade_detail(db: Session, trade_id: int, runtime) -> dict | None:
    row = db.get(PaperTrade, trade_id)
    if row is None:
        return None
    calculator = load_calculator(db)
    quote = runtime.cache.get(int(row.instrument_token)) if runtime else {}
    ltp = (quote or {}).get("ltp")
    if row.status == "OPEN" and ltp is not None:
        _mark(row, ltp, calculator)
    return {
        **_trade_dict(row, ltp),
        "conditions": [
            {"name": item.name, "passed": item.passed, "current": item.current_value, "required": item.required_value}
            for item in row.conditions
        ],
        "events": [
            {"time": jsonable(item.created_at), "message": item.message}
            for item in sorted(row.events, key=lambda item: item.created_at)
        ],
        "charges": {
            "brokerage": float(row.brokerage),
            "stt": float(row.stt),
            "transaction": float(row.transaction_charges),
            "sebi": float(row.sebi_charges),
            "gst": float(row.gst),
            "stamp": float(row.stamp_duty),
            "other": float(row.other_charges),
            "total": float(row.total_charges),
            "estimated": row.charges_estimated,
        },
    }


def save_config(db: Session, data: dict) -> PaperAutoConfig:
    row = db.get(PaperAutoSetting, 1)
    if row is None:
        row = PaperAutoSetting(id=1)
        db.add(row)
    if "enabled" in data:
        row.enabled = bool(data["enabled"])
    for name in (
        "min_signal_score",
        "candidate_strike_range",
        "max_open_positions",
        "max_trades_per_day",
        "max_daily_loss",
        "max_loss_per_trade",
        "cooldown_minutes",
        "lots",
    ):
        if data.get(name) is not None:
            setattr(row, name, data[name])
    if data.get("square_off"):
        value = data["square_off"]
        if isinstance(value, str):
            hour, minute = value.split(":")[:2]
            from datetime import time as clock

            row.square_off = clock(int(hour), int(minute))
        else:
            row.square_off = value
    if "allow_reentry" in data:
        row.allow_reentry = bool(data["allow_reentry"])
    if "ce_strategy_enabled" in data:
        row.ce_strategy_enabled = bool(data["ce_strategy_enabled"])
    if "pe_strategy_enabled" in data:
        row.pe_strategy_enabled = bool(data["pe_strategy_enabled"])
    if data.get("paper_instrument"):
        instrument = str(data["paper_instrument"]).upper()
        if instrument not in {"OPTIONS", "INDEX"}:
            raise ValueError("paper_instrument must be OPTIONS or INDEX")
        row.paper_instrument = instrument
    if data.get("paper_mode"):
        mode = str(data["paper_mode"]).upper().replace(" ", "_")
        allowed = {"MANUAL", "AUTOMATIC", "HISTORICAL_BACKTEST", "HISTORICAL_REPLAY", "AUTO"}
        if mode not in allowed:
            raise ValueError("paper_mode is not a supported paper-only mode")
        row.paper_mode = "AUTOMATIC" if mode == "AUTO" else mode
    if "auto_paused" in data:
        row.auto_paused = bool(data["auto_paused"])
    if "index_strategy" in data and data["index_strategy"] is not None:
        from app.domain.index_strategy import config_from_dict

        row.index_strategy_json = json.dumps(config_from_dict(data["index_strategy"] if isinstance(data["index_strategy"], dict) else {}).as_dict())
        idx = config_from_dict(json.loads(row.index_strategy_json))
        row.cooldown_minutes = int(idx.cooldown_minutes or 0)
        row.max_trades_per_day = int(idx.max_trades_per_day or 0)
        row.min_signal_score = idx.buy_threshold
        if idx.max_daily_loss is not None:
            row.max_daily_loss = idx.max_daily_loss
    if data.get("entry_price_source"):
        source = str(data["entry_price_source"]).upper()
        if source not in {"LTP", "ASK", "MID"}:
            raise ValueError("entry_price_source must be LTP, ASK or MID")
        row.entry_price_source = source
    db.flush()
    return load_config(db)


def _trade_dict(row: PaperTrade, ltp) -> dict:
    return {
        "id": row.id,
        "underlying": row.underlying,
        "option_type": row.option_type,
        "instrument_kind": _paper_kind(row),
        "tradingsymbol": row.tradingsymbol,
        "exchange": row.exchange,
        "expiry": jsonable(row.expiry),
        "strike": float(row.strike),
        "quantity": row.quantity,
        "lot_size": row.lot_size,
        "lots": row.lots,
        "entry_time": jsonable(row.entry_time),
        "entry_price": float(row.entry_price),
        "current": float(ltp) if ltp is not None else (float(row.exit_price) if row.exit_price is not None else None),
        "exit_time": jsonable(row.exit_time),
        "exit_price": float(row.exit_price) if row.exit_price is not None else None,
        "exit_reason": row.exit_reason,
        "signal_percent": float(row.entry_signal_score),
        "passed": row.entry_passed,
        "total": row.entry_total,
        "gross_pnl": float(row.gross_pnl),
        "total_charges": float(row.total_charges),
        "net_pnl": float(row.net_pnl),
        "charges_estimated": row.charges_estimated,
        "status": row.status,
    }


def _config_dict(config: PaperAutoConfig) -> dict:
    from app.domain.index_strategy import ENTRY_VALIDATION_RULES

    return {
        "enabled": config.enabled,
        "min_signal_score": float(config.min_signal_score),
        "candidate_strike_range": config.candidate_strike_range,
        "max_open_positions": config.max_open_positions,
        "max_trades_per_day": config.max_trades_per_day,
        "max_daily_loss": float(config.max_daily_loss),
        "max_loss_per_trade": float(config.max_loss_per_trade),
        "square_off": config.square_off.strftime("%H:%M"),
        "allow_reentry": config.allow_reentry,
        "cooldown_minutes": config.cooldown_minutes,
        "entry_price_source": config.entry_price_source,
        "lots": config.lots,
        "min_hold_minutes": config.min_hold_minutes,
        "ce_strategy_enabled": config.ce_enabled,
        "pe_strategy_enabled": config.pe_enabled,
        "paper_instrument": config.paper_instrument,
        "paper_mode": config.paper_mode,
        "auto_paused": config.auto_paused,
        "index_strategy": config.index_strategy,
        "entry_validation": list(ENTRY_VALIDATION_RULES),
        "paper_timeframe": f"{INDEX_PAPER_MINUTES}m",
    }


def _curve(trades: list[dict]) -> list[dict]:
    closed = sorted(
        [row for row in trades if row.get("status") == "CLOSED" and row.get("exit_time")],
        key=lambda row: row["exit_time"],
    )
    running = 0.0
    points = []
    for row in closed:
        running += float(row.get("net_pnl") or 0)
        points.append({"time": row["exit_time"], "net_pnl": round(running, 2)})
    return points

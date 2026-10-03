"""Historical paper replay. Reuses live CE/PE evaluators, exits and charges. No broker orders."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import IST, as_utc_naive, now_utc, to_ist
from app.db.bootstrap import latest_version, params_for
from app.db.models import (
    ChargeRateRow,
    HistoricalConditionStat,
    HistoricalPaperRun,
    HistoricalPaperTrade,
    UnderlyingProfile,
)
from app.domain.charges import ChargeRate, ZerodhaChargeCalculator, default_calculator, money
from app.domain.dashboard_brief import implied_market_direction, score_percent
from app.domain.exits import ExitContext, PositionState, advance_trail, choose_exit, initial_levels, trail_is_armed
from app.domain.historical import in_session, normalize_timeframe, parse_instruments, timeframe_minutes
from app.domain.index_strategy import (
    SAME_CANDLE_RULE,
    apply_slippage,
    config_from_dict,
    confirm_with_higher_tf,
    decide,
    features_from_bars,
    higher_tf_decision,
    htf_trend_side,
    index_exit_fill,
    index_hold_exit,
    index_paper_net,
    is_index_expiry,
    levels_for,
    map_exit_reason,
    parse_clock,
    performance_from_trades,
    validate_index_entry,
)
from app.domain.index_exits import (
    IndexExitMarket,
    IndexExitState,
    PARTIAL_1R,
    evaluate_index_exit,
    signed_r,
)
from app.domain.candles import resample_bars
from app.domain.option_buy import (
    PASS,
    UNAVAILABLE,
    OptionPremiumState,
    available_score_percent,
    evaluate_ce_buy_strategy,
    evaluate_pe_buy_strategy,
    futures_state,
)
from app.domain.paper_auto import aligned_with_tape, complete_setup, gross_pnl, net_pnl
from app.domain.strategy import evaluate_snapshot, snapshot_from_bars
from app.services.historical_import import INDEX_FUTURES_NOTE, OPTION_NOTE
from app.services.historical_repo import bars_from_rows, get_historical_candles, option_rows_exist, quality_for, stored_range
from app.services.mapping import exit_of, jsonable, option_buy_config_of, params_of

log = logging.getLogger("hextrade.historical.paper")

THRESHOLDS = (60, 65, 70, 75, 80, 85, 90)


def calculator_on(db: Session, as_of: date) -> ZerodhaChargeCalculator:
    rows = db.scalars(select(ChargeRateRow).where(ChargeRateRow.segment == "NFO")).all()
    active = []
    for row in rows:
        if not row.is_active:
            continue
        if row.effective_from and row.effective_from > as_of:
            continue
        if row.effective_to and row.effective_to < as_of:
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


def stored_walk_cover(db: Session, symbol: str, timeframe: str):
    """3m/5m/15m paper walks stored 1-minute archive when higher-TF rows are not imported."""
    tf = normalize_timeframe(timeframe)
    cover = stored_range(db, symbol, tf)
    if cover is None and timeframe_minutes(tf) > 1:
        cover = stored_range(db, symbol, "1Minute")
    return cover


def queue_run(
    db: Session,
    *,
    instrument: str,
    date_from: date,
    date_to: date,
    threshold: float = 75,
    initial_capital: float = 100000,
    strike_range: int = 5,
    timeframe: str = "1Minute",
    strategy: dict | None = None,
    sweep: dict | None = None,
) -> HistoricalPaperRun:
    symbols = parse_instruments(instrument)
    if not symbols:
        raise ValueError("Instrument must be NIFTY, SENSEX or BOTH")
    if date_to < date_from:
        raise ValueError("To date is before from date")
    tf = normalize_timeframe(timeframe)
    for symbol in symbols:
        cover = stored_walk_cover(db, symbol, tf)
        if cover is None:
            raise ValueError(f"No historical {tf} candles stored for {symbol}. Import 1-minute historical data first.")
        stored_from, stored_to = cover
        if date_to < stored_from or date_from > stored_to:
            raise ValueError(
                f"Chosen range {date_from} → {date_to} is outside stored {symbol} data ({stored_from} → {stored_to})."
            )
    cover = stored_walk_cover(db, symbols[0], tf)
    if cover is None:
        raise ValueError(f"No historical candles stored for {symbols[0]}. Import 1-minute historical data first.")
    stored_from, stored_to = cover
    date_from = max(date_from, stored_from)
    date_to = min(date_to, stored_to)
    cfg = config_from_dict(strategy)
    version = latest_version(db)
    params_row = params_for(version, symbols[0]) if version else None
    label = cfg.name or (f"Strategy v{version.version_number}" if version else "Current Strategy")
    now = as_utc_naive(now_utc())
    snapshot = _strategy_snapshot(version, params_row, symbols[0])
    try:
        payload = json.loads(snapshot)
    except json.JSONDecodeError:
        payload = {"raw": snapshot}
    payload["index_strategy"] = cfg.as_dict()
    if sweep:
        payload["sweep"] = sweep
        label = "Score/exit sweep"
    run = HistoricalPaperRun(
        created_at=now,
        updated_at=now,
        status="QUEUED",
        instrument="BOTH" if len(symbols) > 1 else symbols[0],
        timeframe=tf,
        date_from=date_from,
        date_to=date_to,
        strategy_version_id=version.id if version else None,
        strategy_label=label,
        strategy_snapshot=json.dumps(payload),
        threshold=Decimal(str(cfg.buy_threshold if threshold is None else threshold)),
        initial_capital=Decimal(str(initial_capital)),
        strike_range=int(strike_range),
        charge_note="INDEX FUTURES PROXY uses index-point P&L. Option NFO charges are not applied to index notionals.",
        option_data_note=OPTION_NOTE,
        same_candle_rule=SAME_CANDLE_RULE,
    )
    db.add(run)
    db.flush()
    return run


def run_queued_paper(db: Session) -> None:
    run = db.scalar(select(HistoricalPaperRun).where(HistoricalPaperRun.status == "QUEUED").order_by(HistoricalPaperRun.id.asc()))
    if run is None:
        return
    try:
        execute_run(db, run)
    except Exception as exc:
        log.exception("historical paper run %s failed", run.id)
        run.status = "FAILED"
        run.last_error = f"{type(exc).__name__}: {exc}"[:400]
        run.updated_at = as_utc_naive(now_utc())
        db.commit()


def _index_cfg(run: HistoricalPaperRun):
    try:
        payload = json.loads(run.strategy_snapshot or "{}")
    except json.JSONDecodeError:
        payload = {}
    return config_from_dict(payload.get("index_strategy") if isinstance(payload, dict) else {})


def _walk_instrument(db, run, symbol, version, setting, cfg, stats, threshold_hits):
    from app.adapters.kite.instruments import front_future

    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    params_row = params_for(version, symbol) if version else None
    if params_row is None or profile is None:
        return {"trades": [], "option_available": False, "warnings": [], "equity": Decimal("0"), "peak": Decimal("0"), "max_dd": Decimal("0"), "samples": [], "proxy_used": True, "error": f"Strategy, profile or parameters are missing for {symbol}"}
    ce_on = bool(cfg.buy_enabled if cfg else True) if setting is None else bool(getattr(setting, "ce_strategy_enabled", True) and cfg.buy_enabled)
    pe_on = bool(cfg.sell_enabled if cfg else True) if setting is None else bool(getattr(setting, "pe_strategy_enabled", True) and cfg.sell_enabled)
    future = front_future(db, symbol)
    lot_size = int(future.lot_size) if future and future.lot_size else 1
    lots = max(1, int(cfg.lots or 1))
    qty_per_trade = lot_size * lots
    cooldown_minutes = int(cfg.cooldown_minutes)
    max_per_day = int(cfg.max_trades_per_day)
    params = params_of(params_row, lot_size)
    option_cfg = replace(option_buy_config_of(params_row, profile), candidate_strike_range=int(run.strike_range))
    policy = exit_of(version.exit_policy)
    policy.trail_enabled = bool(cfg.trail_enabled)
    policy.max_hold_enabled = int(cfg.max_hold_minutes or 0) > 0
    policy.max_hold_minutes = int(cfg.max_hold_minutes or 0)
    session_start = parse_clock(cfg.session_start, option_cfg.time_start)
    session_end = parse_clock(cfg.session_end, option_cfg.time_end)
    square = parse_clock(cfg.square_off, policy.time_exit)
    expiry_square = parse_clock(cfg.expiry_square_off, time(14, 30))
    policy.time_exit = square
    warmup_from = run.date_from - timedelta(days=10)
    walk_minutes = timeframe_minutes(normalize_timeframe(run.timeframe or "1Minute"))
    warnings = []
    rows = get_historical_candles(db, symbol, run.timeframe, warmup_from, run.date_to)
    bars = bars_from_rows(rows)
    if walk_minutes > 1:
        minute_rows = get_historical_candles(db, symbol, "1Minute", warmup_from, run.date_to)
        resampled = resample_bars(bars_from_rows(minute_rows), walk_minutes)
        if resampled:
            bars = resampled
            warnings.append(f"{walk_minutes}m index bars built from stored 1-minute candles.")
    quality = quality_for(db, symbol, "1Minute" if walk_minutes > 1 else run.timeframe, profile)
    option_available = option_rows_exist(db, symbol, "1Minute", run.date_from, run.date_to)
    if quality.get("incomplete"):
        warnings.append(f"{symbol} historical data is incomplete.")
    if not option_available:
        warnings.append(f"{symbol}: option history unavailable. Paper BUY/SELL uses the index close.")
    if not bars:
        return {"trades": [], "option_available": option_available, "warnings": warnings, "equity": Decimal("0"), "peak": Decimal("0"), "max_dd": Decimal("0"), "samples": [], "proxy_used": not option_available, "error": f"No historical index candles for {symbol} in this range."}
    stamps = [bar.start for bar in bars]
    if len(stamps) != len(set(stamps)):
        warnings.append(f"{symbol} has duplicate timestamps.")
    trades = []
    samples = []
    open_trade = None
    peak_equity = Decimal("0")
    equity = Decimal("0")
    max_dd = Decimal("0")
    last_fill = None
    day_key = None
    day_trades = 0
    session_net = Decimal("0")
    stopped_today = False
    proxy_used = not option_available
    confirm_minutes = int(cfg.confirm_minutes or 5)
    confirm_15 = int(cfg.confirm_minutes_15 or 15)
    minute_rows = get_historical_candles(db, symbol, "1Minute", warmup_from, run.date_to)
    higher_bars = resample_bars(bars_from_rows(minute_rows), confirm_minutes)
    bars_15 = resample_bars(bars_from_rows(minute_rows), confirm_15)
    day_losses = 0
    day_r = 0.0
    last_stop_index = None

    for i, bar in enumerate(bars):
        visible = _no_lookahead_window(bars, i)
        snap = snapshot_from_bars(visible, params)
        evaluation = evaluate_snapshot(snap, params, float(profile.atr_threshold) if profile.atr_threshold is not None else None, live=False)
        clock = to_ist(bar.start)
        now = clock
        in_range = run.date_from <= clock.date() <= run.date_to
        if day_key != clock.date():
            day_key = clock.date()
            day_trades = 0
            session_net = Decimal("0")
            stopped_today = False
            day_losses = 0
            day_r = 0.0
        ce = pe = None
        if option_available:
            futures = futures_state(visible, option_cfg, symbol=symbol)
            option = OptionPremiumState(option_type="", data_quality="Historical option data unavailable")
            ce = evaluate_ce_buy_strategy(option, futures, option_cfg, now)
            pe = evaluate_pe_buy_strategy(option, futures, option_cfg, now)
        if in_range:
            _tally(stats["INDEX"], evaluation.conditions)
            if ce is not None:
                _tally(stats["CE"], ce.conditions)
            if pe is not None:
                _tally(stats["PE"], pe.conditions)
        implied = implied_market_direction(close=evaluation.close, ema=evaluation.ema, vwap=evaluation.vwap, momentum=evaluation.momentum)
        asof = bar.start + timedelta(minutes=walk_minutes)
        htf5 = higher_tf_decision(higher_bars, asof, cfg, confirm_minutes) if higher_bars else "HOLD"
        htf15 = higher_tf_decision(bars_15, asof, cfg, confirm_15) if bars_15 else "HOLD"
        expiry_day = is_index_expiry(symbol, clock.date())
        facts = features_from_bars(
            visible,
            cfg,
            tape=implied,
            extras={"htf_5_side": htf5, "htf_15_side": htf15, "is_expiry": expiry_day, "oi_change": evaluation.oi_change},
        )
        verdict = decide(facts, cfg)
        if in_range:
            _tally(stats["BUY"], verdict.buy.conditions)
            _tally(stats["SELL"], verdict.sell.conditions)
            best_score = max(verdict.buy.score, verdict.sell.score)
            for level in THRESHOLDS:
                if best_score >= level:
                    threshold_hits[level] += 1
        stamp = clock.time().replace(second=0, microsecond=0)
        hours_ok = in_session(clock, profile.session_start, profile.session_end) and session_start <= stamp <= session_end
        cooling = last_fill is not None and (clock - last_fill).total_seconds() < cooldown_minutes * 60
        halted = session_net <= -Decimal(str(cfg.max_daily_loss))
        confirm_side = htf5
        bars_since_stop = None if last_stop_index is None else i - last_stop_index
        gate_ok, gate_why = True, ""
        if verdict.decision in {"BUY", "SELL"}:
            gate_ok, gate_why = validate_index_entry(
                side=verdict.decision,
                clock=stamp,
                config=cfg,
                day_trades=day_trades,
                day_pnl=session_net,
                stopped_today=stopped_today,
                last_entry_at=last_fill,
                now=clock,
                higher_tf_side=confirm_side,
                htf_5_side=htf5,
                htf_15_side=htf15,
                walk_minutes=walk_minutes,
                score=verdict.buy.score if verdict.decision == "BUY" else verdict.sell.score,
                is_expiry=expiry_day,
                day_losses=day_losses,
                day_r=day_r,
                bars_since_stop=bars_since_stop,
            )
        if in_range and (i % 15 == 0 or verdict.decision != "HOLD"):
            samples.append({
                "t": clock.strftime("%Y-%m-%d %H:%M"),
                "buy": verdict.buy.score,
                "sell": verdict.sell.score,
                "decision": verdict.decision,
                "close": float(bar.close),
                "n": i + 1,
                "total": len(bars),
            })

        if open_trade is None and in_range and hours_ok and not cooling and day_trades < max_per_day and not halted and gate_ok:
            filled = False
            if option_available:
                chosen = None
                ce_score = score_percent(ce.passed_count, ce.total_count) if ce is not None else 0
                pe_score = score_percent(pe.passed_count, pe.total_count) if pe is not None else 0
                if ce is not None and ce_on and complete_setup({"is_strategy_valid": ce.passed}) and aligned_with_tape("CE", implied) and ce_score >= float(run.threshold) and ce.premium_ltp is not None:
                    chosen = ("CE", ce, ce_score)
                elif pe is not None and pe_on and complete_setup({"is_strategy_valid": pe.passed}) and aligned_with_tape("PE", implied) and pe_score >= float(run.threshold) and pe.premium_ltp is not None:
                    chosen = ("PE", pe, pe_score)
                if chosen:
                    kind, result, score = chosen
                    price = Decimal(str(result.premium_ltp))
                    stop, target = initial_levels(float(price), evaluation.atr, policy)
                    open_trade = {
                        "side": kind, "entry_time": bar.start, "entry_price": price, "quantity": qty_per_trade,
                        "score": score, "passed": result.passed_count, "total": result.total_count, "strike": result.strike,
                        "stop": stop, "target": target, "trail": None, "mfe": float(price), "calc": calculator_on(db, clock.date()),
                        "proxy": False, "entry_index": i, "buy_score": verdict.buy.score, "sell_score": verdict.sell.score,
                        "explanation": verdict.reason, "indicators": verdict.indicators, "symbol": symbol,
                    }
                    day_trades += 1
                    last_fill = clock
                    filled = True
            if not filled and verdict.decision in {"BUY", "SELL"} and ((verdict.decision == "BUY" and ce_on) or (verdict.decision == "SELL" and pe_on)):
                raw = apply_slippage(float(bar.close), verdict.decision, cfg.slippage_points)
                price = Decimal(str(raw))
                stop, target = levels_for(float(price), verdict.decision, cfg, facts.get("atr"))
                risk = abs(float(price) - float(stop)) if stop is not None else 0.0
                passed_n = sum(1 for item in (verdict.buy if verdict.decision == "BUY" else verdict.sell).conditions if item.passed)
                total_n = len((verdict.buy if verdict.decision == "BUY" else verdict.sell).conditions)
                brk = facts.get("prior_high") if verdict.decision == "BUY" else facts.get("prior_low")
                open_trade = {
                    "side": verdict.decision, "entry_time": bar.start, "entry_price": price, "quantity": qty_per_trade,
                    "score": verdict.buy.score if verdict.decision == "BUY" else verdict.sell.score,
                    "passed": passed_n, "total": total_n, "strike": None, "stop": stop, "target": target, "trail": None,
                    "mfe": float(price), "calc": calculator_on(db, clock.date()), "proxy": True, "entry_index": i,
                    "buy_score": verdict.buy.score, "sell_score": verdict.sell.score, "explanation": verdict.reason,
                    "indicators": verdict.indicators, "symbol": symbol, "risk": risk, "remaining": qty_per_trade,
                    "partial_taken": False, "breakout_level": brk, "atr": facts.get("atr"),
                }
                day_trades += 1
                last_fill = clock
                proxy_used = True

        if open_trade is not None and in_range:
            index_px = float(bar.close)
            last = i == len(bars) - 1 or to_ist(bar.start).date() != to_ist(bars[min(i + 1, len(bars) - 1)].start).date()
            end_data = i == len(bars) - 1
            reason = None
            if open_trade.get("proxy"):
                entry_at = open_trade["entry_time"]
                if getattr(entry_at, "tzinfo", None) is None:
                    entry_at = entry_at.replace(tzinfo=IST)
                held = (clock - to_ist(entry_at)).total_seconds() / 60
                peak = max(open_trade["mfe"], float(bar.high) if open_trade["side"] == "BUY" else float(bar.low))
                if open_trade["side"] != "BUY":
                    peak = min(open_trade["mfe"], float(bar.low))
                open_trade["mfe"] = peak
                opposite = verdict.sell.score if open_trade["side"] == "BUY" else verdict.buy.score
                reason, extras = evaluate_index_exit(
                    IndexExitState(
                        side=open_trade["side"],
                        entry=float(open_trade["entry_price"]),
                        stop=open_trade["stop"],
                        target=open_trade["target"],
                        trail=open_trade.get("trail"),
                        atr=open_trade.get("atr"),
                        risk=float(open_trade.get("risk") or 0),
                        bars_held=i - int(open_trade["entry_index"]),
                        remaining_qty=int(open_trade.get("remaining") or open_trade["quantity"]),
                        initial_qty=int(open_trade["quantity"]),
                        partial_taken=bool(open_trade.get("partial_taken")),
                        breakout_level=open_trade.get("breakout_level"),
                        mfe=float(open_trade["mfe"]),
                    ),
                    IndexExitMarket(
                        high=float(bar.high),
                        low=float(bar.low),
                        close=index_px,
                        ema20=facts.get("ema"),
                        vwap=facts.get("vwap"),
                        premium=None,
                        premium_entry=None,
                        clock=stamp,
                        square=square,
                        expiry_square=expiry_square,
                        is_expiry=is_index_expiry(symbol, clock.date()),
                        is_entry_bar=i == open_trade["entry_index"],
                        end_of_walk=end_data,
                        end_of_session=last and not end_data,
                        opposite_score=opposite,
                        opposite_threshold=float(cfg.sell_threshold if open_trade["side"] == "BUY" else cfg.buy_threshold),
                        htf_5_close_vs_ema=htf5,
                    ),
                    variant=cfg.exit_variant,
                    premium_stop_percent=cfg.premium_stop_percent,
                    partial_at_r=cfg.partial_at_r,
                    chandelier_mult=cfg.chandelier_atr_mult,
                    time_stop_bars=cfg.time_stop_bars,
                    time_stop_min_r=cfg.time_stop_min_r,
                    failed_breakout_bars=cfg.failed_breakout_bars,
                )
                if extras.get("new_stop") is not None:
                    open_trade["stop"] = extras["new_stop"]
                if extras.get("new_trail") is not None:
                    open_trade["trail"] = extras["new_trail"]
                if reason == PARTIAL_1R:
                    part_qty = int(extras.get("partial_qty") or max(1, int(open_trade["remaining"]) // 2))
                    part_px = Decimal(str(index_exit_fill(open_trade["side"], "TARGET_HIT", index_px, open_trade["stop"], index_px, cfg.slippage_points)))
                    _append_proxy_trade(trades, run, open_trade, cfg, lot_size, part_qty, part_px, PARTIAL_1R, bar)
                    open_trade["remaining"] = int(open_trade["remaining"]) - part_qty
                    open_trade["partial_taken"] = True
                    open_trade["quantity"] = open_trade["remaining"]
                    reason = None
                if reason is None:
                    reason = index_hold_exit(
                        open_trade["side"],
                        float(bar.high),
                        float(bar.low),
                        open_trade["stop"],
                        open_trade["target"],
                        is_entry_bar=i == open_trade["entry_index"],
                        clock=stamp,
                        square=expiry_square if is_index_expiry(symbol, clock.date()) else square,
                        session_end=session_end,
                        held_minutes=held,
                        max_hold_minutes=int(cfg.max_hold_minutes or 0),
                        end_of_walk=end_data,
                        end_of_session=last and not end_data,
                    )
                exit_px = Decimal(
                    str(
                        index_exit_fill(
                            open_trade["side"],
                            reason,
                            index_px,
                            open_trade["stop"],
                            open_trade["target"],
                            cfg.slippage_points,
                        )
                    )
                )
            else:
                mark = float(open_trade["entry_price"])
                exit_px = Decimal(str(mark))
                state = PositionState(
                    entry_price=float(open_trade["entry_price"]),
                    entry_time=open_trade["entry_time"] if open_trade["entry_time"].tzinfo else open_trade["entry_time"].replace(tzinfo=IST),
                    stop_loss=open_trade["stop"],
                    target=open_trade["target"],
                    trail_price=open_trade["trail"],
                    atr_at_entry=evaluation.atr,
                    mfe_price=open_trade["mfe"],
                )
                if trail_is_armed(state, mark, policy):
                    open_trade["trail"] = advance_trail(state, mark, policy, evaluation.atr)
                    state.trail_price = open_trade["trail"]
                open_trade["mfe"] = mark if state.mfe_price is None else max(state.mfe_price, mark)
                reason = choose_exit(
                    state,
                    ExitContext(
                        premium=mark,
                        now=now if now.tzinfo else now.replace(tzinfo=IST),
                        clock=clock.time().replace(microsecond=0),
                        close=evaluation.close,
                        ema=evaluation.ema,
                        vwap=evaluation.vwap,
                        rsi=evaluation.rsi,
                        rsi_min=0,
                        rsi_max=100,
                        live_atr=evaluation.atr,
                        kill_switch=False,
                        daily_loss_hit=halted,
                        data_stale=False,
                        direction="BEARISH" if open_trade["side"] in {"PE", "SELL"} else "BULLISH",
                        premium_ema=None,
                    ),
                    policy,
                )
            if last and not reason:
                reason = "END_OF_DATA" if end_data else "END_OF_SESSION"
            if reason:
                qty = int(open_trade.get("remaining") or open_trade["quantity"])
                if open_trade.get("proxy"):
                    gross, _ignored, net = index_paper_net(
                        open_trade["side"],
                        open_trade["entry_price"],
                        exit_px,
                        qty,
                        slippage_points=0,
                    )
                    slip = money(abs(float(cfg.slippage_points or 0)) * qty * 2)
                    total_charges = money(0)
                    brokerage = money(0)
                else:
                    calc = open_trade["calc"]
                    buy = calc.calculate_buy(open_trade["entry_price"], qty)
                    sell = calc.calculate_sell(exit_px, qty)
                    charges = calc.combine(buy, sell)
                    gross = gross_pnl(exit_px, open_trade["entry_price"], qty)
                    net = net_pnl(gross, charges.total)
                    slip = Decimal("0")
                    total_charges = charges.total
                    brokerage = charges.brokerage
                mapped = map_exit_reason(reason, end_of_data=reason == "END_OF_DATA")
                trades.append(
                    HistoricalPaperTrade(
                        run_id=run.id,
                        trade_date=to_ist(open_trade["entry_time"]).date(),
                        entry_time=_naive_ist(open_trade["entry_time"]),
                        exit_time=_naive_ist(bar.start),
                        instrument=open_trade["symbol"],
                        option_type=open_trade["side"],
                        strike=Decimal(str(open_trade["strike"])) if open_trade["strike"] is not None else None,
                        expiry=None,
                        quantity=qty,
                        entry_price=open_trade["entry_price"],
                        exit_price=exit_px,
                        signal_percent=Decimal(str(open_trade["score"])),
                        passed=open_trade["passed"],
                        total=open_trade["total"],
                        gross_pnl=gross,
                        total_charges=total_charges,
                        net_pnl=net,
                        exit_reason=mapped[:40],
                        buy_score=Decimal(str(open_trade["buy_score"])),
                        sell_score=Decimal(str(open_trade["sell_score"])),
                        explanation=(open_trade.get("explanation") or "")[:400],
                        indicators_json=json.dumps(open_trade.get("indicators") or {}, default=str),
                        lot_size=lot_size,
                        slippage=slip,
                        brokerage=brokerage,
                        strategy_name=cfg.name,
                    )
                )
                r_mult = signed_r(open_trade["side"], float(open_trade["entry_price"]), float(exit_px), float(open_trade.get("risk") or 0) or 1)
                try:
                    payload = json.loads(trades[-1].indicators_json or "{}")
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                payload["r_multiple"] = r_mult
                trades[-1].indicators_json = json.dumps(payload, default=str)
                equity += net
                session_net += net
                day_r += float(r_mult or 0)
                if mapped == "STOP_LOSS_HIT":
                    stopped_today = True
                    last_stop_index = i
                    day_losses += 1
                peak_equity = max(peak_equity, equity)
                max_dd = max(max_dd, peak_equity - equity)
                last_fill = clock
                open_trade = None
    return {"trades": trades, "option_available": option_available, "warnings": warnings, "equity": equity, "peak": peak_equity, "max_dd": max_dd, "samples": samples, "proxy_used": proxy_used, "error": ""}


SWEEP_DEFAULTS = {
    "thresholds": [65, 70, 75, 80, 85],
    "exit_variants": ["fixed_rr", "partial_trail", "opposite_signal"],
    "train_from": "2023-10-03",
    "train_to": "2025-08-31",
    "test_from": "2025-09-01",
    "test_to": "2026-10-01",
}


def execute_run(db: Session, run: HistoricalPaperRun) -> HistoricalPaperRun:
    run.status = "RUNNING"
    run.updated_at = as_utc_naive(now_utc())
    db.commit()
    try:
        snap = json.loads(run.strategy_snapshot or "{}")
    except json.JSONDecodeError:
        snap = {}
    if isinstance(snap, dict) and snap.get("sweep"):
        return execute_sweep(db, run, snap.get("sweep") if isinstance(snap.get("sweep"), dict) else {})
    version = latest_version(db)
    if version is None:
        raise RuntimeError("Strategy is missing")
    from app.db.models import PaperAutoSetting

    setting = db.get(PaperAutoSetting, 1)
    cfg = _index_cfg(run)
    symbols = parse_instruments(run.instrument)
    stats = {"INDEX": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0}),
             "CE": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0}),
             "PE": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0}),
             "BUY": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0}),
             "SELL": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0})}
    threshold_hits = {level: 0 for level in THRESHOLDS}
    trades = []
    samples = []
    warnings = []
    option_available = False
    proxy_used = False
    equity = Decimal("0")
    peak_equity = Decimal("0")
    max_dd = Decimal("0")
    if (run.date_to - run.date_from).days > 31:
        warnings.append("Selected range is larger than 31 days. Processed on the server; replay series is sampled.")
    for symbol in symbols:
        part = _walk_instrument(db, run, symbol, version, setting, cfg, stats, threshold_hits)
        trades.extend(part["trades"])
        samples.extend(part.get("samples") or [])
        option_available = option_available or part["option_available"]
        proxy_used = proxy_used or bool(part.get("proxy_used"))
        warnings.extend(part["warnings"])
        equity += part["equity"]
        peak_equity = max(peak_equity, part["peak"])
        max_dd = max(max_dd, part["max_dd"])
        if part["error"]:
            run.status = "FAILED"
            run.last_error = part["error"]
            run.option_data_note = f"{OPTION_NOTE} {INDEX_FUTURES_NOTE}"[:400]
            db.commit()
            return run
    run.option_data_available = option_available
    run.data_incomplete = any("incomplete" in item.lower() or "gap" in item.lower() for item in warnings)

    db.add_all(trades)
    for side, names in stats.items():
        for name, counts in names.items():
            db.add(
                HistoricalConditionStat(
                    run_id=run.id,
                    side=side,
                    name=name,
                    evaluated=counts["evaluated"],
                    passed=counts["passed"],
                    failed=counts["failed"],
                    unavailable=counts["unavailable"],
                )
            )
    closed = trades
    wins = [row for row in closed if row.net_pnl > 0]
    losses = [row for row in closed if row.net_pnl <= 0]
    gross = money(sum((row.gross_pnl for row in closed), Decimal("0")))
    charges = money(sum((row.total_charges for row in closed), Decimal("0")))
    net = money(sum((row.net_pnl for row in closed), Decimal("0")))
    win_gross = sum((row.net_pnl for row in wins), Decimal("0"))
    loss_gross = abs(sum((row.net_pnl for row in losses), Decimal("0")))
    analysis = []
    for level in THRESHOLDS:
        analysis.append(
            {
                "threshold": level,
                "signals": threshold_hits[level],
                "trades": len(closed) if level == float(run.threshold) else None,
                "win_rate": None,
                "net_pnl": None,
            }
        )
    run.total_trades = len(closed)
    run.wins = len(wins)
    run.losses = len(losses)
    run.win_rate = money(Decimal(len(wins) * 100) / Decimal(len(closed))) if closed else None
    run.gross_pnl = gross
    run.total_charges = charges
    run.net_pnl = net
    run.max_drawdown = money(max_dd)
    run.profit_factor = (win_gross / loss_gross).quantize(Decimal("0.01")) if loss_gross else None
    run.threshold_analysis = json.dumps(analysis)
    run.status = "COMPLETED"
    option_fills = any(row.option_type in {"CE", "PE"} for row in closed)
    if option_fills and not proxy_used:
        run.option_data_note = ""
        run.charge_note = "Zerodha F&O rates effective on each trade date"
    else:
        run.option_data_note = f"{OPTION_NOTE} {INDEX_FUTURES_NOTE}"[:400]
        run.charge_note = (
            "INDEX FUTURES PROXY. Option NFO charges are not applied to index notionals. "
            "NET is index-point P&L after entry/exit slippage only."
        )[:200]
    run.same_candle_rule = SAME_CANDLE_RULE
    run.warnings_json = json.dumps(warnings)
    summary = performance_from_trades(
        [
            {
                "status": "CLOSED",
                "net_pnl": float(row.net_pnl),
                "gross_pnl": float(row.gross_pnl),
                "total_charges": float(row.total_charges),
                "option_type": row.option_type,
                "instrument": row.instrument,
                "exit_reason": row.exit_reason,
                "exit_time": row.exit_time.isoformat() if row.exit_time else "",
                "entry_time": row.entry_time.isoformat() if row.entry_time else "",
                "date": row.trade_date.isoformat(),
                "holding_minutes": ((row.exit_time - row.entry_time).total_seconds() / 60) if row.exit_time and row.entry_time else None,
                "r_multiple": (_json_obj(getattr(row, "indicators_json", None)) or {}).get("r_multiple"),
            }
            for row in closed
        ]
    )
    summary["score_series"] = samples[:2500]
    run.performance_json = json.dumps(summary)
    run.updated_at = as_utc_naive(now_utc())
    db.commit()
    return run


def execute_sweep(db: Session, run: HistoricalPaperRun, sweep: dict) -> HistoricalPaperRun:
    """Walk stored index history for each threshold × exit variant × train/test split. Paper only."""
    spec = {**SWEEP_DEFAULTS, **(sweep or {})}
    cfg = _index_cfg(run)
    version = latest_version(db)
    setting = None
    from app.db.models import PaperAutoSetting

    setting = db.get(PaperAutoSetting, 1)
    symbols = parse_instruments(run.instrument)
    splits = [
        ("train", date.fromisoformat(str(spec["train_from"])), date.fromisoformat(str(spec["train_to"]))),
        ("test", date.fromisoformat(str(spec["test_from"])), date.fromisoformat(str(spec["test_to"]))),
    ]
    grid = []
    warnings = []
    for symbol in symbols:
        cover = stored_walk_cover(db, symbol, run.timeframe or "3Minute")
        if cover is None:
            warnings.append(f"No stored candles for {symbol}.")
            continue
        for split_name, raw_from, raw_to in splits:
            start = max(raw_from, cover[0])
            end = min(raw_to, cover[1])
            if end < start:
                warnings.append(f"{symbol} {split_name} {raw_from}→{raw_to} is outside stored {cover[0]}→{cover[1]}.")
                continue
            for threshold in spec.get("thresholds") or SWEEP_DEFAULTS["thresholds"]:
                for variant in spec.get("exit_variants") or SWEEP_DEFAULTS["exit_variants"]:
                    local = replace(cfg, buy_threshold=float(threshold), sell_threshold=float(threshold), exit_variant=str(variant))
                    clone = HistoricalPaperRun(
                        created_at=run.created_at,
                        updated_at=run.updated_at,
                        status="RUNNING",
                        instrument=symbol,
                        timeframe=run.timeframe,
                        date_from=start,
                        date_to=end,
                        threshold=Decimal(str(threshold)),
                        strike_range=run.strike_range,
                        strategy_snapshot=json.dumps({"index_strategy": local.as_dict()}),
                    )
                    stats = {
                        "INDEX": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0}),
                        "CE": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0}),
                        "PE": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0}),
                        "BUY": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0}),
                        "SELL": defaultdict(lambda: {"evaluated": 0, "passed": 0, "failed": 0, "unavailable": 0}),
                    }
                    hits = {level: 0 for level in THRESHOLDS}
                    part = _walk_instrument(db, clone, symbol, version, setting, local, stats, hits)
                    closed = part.get("trades") or []
                    summary = performance_from_trades(
                        [
                            {
                                "status": "CLOSED",
                                "net_pnl": float(row.net_pnl),
                                "gross_pnl": float(row.gross_pnl),
                                "total_charges": float(row.total_charges or 0),
                                "option_type": row.option_type,
                                "instrument": row.instrument,
                                "exit_reason": row.exit_reason,
                                "exit_time": row.exit_time.isoformat() if row.exit_time else "",
                                "entry_time": row.entry_time.isoformat() if row.entry_time else "",
                                "date": row.trade_date.isoformat(),
                                "r_multiple": (_json_obj(row.indicators_json) or {}).get("r_multiple"),
                            }
                            for row in closed
                        ]
                    )
                    expiry_rows = [row for row in closed if is_index_expiry(row.instrument, row.trade_date)]
                    other_rows = [row for row in closed if row not in expiry_rows]
                    grid.append(
                        {
                            "instrument": symbol,
                            "split": split_name,
                            "from_date": start.isoformat(),
                            "to_date": end.isoformat(),
                            "threshold": float(threshold),
                            "exit_variant": variant,
                            "trades": len(closed),
                            "win_pct": summary.get("win_rate"),
                            "avg_r": summary.get("average_r"),
                            "expectancy": summary.get("expectancy"),
                            "profit_factor": summary.get("profit_factor"),
                            "max_drawdown": summary.get("max_drawdown"),
                            "net_pnl": summary.get("net_pnl"),
                            "expiry_trades": len(expiry_rows),
                            "non_expiry_trades": len(other_rows),
                            "note": "HYPOTHETICAL / PAPER only. Not live performance.",
                        }
                    )
                    warnings.extend(part.get("warnings") or [])
    run.total_trades = sum(int(row["trades"]) for row in grid)
    run.net_pnl = money(sum(float(row.get("net_pnl") or 0) for row in grid))
    run.status = "COMPLETED"
    run.warnings_json = json.dumps(list(dict.fromkeys(warnings)))
    run.performance_json = json.dumps(
        {
            "label": "SCORE/EXIT SWEEP — HISTORICAL PAPER — NOT LIVE PERFORMANCE",
            "grid": grid,
            "spec": spec,
            "note": "HYPOTHETICAL / PAPER signals only. No broker orders.",
        }
    )
    run.updated_at = as_utc_naive(now_utc())
    db.commit()
    return run


def run_payload(db: Session, run: HistoricalPaperRun) -> dict:
    trades = [
        {
            "id": row.id,
            "date": row.trade_date.isoformat(),
            "time": row.entry_time.strftime("%H:%M") if row.entry_time else "",
            "entry_time": row.entry_time.isoformat() if row.entry_time else None,
            "exit_time": row.exit_time.isoformat() if row.exit_time else None,
            "instrument": row.instrument,
            "option_type": row.option_type,
            "strike": float(row.strike) if row.strike is not None else None,
            "expiry": row.expiry.isoformat() if row.expiry else None,
            "entry": float(row.entry_price),
            "exit": float(row.exit_price) if row.exit_price is not None else None,
            "quantity": row.quantity,
            "signal_percent": float(row.signal_percent),
            "gross_pnl": float(row.gross_pnl),
            "charges": float(row.total_charges),
            "net_pnl": float(row.net_pnl),
            "exit_reason": row.exit_reason,
            "buy_score": float(row.buy_score) if getattr(row, "buy_score", None) is not None else None,
            "sell_score": float(row.sell_score) if getattr(row, "sell_score", None) is not None else None,
            "explanation": getattr(row, "explanation", None),
            "indicators": _json_obj(getattr(row, "indicators_json", None)),
            "lot_size": getattr(row, "lot_size", None),
            "slippage": float(row.slippage) if getattr(row, "slippage", None) is not None else None,
            "brokerage": float(row.brokerage) if getattr(row, "brokerage", None) is not None else None,
            "strategy_name": getattr(row, "strategy_name", None),
            "status": "CLOSED",
            "paper_only": True,
        }
        for row in sorted(run.trades, key=lambda item: item.entry_time)
    ]
    stats = {"CE": [], "PE": [], "INDEX": []}
    for row in run.condition_stats:
        bucket = stats.setdefault(row.side, [])
        pct = round(100.0 * row.passed / row.evaluated, 2) if row.evaluated else 0
        bucket.append(
            {
                "name": row.name,
                "evaluated": row.evaluated,
                "passed": row.passed,
                "failed": row.failed,
                "unavailable": row.unavailable,
                "pass_percent": pct,
            }
        )
    try:
        analysis = json.loads(run.threshold_analysis or "[]")
    except json.JSONDecodeError:
        analysis = []
    return {
        "id": run.id,
        "status": run.status,
        "instrument": run.instrument,
        "timeframe": run.timeframe,
        "from_date": run.date_from.isoformat(),
        "to_date": run.date_to.isoformat(),
        "strategy_label": run.strategy_label,
        "strategy_version_id": run.strategy_version_id,
        "threshold": float(run.threshold),
        "initial_capital": float(run.initial_capital),
        "strike_range": run.strike_range,
        "option_data_available": run.option_data_available,
        "option_data_note": run.option_data_note,
        "data_incomplete": run.data_incomplete,
        "last_error": run.last_error,
        "total_trades": run.total_trades,
        "wins": run.wins,
        "losses": run.losses,
        "win_rate": float(run.win_rate) if run.win_rate is not None else None,
        "gross_pnl": float(run.gross_pnl),
        "total_charges": float(run.total_charges),
        "net_pnl": float(run.net_pnl),
        "max_drawdown": float(run.max_drawdown),
        "profit_factor": float(run.profit_factor) if run.profit_factor is not None else None,
        "trades": trades,
        "condition_stats": stats,
        "threshold_analysis": analysis,
        "charge_note": run.charge_note,
        "paper_mode": _paper_mode(run),
        "performance": _json_obj(getattr(run, "performance_json", None)) or {},
        "warnings": _json_obj(getattr(run, "warnings_json", None)) or [],
        "same_candle_rule": getattr(run, "same_candle_rule", None) or SAME_CANDLE_RULE,
        "strategy": _index_cfg(run).as_dict(),
        "note": "HYPOTHETICAL / PAPER signals only. No broker orders. HISTORICAL PAPER RESULTS — NOT LIVE PERFORMANCE.",
    }


def _json_obj(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


INDEX_PROXY_SIDES = {"BUY", "SELL"}
OPTION_CHARGE_ON_INDEX_MIN = 200


def is_index_proxy_trade(row) -> bool:
    return str(getattr(row, "option_type", "") or "").upper() in INDEX_PROXY_SIDES and getattr(row, "strike", None) is None


def repair_index_proxy_charges(db: Session) -> int:
    """Strip option NFO charges that were wrongly applied to index-proxy paper rows."""
    rows = list(db.scalars(select(HistoricalPaperTrade)).all())
    touched: set[int] = set()
    fixed = 0
    for row in rows:
        if not is_index_proxy_trade(row):
            continue
        charges = float(row.total_charges or 0)
        if charges < OPTION_CHARGE_ON_INDEX_MIN:
            continue
        row.total_charges = money(0)
        if getattr(row, "brokerage", None) is not None:
            row.brokerage = money(0)
        row.net_pnl = money(row.gross_pnl or 0)
        touched.add(row.run_id)
        fixed += 1
    for run_id in touched:
        run = db.get(HistoricalPaperRun, run_id)
        if run is None:
            continue
        closed = [row for row in rows if row.run_id == run_id]
        wins = [row for row in closed if row.net_pnl > 0]
        losses = [row for row in closed if row.net_pnl <= 0]
        run.wins = len(wins)
        run.losses = len(losses)
        run.win_rate = money(Decimal(len(wins) * 100) / Decimal(len(closed))) if closed else None
        run.gross_pnl = money(sum((row.gross_pnl for row in closed), Decimal("0")))
        run.total_charges = money(sum((row.total_charges or 0 for row in closed), Decimal("0")))
        run.net_pnl = money(sum((row.net_pnl for row in closed), Decimal("0")))
        win_gross = sum((row.net_pnl for row in wins), Decimal("0"))
        loss_gross = abs(sum((row.net_pnl for row in losses), Decimal("0")))
        run.profit_factor = (win_gross / loss_gross).quantize(Decimal("0.01")) if loss_gross else None
        run.charge_note = (
            "INDEX FUTURES PROXY. Option NFO charges removed from stored index notionals. "
            "NET is index-point P&L. HYPOTHETICAL / PAPER only."
        )[:200]
        summary = performance_from_trades(
            [
                {
                    "status": "CLOSED",
                    "net_pnl": float(row.net_pnl),
                    "gross_pnl": float(row.gross_pnl),
                    "total_charges": float(row.total_charges or 0),
                    "option_type": row.option_type,
                    "instrument": row.instrument,
                    "exit_reason": row.exit_reason,
                    "exit_time": row.exit_time.isoformat() if row.exit_time else "",
                    "entry_time": row.entry_time.isoformat() if row.entry_time else "",
                    "date": row.trade_date.isoformat(),
                    "holding_minutes": ((row.exit_time - row.entry_time).total_seconds() / 60) if row.exit_time and row.entry_time else None,
                }
                for row in closed
            ]
        )
        run.performance_json = json.dumps(summary)
        run.updated_at = as_utc_naive(now_utc())
    if fixed:
        db.commit()
    return fixed


def _paper_mode(run: HistoricalPaperRun) -> str:
    note = run.option_data_note or ""
    if "INDEX FUTURES PROXY" in note or not run.option_data_available:
        return "INDEX_FUTURES_PROXY"
    return "OPTIONS"


def _in_entry_hours(clock: datetime, profile, option_cfg) -> bool:
    if not in_session(clock, profile.session_start, profile.session_end):
        return False
    stamp = clock.time().replace(second=0, microsecond=0)
    return option_cfg.time_start <= stamp <= option_cfg.time_end


def _available_counts(result) -> tuple[int, int]:
    rows = [item for item in result.conditions if item.status != UNAVAILABLE]
    passed = sum(1 for item in rows if item.status == PASS)
    return passed, len(rows)


def _append_proxy_trade(trades, run, open_trade, cfg, lot_size, qty, exit_px, reason, bar):
    gross, _ignored, net = index_paper_net(open_trade["side"], open_trade["entry_price"], exit_px, qty, slippage_points=0)
    r_mult = signed_r(open_trade["side"], float(open_trade["entry_price"]), float(exit_px), float(open_trade.get("risk") or 0) or 1)
    indicators = dict(open_trade.get("indicators") or {})
    indicators["r_multiple"] = r_mult
    trades.append(
        HistoricalPaperTrade(
            run_id=run.id,
            trade_date=to_ist(open_trade["entry_time"]).date(),
            entry_time=_naive_ist(open_trade["entry_time"]),
            exit_time=_naive_ist(bar.start),
            instrument=open_trade["symbol"],
            option_type=open_trade["side"],
            strike=None,
            expiry=None,
            quantity=qty,
            entry_price=open_trade["entry_price"],
            exit_price=exit_px,
            signal_percent=Decimal(str(open_trade["score"])),
            passed=open_trade["passed"],
            total=open_trade["total"],
            gross_pnl=gross,
            total_charges=money(0),
            net_pnl=net,
            exit_reason=map_exit_reason(reason)[:40],
            buy_score=Decimal(str(open_trade["buy_score"])),
            sell_score=Decimal(str(open_trade["sell_score"])),
            explanation=(open_trade.get("explanation") or "")[:400],
            indicators_json=json.dumps(indicators, default=str),
            lot_size=lot_size,
            slippage=money(0),
            brokerage=money(0),
            strategy_name=cfg.name,
        )
    )


def _index_mark(side: str, entry: float, close: float) -> float:
    if side in {"PE", "SELL"}:
        return 2 * entry - close
    return close


def _index_gross(side: str, entry: Decimal, exit_px: Decimal, qty: int) -> Decimal:
    if side in {"PE", "SELL"}:
        return money((entry - exit_px) * qty)
    return gross_pnl(exit_px, entry, qty)


def _no_lookahead_window(bars, i: int, warmup: int = 240):
    current_day = to_ist(bars[i].start).date()
    day_start = i
    while day_start > 0 and to_ist(bars[day_start - 1].start).date() == current_day:
        day_start -= 1
    start = max(0, day_start - warmup)
    return bars[start : i + 1]


def _tally(bucket, conditions) -> None:
    for item in conditions:
        name = getattr(item, "name", None) or ""
        status = getattr(item, "status", None) or ("PASS" if getattr(item, "passed", False) else "FAIL")
        row = bucket[name]
        row["evaluated"] += 1
        if status == UNAVAILABLE or status == "DATA UNAVAILABLE":
            row["unavailable"] += 1
        elif status == "PASS" or getattr(item, "passed", False) is True:
            row["passed"] += 1
        else:
            row["failed"] += 1


def _naive_ist(value: datetime) -> datetime:
    local = value.astimezone(IST) if value.tzinfo else value
    return local.replace(tzinfo=None)


def _strategy_snapshot(version, params_row, symbol: str) -> str:
    payload = {
        "instrument": symbol,
        "version_id": version.id if version else None,
        "version_number": version.version_number if version else None,
        "note": version.note if version else "",
        "parameters": {},
    }
    if params_row is not None:
        payload["parameters"] = {
            column.key: jsonable(getattr(params_row, column.key))
            for column in params_row.__table__.columns
            if column.key not in {"id"}
        }
    return json.dumps(payload, default=str)

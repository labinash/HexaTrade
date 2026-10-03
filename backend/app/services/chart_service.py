"""Chart candles, indicators, and hypothetical signals. Reuses the strategy engine."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
import time as pytime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import as_utc_naive, now_ist, to_ist
from app.db.bootstrap import latest_version, params_for
from app.db.models import (
    ChartSignalRow,
    HistoricalPaperTrade,
    PaperTrade,
    UnderlyingProfile,
)
from app.domain.candles import Bar, resample_bars
from app.domain.chart_signals import BUY, SELL, ChartSignal, rising_edge
from app.domain.dashboard_brief import implied_market_direction, score_percent
from app.domain.historical import expected_session_bars, in_session, normalize_timeframe, timeframe_minutes
from app.domain.indicators import atr, ema, rsi, session_vwap
from app.domain.market_facts import market_facts_from_bars
from app.domain.index_strategy import (
    ENTRY_VALIDATION_RULES,
    config_from_dict,
    confirm_with_higher_tf,
    decide,
    features_from_bars,
    higher_tf_decision,
    index_hold_exit,
    index_paper_net,
    infer_walk_minutes,
    levels_for,
    parse_clock,
    performance_from_trades,
    validate_index_entry,
)
from app.domain.option_buy import OptionPremiumState, evaluate_ce_buy_strategy, evaluate_pe_buy_strategy, futures_state
from app.domain.paper_auto import eligible_score, gated_sides
from app.domain.strategy import snapshot_from_bars
from app.services.historical_backtest import _no_lookahead_window
from app.services.historical_repo import (
    bars_from_rows,
    count_candles_in_range,
    get_historical_candles,
    option_rows_exist,
    stored_coverage,
    stored_range,
)
from app.services.mapping import option_buy_config_of, params_of
from app.services.paper_engine import load_config

LIVE_PAPER = "LIVE_PAPER"
HISTORICAL = "HISTORICAL_ANALYSIS"
HISTORICAL_ANALYSIS = "HISTORICAL_ANALYSIS"
HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
HISTORICAL_MODES = {HISTORICAL, HISTORICAL_ANALYSIS, HISTORICAL_REPLAY, "HISTORICAL"}
RANGE_BARS = {"1D": 1, "5D": 5, "1M": 22}
MAX_SPAN_DAYS = 31
CALC_VERSION = "chart-historical-v10"
log = logging.getLogger("hextrade.chart")
_PAYLOAD_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 45.0
_CACHE_MAX = 12

INTERVAL_MAP = {
    "1m": ("minute", "1Minute"),
    "1Minute": ("minute", "1Minute"),
    "3m": ("3minute", "3Minute"),
    "3Minute": ("3minute", "3Minute"),
    "5m": ("5minute", "5Minute"),
    "5Minute": ("5minute", "5Minute"),
    "15m": ("15minute", "15Minute"),
    "15Minute": ("15minute", "15Minute"),
}


def chart_payload(
    db: Session,
    runtime,
    *,
    instrument: str,
    timeframe: str = "1m",
    mode: str = LIVE_PAPER,
    trading_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    range_key: str = "1D",
) -> dict:
    symbol = instrument.upper()
    if symbol not in {"NIFTY", "SENSEX"}:
        raise ValueError("Instrument must be NIFTY or SENSEX")
    live_interval, hist_tf = INTERVAL_MAP.get(timeframe, INTERVAL_MAP["1m"])
    hist_tf = normalize_timeframe(hist_tf)
    config = load_config(db)
    mode_key = _normalize_mode(mode)
    native_cover = stored_range(db, symbol, hist_tf)
    minute_cover = stored_range(db, symbol, "1Minute")
    cover = native_cover or minute_cover
    source_tf = hist_tf if native_cover is not None else "1Minute"
    resample_minutes = timeframe_minutes(hist_tf)
    start, end, date_warnings = _resolve_dates(trading_date, start_date, end_date, cover, mode_key)
    if mode_key in HISTORICAL_MODES and native_cover is None and minute_cover is not None and hist_tf != "1Minute":
        date_warnings.append(
            f"No stored {hist_tf} rows. {hist_tf} candles are built from the 1-minute NIFTY/SENSEX archive."
        )
    cache_key = None
    if mode_key in HISTORICAL_MODES:
        cache_key = "|".join(
            [
                CALC_VERSION,
                symbol,
                hist_tf,
                mode_key,
                start.isoformat(),
                end.isoformat(),
                str(int(bool(config.ce_enabled))),
                str(int(bool(config.pe_enabled))),
                str(getattr(config, "paper_instrument", "OPTIONS")),
                str(config.min_signal_score),
                str((getattr(config, "index_strategy", None) or {}).get("buy_threshold")),
                str((getattr(config, "index_strategy", None) or {}).get("sell_threshold")),
                str((getattr(config, "index_strategy", None) or {}).get("session_start")),
                str((getattr(config, "index_strategy", None) or {}).get("session_end")),
                str((getattr(config, "index_strategy", None) or {}).get("max_trades_per_day")),
                str((getattr(config, "index_strategy", None) or {}).get("cooldown_minutes")),
                str((getattr(config, "index_strategy", None) or {}).get("allow_reentry")),
                str((getattr(config, "index_strategy", None) or {}).get("no_reentry_after_stop")),
                str((getattr(config, "index_strategy", None) or {}).get("confirm_higher_tf")),
                str((getattr(config, "index_strategy", None) or {}).get("max_daily_loss")),
                str(config.cooldown_minutes),
            ]
        )
        hit = _cache_get(cache_key)
        if hit is not None:
            return hit
    version = latest_version(db)
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    params_row = params_for(version, symbol) if version else None
    lot = 1
    params = params_of(params_row, lot) if params_row else None
    bars = _load_bars(db, runtime, symbol, live_interval, source_tf, mode_key, start, end, range_key, resample_minutes)
    candles, indicators = series_from_bars(bars, params)
    if mode_key in HISTORICAL_MODES:
        candles = [row for row in candles if _ist_in_range(row.get("ist"), start, end)]
        indicators = {
            key: [row for row in series if _unix_in_range(row.get("time"), start, end)]
            for key, series in indicators.items()
        }
    rejected: list[dict] = []
    signals: list[ChartSignal] = []
    signals, rejected = _collect_signals(
        db, runtime, symbol, bars, params, params_row, profile, config, mode_key, start, end
    )
    data_status = "OK"
    view = None
    if runtime is not None and mode_key == LIVE_PAPER:
        from app.services.engine import market_view

        view = market_view(db, runtime, symbol)
        if not candles:
            data_status = "NO_SESSION"
            date_warnings.append(
                f"{start.isoformat()} has no live {symbol} session candles (market closed, weekend, or holiday)."
            )
    warnings = list(date_warnings)
    option_available = True
    if mode_key in HISTORICAL_MODES:
        option_available = option_rows_exist(db, symbol, "1Minute", start, end)
        if not option_available and candles:
            warnings.append(
                "Option history unavailable for this period. Index close is being used as the futures proxy."
            )
    stored = len(candles)
    session_start = profile.session_start if profile else time(9, 15)
    session_end = profile.session_end if profile else time(15, 30)
    if mode_key in HISTORICAL_MODES:
        days = {to_ist(bar.start).date() for bar in bars if start <= to_ist(bar.start).date() <= end}
        expected = len(days) * expected_session_bars(session_start, session_end, hist_tf)
        if stored == 0:
            data_status = "NO_SESSION" if start == end else "NO_DATA"
            archive_total = count_candles_in_range(db, symbol, source_tf, date(1990, 1, 1), date(2100, 1, 1))
            live_n = _live_candle_count(db, symbol)
            if archive_total == 0:
                warnings.append(
                    f"The historical archive has 0 {symbol} index rows. "
                    f"LIVE PAPER currently has {live_n} live candles; replay will not use them. "
                    "Open Historical Data, connect Zerodha, start an INITIAL import, and wait until the job is COMPLETED."
                )
            elif start == end:
                warnings.append(
                    f"{start.isoformat()} has no {symbol} index session (weekend, holiday, or not stored)."
                )
            else:
                warnings.append(
                    f"No {symbol} index session on {start.isoformat()} → {end.isoformat()}. "
                    f"Stored archive is {cover[0].isoformat()} → {cover[1].isoformat()}."
                    if cover else
                    "No historical data found for the selected period."
                )
        elif expected and stored < expected * 0.8:
            if data_status == "OK":
                data_status = "GAPS"
            warnings.append("Historical data contains gaps for the selected period.")
    payload = {
        "instrument": symbol,
        "timeframe": hist_tf,
        "timezone": "Asia/Kolkata",
        "today_ist": now_ist().date().isoformat(),
        "mode": mode_key,
        "date": start.isoformat() if start == end else f"{start.isoformat()} → {end.isoformat()}",
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "range": range_key,
        "note": "HYPOTHETICAL / PAPER signals only. Historical or paper results are not a forecast. No broker orders.",
        "calculation_version": CALC_VERSION,
        "sequential": mode_key == HISTORICAL_REPLAY,
        "data_status": data_status,
        "candles": candles,
        "indicators": indicators,
        "signals": [row.as_dict() if isinstance(row, ChartSignal) else row for row in signals],
        "rejected": rejected[:60],
        "summary": _summary(candles, signals, rejected),
        "strategy": _strategy_panel(view, config),
        "coverage": stored_coverage(db, "1Minute"),
        "available_from": cover[0].isoformat() if cover else None,
        "available_to": cover[1].isoformat() if cover else None,
        "archive_candles": stored,
        "live_candles": _live_candle_count(db, symbol) if mode_key in HISTORICAL_MODES else None,
        "warnings": warnings,
        "option_data_available": option_available,
        "params": {
            "ema_period": params.ema_period if params else 20,
            "rsi_period": params.rsi_period if params else 14,
            "atr_period": params.atr_period if params else 14,
            "session_start": session_start.strftime("%H:%M"),
            "session_end": session_end.strftime("%H:%M"),
            "threshold": float(config.min_signal_score),
            "cooldown_minutes": int(config.cooldown_minutes),
            "entry_validation": list(ENTRY_VALIDATION_RULES),
        },
    }
    last_facts = market_facts_from_bars(
        bars,
        ema_period=params.ema_period if params else 20,
        rsi_period=params.rsi_period if params else 14,
        atr_period=params.atr_period if params else 14,
        timeframe=hist_tf,
    )
    payload["last_facts"] = {
        "close": last_facts.close,
        "ema20": last_facts.ema20,
        "rsi": last_facts.rsi,
        "atr": last_facts.atr,
        "vwap": last_facts.vwap,
        "volume": last_facts.volume,
        "timeframe": last_facts.timeframe,
    }
    if runtime is not None and mode_key == LIVE_PAPER:
        last_unix = candles[-1]["time"] if candles else None
        try:
            from app.services.chart_trading import live_markers

            payload["signals"] = payload["signals"] + live_markers(db, runtime, symbol, last_unix)
        except Exception:
            log.exception("live zerodha chart markers failed")
    if cache_key:
        _cache_put(cache_key, payload)
    return payload


def series_from_bars(bars: list[Bar], params) -> tuple[list[dict], dict]:
    """Same EMA/VWAP/RSI/ATR as snapshot_from_bars, as full series for the chart."""
    candles = []
    ema_out: list[dict] = []
    vwap_out: list[dict] = []
    rsi_out: list[dict] = []
    atr_out: list[dict] = []
    volume_out: list[dict] = []
    oi_out: list[dict] = []
    if not bars:
        return candles, {"ema20": ema_out, "vwap": vwap_out, "rsi": rsi_out, "atr": atr_out, "volume": volume_out, "oi": oi_out}
    ema_period = params.ema_period if params else 20
    rsi_period = params.rsi_period if params else 14
    atr_period = params.atr_period if params else 14
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    ema_series = ema(closes, ema_period)
    rsi_series = rsi(closes, rsi_period)
    atr_series = atr([bar.high for bar in bars], [bar.low for bar in bars], closes, atr_period)
    typical = [(bar.high + bar.low + bar.close) / 3 for bar in bars]
    keys = [to_ist(bar.start).date() for bar in bars]
    vwap_series = session_vwap(typical, volumes, keys)
    for i, bar in enumerate(bars):
        unix = _unix(bar.start)
        candles.append(
            {
                "time": unix,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "oi": bar.oi,
                "ist": to_ist(bar.start).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        volume_out.append({"time": unix, "value": bar.volume, "color": "#2f7d4a" if bar.close >= bar.open else "#8a4b32"})
        if bar.oi:
            oi_out.append({"time": unix, "value": bar.oi})
        if ema_series[i] is not None:
            ema_out.append({"time": unix, "value": ema_series[i]})
        if vwap_series[i] is not None:
            vwap_out.append({"time": unix, "value": vwap_series[i]})
        if rsi_series[i] is not None:
            rsi_out.append({"time": unix, "value": rsi_series[i]})
        if atr_series[i] is not None:
            atr_out.append({"time": unix, "value": atr_series[i]})
    return candles, {"ema20": ema_out, "vwap": vwap_out, "rsi": rsi_out, "atr": atr_out, "volume": volume_out, "oi": oi_out}


def capture_live_signals(runtime, db: Session) -> None:
    """Persist first-cross hypothetical BUYs from the live engine. No broker orders."""
    from app.services.engine import market_view

    config = load_config(db)
    for symbol in ("NIFTY", "SENSEX"):
        view = market_view(db, runtime, symbol)
        _persist_threshold_buys(db, view, config)


def _persist_threshold_buys(db: Session, view: dict, config) -> None:
    symbol = view.get("symbol") or ""
    packs = view.get("candidates") or {}
    ce, pe = gated_sides(packs.get("ce"), packs.get("pe"), config.ce_enabled, config.pe_enabled)
    stamp = as_utc_naive(now_ist().replace(second=0, microsecond=0))
    for side, row in (("CE", ce), ("PE", pe)):
        if not row:
            continue
        passed = int(row.get("passed_count") or 0)
        total = int(row.get("total_count") or 0)
        if not eligible_score(passed, total, config.min_signal_score):
            continue
        existing = db.scalar(
            select(ChartSignalRow).where(
                ChartSignalRow.instrument == symbol,
                ChartSignalRow.timestamp == stamp,
                ChartSignalRow.signal_type == BUY,
                ChartSignalRow.option_type == side,
                ChartSignalRow.source == LIVE_PAPER,
            )
        )
        if existing:
            continue
        db.add(
            ChartSignalRow(
                instrument=symbol,
                timestamp=stamp,
                signal_type=BUY,
                option_type=side,
                strike=_dec(row.get("strike")),
                expiry=_expiry(row.get("expiry")),
                option_ltp=_dec(row.get("ltp") or row.get("premium_ltp")),
                underlying_ltp=_dec(view.get("futures_ltp") or view.get("ltp")),
                strategy_score=_dec(row.get("score_percent") or score_percent(passed, total)),
                passed_conditions=passed,
                total_conditions=total,
                entry_price=_dec(row.get("ltp") or row.get("premium_ltp")),
                tradingsymbol=str(row.get("tradingsymbol") or f"{symbol} {row.get('strike')} {side}"),
                is_hypothetical=True,
                source=LIVE_PAPER,
            )
        )


def _collect_signals(db, runtime, symbol, bars, params, params_row, profile, config, mode, start, end) -> tuple[list[ChartSignal], list[dict]]:
    out: list[ChartSignal] = []
    walked, rejected = _historical_replay_signals(
        db, symbol, bars, params, params_row, profile, config, start, end, sequential=mode == HISTORICAL_REPLAY
    )
    out.extend(walked)
    if mode in HISTORICAL_MODES:
        day = start
        while day <= end:
            out.extend(_signals_from_historical_trades(db, symbol, day))
            day += timedelta(days=1)
    else:
        out.extend(_signals_from_paper_trades(db, symbol, start))
        try:
            out.extend(_signals_from_rows(db, symbol, start, LIVE_PAPER))
        except Exception:
            pass
        if runtime is not None:
            from app.services.engine import market_view

            view = market_view(db, runtime, symbol)
            out.extend(_live_watch_signals(view, config, bars))
    return _dedupe(out), rejected


def _historical_replay_signals(db, symbol, bars, params, params_row, profile, config, start, end, sequential: bool) -> tuple[list[ChartSignal], list[dict]]:
    """Walk bars[:i+1] with the existing evaluators. No lookahead. No broker orders."""
    if not bars:
        return [], []
    option_cfg = option_buy_config_of(params_row, profile) if params_row is not None and profile is not None else None
    threshold = config.min_signal_score
    cooldown = timedelta(minutes=max(0, int(config.cooldown_minutes or 0)))
    session_start = (profile.session_start if profile else None) or time(9, 15)
    session_end = (profile.session_end if profile else None) or time(15, 30)
    entry_start = option_cfg.time_start if option_cfg is not None else time(9, 15)
    entry_end = option_cfg.time_end if option_cfg is not None else time(15, 25)
    prev_ce = False
    prev_pe = False
    last_buy_at = {"CE": None, "PE": None}
    open_side: dict[str, ChartSignal] = {}
    signals: list[ChartSignal] = []
    rejected: list[dict] = []
    idx_cfg = config_from_dict(getattr(config, "index_strategy", None) or {})
    option_available = option_rows_exist(db, symbol, "1Minute", start, end)
    use_index = True
    idx_start = parse_clock(idx_cfg.session_start, entry_start)
    idx_end = parse_clock(idx_cfg.session_end, entry_end)
    prev_buy = False
    prev_sell = False
    last_index_at = None
    open_index: ChartSignal | None = None
    open_index_i: int | None = None
    day_key = None
    day_trades = 0
    day_pnl = Decimal("0")
    stopped_today = False
    square = parse_clock(idx_cfg.square_off, time(15, 20))
    walk_minutes = infer_walk_minutes(bars)
    confirm_minutes = int(idx_cfg.confirm_minutes or 5)
    higher_bars = _higher_tf_bars(db, symbol, start, end, idx_cfg, walk_minutes)
    paper_qty = _index_paper_qty(db, symbol, idx_cfg)
    for i, bar in enumerate(bars):
        now = to_ist(bar.start)
        day = now.date()
        if day > end:
            break
        if day < start:
            continue
        visible = _no_lookahead_window(bars, i)
        snap = snapshot_from_bars(visible, params) if params is not None else None
        clock = now.time().replace(microsecond=0)
        hours_ok = in_session(bar.start, session_start, session_end) and entry_start <= clock <= entry_end
        if option_available and option_cfg is not None and snap is not None:
            futures = futures_state(visible, option_cfg, symbol=symbol)
            option = OptionPremiumState(option_type="", data_quality="Historical option data unavailable")
            ce = evaluate_ce_buy_strategy(option, futures, option_cfg, now)
            pe = evaluate_pe_buy_strategy(option, futures, option_cfg, now)
            ce_score = score_percent(ce.passed_count, ce.total_count)
            pe_score = score_percent(pe.passed_count, pe.total_count)
            ce_hot = bool(config.ce_enabled) and hours_ok and eligible_score(ce.passed_count, ce.total_count, threshold)
            pe_hot = bool(config.pe_enabled) and hours_ok and eligible_score(pe.passed_count, pe.total_count, threshold)
        else:
            ce = pe = None
            ce_score = pe_score = 0
            ce_hot = pe_hot = False
        in_range = start <= day <= end
        facts = features_from_bars(visible, idx_cfg)
        tape = implied_market_direction(
            close=facts.get("close") if snap is None else snap.close,
            ema=facts.get("ema") if snap is None else snap.ema,
            vwap=facts.get("vwap") if snap is None else snap.vwap,
            momentum=facts.get("momentum") if snap is None else (
                (snap.close - snap.reference_close) if snap.close is not None and snap.reference_close is not None else None
            ),
        )
        verdict = decide(features_from_bars(visible, idx_cfg, tape=tape), idx_cfg) if use_index else None
        stamp_ok = idx_start <= clock <= idx_end and in_session(bar.start, session_start, session_end)
        buy_hot = bool(verdict and stamp_ok and idx_cfg.buy_enabled and verdict.decision == "BUY")
        sell_hot = bool(verdict and stamp_ok and idx_cfg.sell_enabled and verdict.decision == "SELL")
        if not in_range:
            prev_ce = ce_hot
            prev_pe = pe_hot
            prev_buy = buy_hot
            prev_sell = sell_hot
            continue
        if day_key != day:
            day_key = day
            day_trades = 0
            day_pnl = Decimal("0")
            stopped_today = False
        indicators = {
            "ema": snap.ema if snap is not None else facts.get("ema"),
            "vwap": snap.vwap if snap is not None else facts.get("vwap"),
            "rsi": snap.rsi if snap is not None else facts.get("rsi"),
            "atr": snap.atr if snap is not None else facts.get("atr"),
            "momentum": facts.get("momentum") if snap is None else (
                (snap.close - snap.reference_close) if snap.close is not None and snap.reference_close is not None else None
            ),
            "close": snap.close if snap is not None else facts.get("close"),
            "ce_score": verdict.buy.score if verdict else ce_score,
            "pe_score": verdict.sell.score if verdict else pe_score,
            "threshold": float(idx_cfg.buy_threshold if verdict else threshold),
        }
        if option_available and option_cfg is not None:
            for side, result, hot, prev, enabled in (
                ("CE", ce, ce_hot, prev_ce, config.ce_enabled),
                ("PE", pe, pe_hot, prev_pe, config.pe_enabled),
            ):
                if not enabled:
                    continue
                if rising_edge(prev, hot):
                    last = last_buy_at[side]
                    if last is not None and cooldown.total_seconds() > 0 and now - last < cooldown:
                        rejected.append(_reject(symbol, bar, side, result, "Duplicate suppressed by cooldown.", indicators))
                        continue
                    buy = _from_eval(symbol, bar, BUY, side, result, snap.close, HISTORICAL_ANALYSIS, indicators, sequential)
                    buy.result = "OPEN"
                    buy.signal_reason = f"{side} score {buy.strategy_score}% reached threshold {float(threshold)}% on this candle only."
                    signals.append(buy)
                    last_buy_at[side] = now
                    open_side[side] = buy
                elif not hot:
                    reason = _reject_reason(result, hours_ok, enabled, threshold)
                    if result.total_count and (result.passed_count or result.failed):
                        if len(rejected) < 80:
                            rejected.append(_reject(symbol, bar, side, result, reason, indicators))
                    held = open_side.get(side)
                    if held is not None and (not hours_ok or clock >= session_end or clock >= entry_end):
                        sell = _from_eval(symbol, bar, SELL, side, result, snap.close, HISTORICAL_ANALYSIS, indicators, sequential)
                        sell.entry_price = held.entry_price
                        sell.exit_reason = "TIME_EXIT"
                        sell.signal_reason = "Session or configured trading window ended. Option P&L is NOT EVALUATED without historical option premiums."
                        sell.result = "NOT EVALUATED"
                        signals.append(sell)
                        open_side.pop(side, None)
                    elif held is not None and not hot and prev:
                        sell = _from_eval(symbol, bar, SELL, side, result, snap.close, HISTORICAL_ANALYSIS, indicators, sequential)
                        sell.entry_price = held.entry_price
                        sell.exit_reason = "STRATEGY_INVALIDATED"
                        sell.result = "CANCELLED"
                        sell.signal_reason = reason
                        signals.append(sell)
                        open_side.pop(side, None)
        if use_index and verdict is not None:
            closed_this_bar = False
            if open_index is not None:
                entry_stamp = open_index.timestamp
                if isinstance(entry_stamp, datetime) and entry_stamp.tzinfo is None:
                    entry_stamp = entry_stamp.replace(tzinfo=timezone.utc)
                held = (now - to_ist(entry_stamp)).total_seconds() / 60 if isinstance(entry_stamp, datetime) else 0
                next_day = to_ist(bars[i + 1].start).date() if i + 1 < len(bars) else None
                end_of_walk = i + 1 >= len(bars) or (next_day is not None and next_day > end)
                end_of_session = next_day is not None and next_day != day
                reason = index_hold_exit(
                    open_index.option_type,
                    float(bar.high),
                    float(bar.low),
                    open_index.stop_loss_price,
                    open_index.target_price,
                    is_entry_bar=open_index_i == i,
                    clock=clock,
                    square=square,
                    session_end=session_end,
                    held_minutes=held,
                    max_hold_minutes=int(idx_cfg.max_hold_minutes or 0),
                    end_of_walk=end_of_walk,
                    end_of_session=end_of_session,
                )
                if reason:
                    exit_row = _index_chart_exit(symbol, bar, open_index, reason, sequential, now)
                    fill = float(exit_row.exit_price if exit_row.exit_price is not None else bar.close)
                    gross, slip, net = index_paper_net(
                        open_index.option_type,
                        open_index.entry_price,
                        fill,
                        paper_qty,
                        slippage_points=idx_cfg.slippage_points,
                    )
                    exit_row.gross_pnl = float(gross)
                    exit_row.total_charges = float(slip)
                    exit_row.net_pnl = float(net)
                    signals.append(exit_row)
                    day_pnl += net
                    if reason == "STOP_LOSS_HIT":
                        stopped_today = True
                    open_index = None
                    open_index_i = None
                    closed_this_bar = True
            if open_index is None and not closed_this_bar:
                asof = bar.start + timedelta(minutes=walk_minutes)
                confirm_side = (
                    higher_tf_decision(higher_bars, asof, idx_cfg, confirm_minutes)
                    if confirm_with_higher_tf(walk_minutes, idx_cfg)
                    else None
                )
                for side, hot, prev in (("BUY", buy_hot, prev_buy), ("SELL", sell_hot, prev_sell)):
                    if not rising_edge(prev, hot):
                        continue
                    score = verdict.buy.score if side == "BUY" else verdict.sell.score
                    ok, why = validate_index_entry(
                        side=side,
                        clock=clock,
                        config=idx_cfg,
                        day_trades=day_trades,
                        day_pnl=day_pnl,
                        stopped_today=stopped_today,
                        last_entry_at=last_index_at,
                        now=now,
                        higher_tf_side=confirm_side,
                        walk_minutes=walk_minutes,
                        score=score,
                    )
                    if not ok:
                        if len(rejected) < 80:
                            rejected.append(_index_gate_reject(symbol, bar, side, why, indicators))
                        continue
                    stop, target = levels_for(float(bar.close), side, idx_cfg, snap.atr if snap is not None else facts.get("atr"))
                    passed_n = sum(1 for item in (verdict.buy if side == "BUY" else verdict.sell).conditions if item.passed)
                    total_n = len((verdict.buy if side == "BUY" else verdict.sell).conditions)
                    signals.append(
                        ChartSignal(
                            signal_id=f"INDEX|{symbol}|{side}|ENTRY|{_unix(bar.start)}",
                            instrument=symbol,
                            timestamp=bar.start,
                            signal_type=BUY if side == "BUY" else SELL,
                            option_type=side,
                            strike=None,
                            expiry=None,
                            option_ltp=float(bar.close),
                            underlying_ltp=float(bar.close),
                            strategy_score=score,
                            passed_conditions=passed_n,
                            total_conditions=total_n,
                            entry_price=float(bar.close),
                            target_price=target,
                            stop_loss_price=stop,
                            exit_price=None,
                            exit_reason=None,
                            tradingsymbol=f"{symbol} {side}",
                            is_hypothetical=True,
                            source="INDEX_PROXY",
                            candle_low=bar.low,
                            candle_high=bar.high,
                            strategy_name=idx_cfg.name,
                            candle_timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
                            ema=snap.ema if snap is not None else facts.get("ema"),
                            vwap=snap.vwap if snap is not None else facts.get("vwap"),
                            rsi=snap.rsi if snap is not None else facts.get("rsi"),
                            atr=snap.atr if snap is not None else facts.get("atr"),
                            ce_score=verdict.buy.score,
                            pe_score=verdict.sell.score,
                            threshold=verdict.buy.threshold if side == "BUY" else verdict.sell.threshold,
                            sequential=sequential,
                            signal_reason=verdict.reason,
                            result="OPEN",
                            marker_role="entry",
                        )
                    )
                    last_index_at = now
                    open_index = signals[-1]
                    open_index_i = i
                    day_trades += 1
                    break
        prev_ce = ce_hot
        prev_pe = pe_hot
        prev_buy = buy_hot
        prev_sell = sell_hot
    return signals, rejected


def _index_exit_fill(reason: str, held: ChartSignal, close: float) -> float:
    if reason == "TARGET_HIT" and held.target_price is not None:
        return float(held.target_price)
    if reason == "STOP_LOSS_HIT" and held.stop_loss_price is not None:
        return float(held.stop_loss_price)
    return float(close)


def _index_chart_exit(symbol: str, bar: Bar, held: ChartSignal, reason: str, sequential: bool, now) -> ChartSignal:
    fill = _index_exit_fill(reason, held, bar.close)
    return ChartSignal(
        signal_id=f"INDEX|{symbol}|{held.option_type}|EXIT|{_unix(bar.start)}",
        instrument=symbol,
        timestamp=bar.start,
        signal_type=SELL,
        option_type=held.option_type,
        strike=None,
        expiry=None,
        option_ltp=fill,
        underlying_ltp=float(bar.close),
        strategy_score=held.strategy_score,
        passed_conditions=held.passed_conditions,
        total_conditions=held.total_conditions,
        entry_price=held.entry_price,
        target_price=held.target_price,
        stop_loss_price=held.stop_loss_price,
        exit_price=fill,
        exit_reason=reason,
        tradingsymbol=f"{symbol} {held.option_type}",
        is_hypothetical=True,
        source="INDEX_PROXY",
        candle_low=bar.low,
        candle_high=bar.high,
        strategy_name=held.strategy_name,
        candle_timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
        sequential=sequential,
        signal_reason="Index paper exit at stop, target, or session. HYPOTHETICAL / PAPER only. No broker order.",
        result="CLOSED",
        marker_role="exit",
    )


def _from_eval(symbol: str, bar: Bar, kind: str, side: str, result, underlying, source: str, indicators: dict | None = None, sequential: bool = False) -> ChartSignal:
    indicators = indicators or {}
    failed = []
    for item in getattr(result, "failed", None) or []:
        if isinstance(item, dict):
            failed.append(str(item.get("name") or item.get("failure_reason") or item))
        else:
            failed.append(str(getattr(item, "name", item)))
    return ChartSignal(
        signal_id=f"{source}|{symbol}|{kind}|{side}|{_unix(bar.start)}",
        instrument=symbol,
        timestamp=bar.start,
        signal_type=kind,
        option_type=side,
        strike=float(result.strike) if getattr(result, "strike", None) is not None else None,
        expiry=_expiry(getattr(result, "expiry", None)),
        option_ltp=result.premium_ltp if getattr(result, "premium_ltp", None) is not None else None,
        underlying_ltp=underlying,
        strategy_score=score_percent(result.passed_count, result.total_count),
        passed_conditions=result.passed_count,
        total_conditions=result.total_count,
        entry_price=result.premium_ltp if getattr(result, "premium_ltp", None) is not None else underlying,
        tradingsymbol=getattr(result, "option_symbol", None) or f"{symbol} {side}",
        is_hypothetical=True,
        source=source,
        candle_low=bar.low,
        candle_high=bar.high,
        strategy_name=f"{side} Strategy",
        candle_timestamp=to_ist(bar.start).strftime("%Y-%m-%d %H:%M:%S"),
        ema=indicators.get("ema"),
        vwap=indicators.get("vwap"),
        rsi=indicators.get("rsi"),
        atr=indicators.get("atr"),
        momentum=indicators.get("momentum"),
        ce_score=indicators.get("ce_score"),
        pe_score=indicators.get("pe_score"),
        threshold=indicators.get("threshold"),
        sequential=sequential,
        rejection_reason="; ".join(failed[:4]),
        result="OPEN" if kind == BUY else "NOT EVALUATED",
        signal_reason=getattr(result, "data_quality", "") or "",
    )


def _signals_from_paper_trades(db, symbol: str, session: date) -> list[ChartSignal]:
    rows = db.scalars(select(PaperTrade).where(PaperTrade.underlying == symbol)).all()
    out = []
    for row in rows:
        entry = to_ist(row.entry_time.replace(tzinfo=row.entry_time.tzinfo or timezone.utc)).date()
        if entry != session:
            continue
        out.append(_paper_buy(row))
        if row.status == "CLOSED" and row.exit_time is not None:
            out.append(_paper_sell(row))
    return out


def _signals_from_historical_trades(db, symbol: str, session: date) -> list[ChartSignal]:
    rows = db.scalars(
        select(HistoricalPaperTrade).where(
            HistoricalPaperTrade.instrument == symbol,
            HistoricalPaperTrade.trade_date == session,
        )
    ).all()
    out = []
    for row in rows:
        out.append(
            ChartSignal(
                signal_id=f"HIST_TRADE|{row.id}|ENTRY",
                instrument=symbol,
                timestamp=row.entry_time,
                signal_type=BUY if row.option_type not in {"SELL", "PE"} else SELL,
                option_type=row.option_type,
                strike=float(row.strike) if row.strike is not None else None,
                expiry=row.expiry,
                option_ltp=float(row.entry_price),
                underlying_ltp=None,
                strategy_score=float(row.signal_percent),
                passed_conditions=row.passed,
                total_conditions=row.total,
                entry_price=float(row.entry_price),
                target_price=None,
                stop_loss_price=None,
                exit_price=float(row.exit_price) if row.exit_price is not None else None,
                exit_reason=row.exit_reason,
                tradingsymbol=f"{symbol} {row.option_type}",
                quantity=row.quantity,
                gross_pnl=float(row.gross_pnl),
                total_charges=float(row.total_charges),
                net_pnl=float(row.net_pnl),
                is_hypothetical=True,
                source="HISTORICAL_TRADE",
                paper_trade_id=row.id,
                strategy_name=getattr(row, "strategy_name", None) or "",
                signal_reason=getattr(row, "explanation", None) or "",
                ce_score=float(row.buy_score) if getattr(row, "buy_score", None) is not None else None,
                pe_score=float(row.sell_score) if getattr(row, "sell_score", None) is not None else None,
                marker_role="entry",
            )
        )
        if row.exit_time is not None:
            out.append(
                ChartSignal(
                    signal_id=f"HIST_TRADE|{row.id}|EXIT",
                    instrument=symbol,
                    timestamp=row.exit_time,
                    signal_type=SELL,
                    option_type=row.option_type,
                    strike=float(row.strike) if row.strike is not None else None,
                    expiry=row.expiry,
                    option_ltp=float(row.exit_price) if row.exit_price is not None else None,
                    underlying_ltp=None,
                    strategy_score=float(row.signal_percent),
                    passed_conditions=row.passed,
                    total_conditions=row.total,
                    entry_price=float(row.entry_price),
                    target_price=None,
                    stop_loss_price=None,
                    exit_price=float(row.exit_price) if row.exit_price is not None else None,
                    exit_reason=row.exit_reason,
                    tradingsymbol=f"{symbol} {row.option_type}",
                    quantity=row.quantity,
                    gross_pnl=float(row.gross_pnl),
                    total_charges=float(row.total_charges),
                    net_pnl=float(row.net_pnl),
                    is_hypothetical=True,
                    source="HISTORICAL_TRADE",
                    paper_trade_id=row.id,
                    strategy_name=getattr(row, "strategy_name", None) or "",
                    signal_reason=getattr(row, "explanation", None) or "",
                    ce_score=float(row.buy_score) if getattr(row, "buy_score", None) is not None else None,
                    pe_score=float(row.sell_score) if getattr(row, "sell_score", None) is not None else None,
                    marker_role="exit",
                )
            )
    return out


def _signals_from_rows(db, symbol: str, session: date, source: str) -> list[ChartSignal]:
    start = datetime.combine(session, datetime.min.time())
    end = start + timedelta(days=1)
    rows = db.scalars(
        select(ChartSignalRow).where(
            ChartSignalRow.instrument == symbol,
            ChartSignalRow.source == source,
            ChartSignalRow.timestamp >= start,
            ChartSignalRow.timestamp < end,
        )
    ).all()
    return [_from_row(row) for row in rows]


def _from_row(row: ChartSignalRow) -> ChartSignal:
    return ChartSignal(
        signal_id=f"ROW|{row.id}",
        instrument=row.instrument,
        timestamp=row.timestamp,
        signal_type=row.signal_type,
        option_type=row.option_type,
        strike=float(row.strike) if row.strike is not None else None,
        expiry=row.expiry,
        option_ltp=float(row.option_ltp) if row.option_ltp is not None else None,
        underlying_ltp=float(row.underlying_ltp) if row.underlying_ltp is not None else None,
        strategy_score=float(row.strategy_score) if row.strategy_score is not None else None,
        passed_conditions=row.passed_conditions,
        total_conditions=row.total_conditions,
        entry_price=float(row.entry_price) if row.entry_price is not None else None,
        target_price=float(row.target_price) if row.target_price is not None else None,
        stop_loss_price=float(row.stop_loss_price) if row.stop_loss_price is not None else None,
        exit_price=float(row.exit_price) if row.exit_price is not None else None,
        exit_reason=row.exit_reason,
        is_hypothetical=bool(row.is_hypothetical),
        tradingsymbol=row.tradingsymbol,
        quantity=row.quantity,
        gross_pnl=float(row.gross_pnl) if row.gross_pnl is not None else None,
        total_charges=float(row.total_charges) if row.total_charges is not None else None,
        net_pnl=float(row.net_pnl) if row.net_pnl is not None else None,
        charges_estimated=row.charges_estimated,
        source=row.source,
        paper_trade_id=row.paper_trade_id,
    )


def _paper_buy(row: PaperTrade) -> ChartSignal:
    side = BUY if row.option_type not in {"SELL", "PE"} else SELL
    return ChartSignal(
        signal_id=f"PAPER|{row.id}|ENTRY",
        instrument=row.underlying,
        timestamp=row.entry_time,
        signal_type=side,
        option_type=row.option_type,
        strike=float(row.strike) if row.strike is not None else None,
        expiry=row.expiry,
        option_ltp=float(row.entry_option_ltp or row.entry_price) if (row.entry_option_ltp or row.entry_price) is not None else None,
        underlying_ltp=float(row.entry_underlying_price) if row.entry_underlying_price is not None else None,
        strategy_score=float(row.entry_signal_score),
        passed_conditions=row.entry_passed,
        total_conditions=row.entry_total,
        entry_price=float(row.entry_price),
        target_price=float(row.target) if row.target is not None else None,
        stop_loss_price=float(row.stop_loss) if row.stop_loss is not None else None,
        exit_price=None,
        exit_reason=None,
        tradingsymbol=row.tradingsymbol,
        quantity=row.quantity,
        is_hypothetical=True,
        source="PAPER_TRADE",
        paper_trade_id=row.id,
        charges_estimated=row.charges_estimated,
        marker_role="entry",
    )


def _paper_sell(row: PaperTrade) -> ChartSignal:
    return ChartSignal(
        signal_id=f"PAPER|{row.id}|EXIT",
        instrument=row.underlying,
        timestamp=row.exit_time,
        signal_type=SELL,
        option_type=row.option_type,
        strike=float(row.strike) if row.strike is not None else None,
        expiry=row.expiry,
        option_ltp=float(row.exit_price) if row.exit_price is not None else None,
        underlying_ltp=float(row.exit_underlying_price) if row.exit_underlying_price is not None else None,
        strategy_score=float(row.entry_signal_score),
        passed_conditions=row.entry_passed,
        total_conditions=row.entry_total,
        entry_price=float(row.entry_price),
        target_price=float(row.target) if row.target is not None else None,
        stop_loss_price=float(row.stop_loss) if row.stop_loss is not None else None,
        exit_price=float(row.exit_price) if row.exit_price is not None else None,
        exit_reason=row.exit_reason,
        tradingsymbol=row.tradingsymbol,
        quantity=row.quantity,
        gross_pnl=float(row.gross_pnl),
        total_charges=float(row.total_charges),
        net_pnl=float(row.net_pnl),
        charges_estimated=row.charges_estimated,
        is_hypothetical=True,
        source="PAPER_TRADE",
        paper_trade_id=row.id,
        marker_role="exit",
    )


def _live_watch_signals(view: dict, config, bars: list[Bar]) -> list[ChartSignal]:
    packs = view.get("candidates") or {}
    ce, pe = gated_sides(packs.get("ce"), packs.get("pe"), config.ce_enabled, config.pe_enabled)
    last = bars[-1] if bars else None
    out = []
    for side, row in (("CE", ce), ("PE", pe)):
        if not row or last is None:
            continue
        passed = int(row.get("passed_count") or 0)
        total = int(row.get("total_count") or 0)
        if not eligible_score(passed, total, config.min_signal_score):
            continue
        out.append(
            ChartSignal(
                signal_id=f"LIVE|{view.get('symbol')}|BUY|{side}|{_unix(last.start)}",
                instrument=view.get("symbol") or "",
                timestamp=last.start,
                signal_type=BUY,
                option_type=side,
                strike=float(row["strike"]) if row.get("strike") is not None else None,
                expiry=_expiry(row.get("expiry")),
                option_ltp=float(row["ltp"]) if row.get("ltp") is not None else None,
                underlying_ltp=view.get("futures_ltp") or view.get("ltp"),
                strategy_score=float(row.get("score_percent") or score_percent(passed, total)),
                passed_conditions=passed,
                total_conditions=total,
                entry_price=float(row["ltp"]) if row.get("ltp") is not None else None,
                tradingsymbol=str(row.get("tradingsymbol") or ""),
                is_hypothetical=True,
                source="LIVE_WATCH",
                candle_low=last.low,
                candle_high=last.high,
            )
        )
    return out


def _live_index_watch_signals(symbol: str, bars: list[Bar], config) -> list[ChartSignal]:
    if not bars:
        return []
    idx_cfg = config_from_dict(getattr(config, "index_strategy", None) or {})
    last = bars[-1]
    facts = features_from_bars(bars, idx_cfg)
    tape = implied_market_direction(
        close=facts.get("close"),
        ema=facts.get("ema"),
        vwap=facts.get("vwap"),
        momentum=facts.get("momentum"),
    )
    verdict = decide(features_from_bars(bars, idx_cfg, tape=tape), idx_cfg)
    if verdict.decision not in {"BUY", "SELL"}:
        return []
    side = verdict.decision
    scored = verdict.buy if side == "BUY" else verdict.sell
    unix = _unix(last.start)
    return [
        ChartSignal(
            signal_id=f"LIVE|{symbol}|{side}|INDEX|{unix}",
            instrument=symbol,
            timestamp=last.start,
            signal_type=BUY if side == "BUY" else SELL,
            option_type=side,
            strike=None,
            expiry=None,
            option_ltp=float(last.close),
            underlying_ltp=float(last.close),
            strategy_score=float(scored.score),
            passed_conditions=sum(1 for item in scored.conditions if item.passed),
            total_conditions=len(scored.conditions),
            entry_price=float(last.close),
            target_price=None,
            stop_loss_price=None,
            exit_price=None,
            exit_reason=None,
            tradingsymbol=f"{symbol} {side}",
            is_hypothetical=True,
            source="LIVE_INDEX",
            candle_low=last.low,
            candle_high=last.high,
            marker_role="entry",
            signal_reason=verdict.reason or "",
        )
    ]


def _strategy_panel(view: dict | None, config) -> dict:
    ce = (view or {}).get("ce_strategy") or {}
    pe = (view or {}).get("pe_strategy") or {}
    setup = (view or {}).get("best_setup") or {}
    idx = config_from_dict(getattr(config, "index_strategy", None) or {})
    return {
        "ce_enabled": config.ce_enabled,
        "pe_enabled": config.pe_enabled,
        "auto_paper": config.enabled,
        "threshold": float(config.min_signal_score),
        "ce": {
            "enabled": config.ce_enabled,
            "passed": ce.get("passed_count"),
            "total": ce.get("total_count"),
            "score_percent": ce.get("score_percent"),
            "status": ce.get("status"),
        },
        "pe": {
            "enabled": config.pe_enabled,
            "passed": pe.get("passed_count"),
            "total": pe.get("total_count"),
            "score_percent": pe.get("score_percent"),
            "status": pe.get("status"),
        },
        "best_setup": setup,
        "direction": (view or {}).get("displayed_direction"),
        "market_status": (view or {}).get("market_status"),
        "entry_validation": list(ENTRY_VALIDATION_RULES),
        "paper_instrument": getattr(config, "paper_instrument", "OPTIONS"),
        "index_strategy": idx.as_dict(),
        "allow_reentry": bool(idx.allow_reentry),
        "max_trades_per_day": int(idx.max_trades_per_day or 2),
        "max_daily_loss": float(idx.max_daily_loss or 5000),
        "no_reentry_after_stop": bool(idx.no_reentry_after_stop),
        "buy_enabled": bool(idx.buy_enabled),
        "sell_enabled": bool(idx.sell_enabled),
    }


def _load_bars(db, runtime, symbol, live_interval, hist_tf, mode, start, end, range_key, resample_minutes: int = 1) -> list[Bar]:
    if mode in HISTORICAL_MODES:
        warmup = start - timedelta(days=7)
        rows = get_historical_candles(db, symbol, hist_tf, warmup, end)
        bars = bars_from_rows(rows)
        if resample_minutes > 1:
            bars = resample_bars(bars, resample_minutes)
        return bars
    from app.adapters.kite.instruments import front_future
    from app.services.engine import load_bars

    future = front_future(db, symbol, start)
    if future is None:
        return []
    span = RANGE_BARS.get(range_key.upper(), 1)
    limit = max(400, span * 400)
    if resample_minutes > 1:
        minute_bars = load_bars(db, int(future.instrument_token), "minute", max(limit * resample_minutes, 1500))
        bars = resample_bars(minute_bars, resample_minutes)
    else:
        bars = load_bars(db, int(future.instrument_token), live_interval, limit)
    if not bars:
        return []
    begin = start - timedelta(days=span - 1)
    return [bar for bar in bars if to_ist(bar.start).date() >= begin and to_ist(bar.start).date() <= end]


def _time_on_session(unix, session: date) -> bool:
    if unix is None:
        return False
    stamp = datetime.fromtimestamp(int(unix), tz=timezone.utc)
    return to_ist(stamp).date() == session


def _session_date(value: str | None, mode: str) -> date:
    if value:
        return date.fromisoformat(str(value)[:10])
    return now_ist().date()


def _normalize_mode(mode: str) -> str:
    raw = str(mode or LIVE_PAPER).upper().replace(" ", "_")
    if raw in {"HISTORICAL_REPLAY", "REPLAY"}:
        return HISTORICAL_REPLAY
    if raw in {"HISTORICAL", "HISTORICAL_ANALYSIS", "ANALYSIS"}:
        return HISTORICAL_ANALYSIS
    return LIVE_PAPER


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def _resolve_dates(trading_date, start_date, end_date, cover: tuple[date, date] | None, mode: str) -> tuple[date, date, list[str]]:
    warnings: list[str] = []
    start = _parse_day(start_date) or _parse_day(trading_date)
    end = _parse_day(end_date) or start
    today = now_ist().date()
    if start is None:
        start = cover[1] if cover and mode in HISTORICAL_MODES else today
    if end is None:
        end = start
    if end < start:
        start, end = end, start
        warnings.append("End date was before start date; the range was swapped.")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        end = start + timedelta(days=MAX_SPAN_DAYS - 1)
        warnings.append(f"Range capped at {MAX_SPAN_DAYS} days so the browser does not load the full archive.")
    if cover and mode in HISTORICAL_MODES and start != end:
        first, last = cover
        if start < first or end > last or start > last or end < first:
            warnings.append(
                f"Selected range is outside stored Asia/Kolkata data ({first.isoformat()} → {last.isoformat()})."
            )
        start = min(max(start, first), last)
        end = min(max(end, first), last)
        if end < start:
            end = start
    elif cover and mode in HISTORICAL_MODES and start == end:
        first, last = cover
        if start < first or start > last:
            warnings.append(
                f"{start.isoformat()} is outside stored Asia/Kolkata data ({first.isoformat()} → {last.isoformat()})."
            )
    return start, end, warnings


def _ist_in_range(ist_text, start: date, end: date) -> bool:
    if not ist_text:
        return False
    try:
        day = date.fromisoformat(str(ist_text)[:10])
    except ValueError:
        return False
    return start <= day <= end


def _unix_in_range(unix, start: date, end: date) -> bool:
    if unix is None:
        return False
    stamp = datetime.fromtimestamp(int(unix), tz=timezone.utc)
    day = to_ist(stamp).date()
    return start <= day <= end


def _higher_tf_bars(db, symbol: str, start: date, end: date, idx_cfg, walk_minutes: int):
    if not confirm_with_higher_tf(walk_minutes, idx_cfg):
        return []
    minutes = int(idx_cfg.confirm_minutes or 5)
    warmup = start - timedelta(days=10)
    rows = get_historical_candles(db, symbol, "1Minute", warmup, end)
    bars = resample_bars(bars_from_rows(rows), minutes)
    if bars:
        return bars
    try:
        from app.adapters.kite.instruments import front_future
        from app.services.engine import load_bars

        future = front_future(db, symbol)
        if future is None:
            return []
        return resample_bars(load_bars(db, int(future.instrument_token), "minute", max(1500, minutes * 400)), minutes)
    except Exception:
        return []


def _index_paper_qty(db, symbol: str, idx_cfg) -> int:
    lots = max(1, int(idx_cfg.lots or 1))
    try:
        from app.adapters.kite.instruments import front_future

        future = front_future(db, symbol)
        if future and future.lot_size:
            return max(1, int(future.lot_size) * lots)
    except Exception:
        pass
    return 100 * lots


def _index_gate_reject(symbol, bar, side: str, reason: str, indicators: dict) -> dict:
    return {
        "instrument": symbol,
        "option_type": side,
        "timestamp": to_ist(bar.start).strftime("%Y-%m-%d %H:%M:%S"),
        "time_unix": _unix(bar.start),
        "strategy_score": indicators.get("ce_score") if side == "BUY" else indicators.get("pe_score"),
        "passed_conditions": None,
        "total_conditions": None,
        "reason": reason,
        "ce_score": indicators.get("ce_score"),
        "pe_score": indicators.get("pe_score"),
        "threshold": indicators.get("threshold"),
        "note": "HYPOTHETICAL / PAPER. Entry blocked by 3m validation. No broker order.",
    }


def _reject_reason(result, hours_ok: bool, enabled: bool, threshold) -> str:
    if not enabled:
        return "Strategy is OFF."
    if not hours_ok:
        return "Outside configured trading hours."
    score = score_percent(result.passed_count, result.total_count)
    if score < float(threshold):
        failed = result.failed or []
        names = []
        for item in failed[:4]:
            if isinstance(item, dict):
                names.append(str(item.get("name") or item.get("reason") or item.get("failure_reason") or ""))
            else:
                names.append(str(getattr(item, "name", item)))
        names = ", ".join(part for part in names if part)
        return f"Score {score}% below threshold {float(threshold)}%." + (f" Failed: {names}." if names else "")
    return result.data_quality or "Candidate rejected."


def _reject(symbol, bar, side, result, reason, indicators) -> dict:
    return {
        "instrument": symbol,
        "option_type": side,
        "timestamp": to_ist(bar.start).strftime("%Y-%m-%d %H:%M:%S"),
        "time_unix": _unix(bar.start),
        "strategy_score": score_percent(result.passed_count, result.total_count),
        "passed_conditions": result.passed_count,
        "total_conditions": result.total_count,
        "reason": reason,
        "ce_score": indicators.get("ce_score"),
        "pe_score": indicators.get("pe_score"),
        "threshold": indicators.get("threshold"),
        "signal": "WAIT",
        "result": "NOT EVALUATED",
    }


def _summary(candles, signals: list[ChartSignal], rejected: list[dict]) -> dict:
    def count(kind, side=None):
        return sum(1 for row in signals if row.signal_type == kind and (side is None or row.option_type == side))

    closed_rows = [
        {
            "status": "CLOSED",
            "net_pnl": row.net_pnl,
            "gross_pnl": row.gross_pnl,
            "total_charges": row.total_charges or 0,
            "option_type": row.option_type,
            "instrument": row.instrument,
            "exit_reason": row.exit_reason,
            "exit_time": row.timestamp.isoformat() if row.timestamp else "",
            "date": row.timestamp.date().isoformat() if row.timestamp else "",
        }
        for row in signals
        if row.net_pnl is not None and (row.marker_role == "exit" or row.signal_type == SELL and row.exit_price is not None)
    ]
    perf = performance_from_trades(closed_rows)
    sells = [row for row in signals if row.signal_type == SELL]
    closed = [row for row in sells if row.net_pnl is not None]
    open_n = count(BUY) - count(SELL)
    if open_n < 0:
        open_n = 0
    reasons = Counter(row.get("reason") or "Unspecified" for row in rejected)
    return {
        "candles": len(candles),
        "ce_buy": count(BUY, "CE"),
        "pe_buy": count(BUY, "PE"),
        "ce_sell": count(SELL, "CE"),
        "pe_sell": count(SELL, "PE"),
        "buy": count(BUY),
        "sell": count(SELL),
        "winning": perf["winning_trades"],
        "losing": perf["losing_trades"],
        "open": open_n,
        "win_rate": perf["win_rate"],
        "average_return": perf["average_trade_result"],
        "net_pnl": perf["net_pnl"],
        "max_drawdown": perf["max_drawdown"],
        "gross_profit": perf["gross_profit"],
        "gross_loss": perf["gross_loss"],
        "total_costs": perf["total_costs"],
        "rejected": len(rejected),
        "rejection_reasons": [{"reason": name, "count": n} for name, n in reasons.most_common(8)],
        "pnl_formula": perf["pnl_formula"],
        "note": "HYPOTHETICAL / PAPER signals only. Not a guarantee of future performance. No broker orders.",
    }


def _cache_get(key: str):
    row = _PAYLOAD_CACHE.get(key)
    if row is None:
        return None
    stored_at, payload = row
    if pytime.monotonic() - stored_at > _CACHE_TTL:
        _PAYLOAD_CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: dict) -> None:
    if len(_PAYLOAD_CACHE) >= _CACHE_MAX:
        oldest = min(_PAYLOAD_CACHE, key=lambda item: _PAYLOAD_CACHE[item][0])
        _PAYLOAD_CACHE.pop(oldest, None)
    _PAYLOAD_CACHE[key] = (pytime.monotonic(), payload)


def _live_candle_count(db: Session, symbol: str) -> int:
    from app.adapters.kite.instruments import front_future, spot_instrument
    from app.db.models import Candle

    tokens = []
    spot = spot_instrument(db, symbol)
    future = front_future(db, symbol)
    if spot:
        tokens.append(int(spot.instrument_token))
    if future:
        tokens.append(int(future.instrument_token))
    if not tokens:
        return 0
    return int(db.scalar(select(func.count()).select_from(Candle).where(Candle.instrument_token.in_(tokens))) or 0)


def _unix(value: datetime) -> int:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return int(aware.timestamp())


def _bar_stamp(evaluation, view) -> datetime:
    return as_utc_naive(now_ist().replace(second=0, microsecond=0))


def _dec(value):
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _expiry(value):
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _dedupe(rows: list[ChartSignal]) -> list[ChartSignal]:
    seen = set()
    out = []
    for row in rows:
        key = (row.instrument, _unix(row.timestamp) if isinstance(row.timestamp, datetime) else row.timestamp, row.signal_type, row.option_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=lambda item: (_unix(item.timestamp) if isinstance(item.timestamp, datetime) else 0, item.option_type, item.signal_type))
    return out

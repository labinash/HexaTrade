"""Assemble independent CE and PE option-buy results for one underlying.

Contract selection reuses the existing option-chain method (ATM / ITM / OTM).
Premium indicators read the selected contract's candles. Futures indicators
always read the front-month index future, never index spot volume or spot OHLC.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("hextrade.option_buy")

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import now_ist, now_utc
from app.db.bootstrap import latest_version, params_for
from app.db.models import AppSetting, PaperAutoSetting, Position
from app.domain.candles import Bar
from app.domain.chain import atm_strike
from app.domain.dashboard_brief import (
    best_setup_copy,
    candidate_label,
    confirmed_trend,
    contract_gaps,
    dashboard_decision,
    direction_basis,
    implied_market_direction,
    indicator_summary,
    market_status,
    pick_best,
    preferred_label,
    preferred_side_for,
    readiness_copy,
    score_percent,
    trend_confidence,
    unconfirmed_notes,
)
from app.domain.exits import ExitContext, PositionState, advance_trail, choose_exit, trail_is_armed
from app.domain.indicators import ema
from app.domain.oi_control import around_atm
from app.domain.option_buy import (
    OptionBuyConfig,
    empty_result,
    evaluate_ce_buy_strategy,
    evaluate_pe_buy_strategy,
    futures_state,
    option_premium_state,
)
from app.domain.strategy import direction_state
from app.services.mapping import exit_of, f, option_buy_config_of


def evaluate_option_sides(db: Session, runtime, view: dict) -> tuple[dict, dict]:
    """Return (ce_strategy, pe_strategy) dicts. Decisions are not shared."""
    symbol = view.get("symbol") or ""
    now = now_ist()
    version = latest_version(db)
    profile = view.get("profile")
    row = params_for(version, symbol) if version else None
    if row is None or profile is None:
        config = OptionBuyConfig(underlying=symbol)
        return empty_result("CE", config, "Strategy is not configured", now).as_dict(), empty_result(
            "PE", config, "Strategy is not configured", now
        ).as_dict()
    config = option_buy_config_of(row, profile)
    from app.services.engine import INTERVALS, chain_snapshot, ensure_today_minutes, load_bars

    interval = INTERVALS.get(config.timeframe_minutes, "minute")
    future = view.get("future")
    fut_quality = ""
    fut_bars: list[Bar] = []
    if future is None:
        fut_quality = "Index futures contract is unavailable"
    else:
        ensure_today_minutes(runtime, db, int(future.instrument_token))
        fut_bars = load_bars(db, int(future.instrument_token), interval)
        if interval != "minute":
            # Futures filters must stay on the configured futures contract.
            if not fut_bars:
                fut_quality = f"No {interval} index futures candles; minute bars are not substituted"
        elif not fut_bars:
            fut_quality = "Index futures candles are unavailable"
    futures = futures_state(fut_bars, config, symbol=future.tradingsymbol if future else None, quality=fut_quality)

    setting = db.get(AppSetting, 1)
    above = setting.strikes_above if setting else 10
    below = setting.strikes_below if setting else 10
    chain = chain_snapshot(db, runtime, symbol, None, above, below)
    price = chain.futures_ltp if chain.futures_ltp is not None else chain.spot
    expiry = chain.expiry.isoformat() if chain.expiry else None
    stale = bool(view.get("stale"))
    rule = version.option_rule if version else None
    method = rule.method if rule else "atm"
    offset = rule.strike_offset if rule else 0

    ce = _side_result(
        db, runtime, config, now, chain, "CE", method, offset, price, expiry, interval, futures, load_bars, ensure_today_minutes, stale,
    )
    pe = _side_result(
        db, runtime, config, now, chain, "PE", method, offset, price, expiry, interval, futures, load_bars, ensure_today_minutes, stale,
    )
    try:
        attach_dashboard_brief(db, runtime, view, config, now, chain, interval, futures, load_bars, stale, ce, pe)
    except Exception:
        log.exception("Dashboard briefing failed for %s; CE/PE results are still returned", symbol)
        view.setdefault("market_status", "WAITING")
        view.setdefault("direction_basis", [])
        view.setdefault("candidates", {"no_valid_candidate": True, "reason": "Candidate ranking unavailable"})
        view.setdefault("trade_readiness", {"message": "Strategy results are available; candidate ranking did not finish."})
        view.setdefault("displayed_direction", implied_market_direction(
            close=view.get("evaluation").close if view.get("evaluation") else None,
            ema=view.get("evaluation").ema if view.get("evaluation") else None,
            vwap=view.get("evaluation").vwap if view.get("evaluation") else None,
            momentum=view.get("evaluation").momentum if view.get("evaluation") else None,
        ) if view.get("evaluation") else "NEUTRAL")
        view.setdefault("presentation", {
            "trend": "NOT READY",
            "preferred_label": "NO PREFERRED CONTRACT — MARKET NOT CONFIRMED",
            "decision": {"label": "DATA NOT READY", "buy": False, "reason": "Dashboard briefing failed. Strategy results are still returned."},
            "indicators": indicator_summary([], []),
            "market_ready_is_not_buy": True,
        })
    ce_payload = ce.as_dict()
    pe_payload = pe.as_dict()
    ce_payload["enabled"] = bool(view.get("ce_strategy_enabled", True))
    pe_payload["enabled"] = bool(view.get("pe_strategy_enabled", True))
    return ce_payload, pe_payload


def exit_alerts_for(db: Session, runtime) -> list[dict]:
    """Watch open CE/PE positions against configured exits. Never sends an order."""
    version = latest_version(db)
    if version is None:
        return []
    policy = exit_of(version.exit_policy)
    from app.services.engine import INTERVALS, load_bars, market_view

    alerts = []
    for position in db.scalars(select(Position).where(Position.status == "OPEN")).all():
        view = market_view(db, runtime, position.underlying)
        quote = runtime.cache.get(int(position.instrument_token))
        premium = quote.get("ltp")
        params_row = params_for(version, position.underlying)
        minutes = params_row.timeframe_minutes if params_row else 1
        interval = INTERVALS.get(minutes, "minute")
        option_bars = load_bars(db, int(position.instrument_token), interval)
        premium_ema = None
        if option_bars and params_row is not None:
            series = ema([bar.close for bar in option_bars], params_row.ema_period)
            premium_ema = series[-1]
        evaluation = view.get("evaluation")
        entry_time = position.entry_time
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        state = PositionState(
            entry_price=float(position.entry_price),
            entry_time=entry_time,
            stop_loss=f(position.stop_loss),
            target=f(position.target),
            trail_price=f(position.trail_price),
            atr_at_entry=f(position.atr_at_entry),
            mfe_price=f(position.mfe_price),
        )
        live_atr = evaluation.atr if evaluation else None
        armed = False
        if premium is not None:
            armed = trail_is_armed(state, float(premium), policy)
            if armed:
                state.trail_price = advance_trail(state, float(premium), policy, live_atr)
        reason = None
        if premium is not None:
            reason = choose_exit(
                state,
                ExitContext(
                    premium=float(premium),
                    now=now_utc(),
                    clock=now_ist().time().replace(microsecond=0),
                    close=evaluation.close if evaluation else None,
                    ema=evaluation.ema if evaluation else None,
                    vwap=evaluation.vwap if evaluation else None,
                    rsi=evaluation.rsi if evaluation else None,
                    rsi_min=0,
                    rsi_max=100,
                    live_atr=live_atr,
                    kill_switch=False,
                    daily_loss_hit=False,
                    data_stale=bool(view.get("stale")),
                    direction="BEARISH" if position.option_type == "PE" else "BULLISH",
                    premium_ema=premium_ema,
                ),
                policy,
            )
        alerts.append(
            {
                "position_id": position.id,
                "underlying": position.underlying,
                "symbol": position.tradingsymbol,
                "option_type": position.option_type,
                "entry_price": f(position.entry_price),
                "premium": premium,
                "premium_ema20": premium_ema,
                "stop_loss": f(position.stop_loss),
                "target": f(position.target),
                "trail_armed": armed,
                "trail_price": state.trail_price,
                "reason": reason,
                "message": _alert_message(reason, armed, policy),
                "note": "Exit alert only. No order was sent.",
            }
        )
    return alerts


def _alert_message(reason: str | None, armed: bool, policy) -> str:
    if reason == "STOP_LOSS":
        return "Stop-loss level based on the persisted entry premium has been reached."
    if reason == "STRATEGY_INVALIDATED":
        return "Option premium has closed below its EMA20, or an index reversal test has fired."
    if reason == "TARGET":
        return "Target based on the configured multiple of initial risk has been reached."
    if reason == "TRAILING_STOP":
        return "Trailing stop-loss has been hit."
    if armed:
        return f"Trailing stop is armed after {policy.trail_arm_fraction:.0%} of the target distance."
    return "No exit condition is active."


def attach_dashboard_brief(db, runtime, view: dict, config: OptionBuyConfig, now, chain, interval, futures, load_bars, stale: bool, ce, pe) -> None:
    """Explain the existing evaluations. Does not place an order or pick a BUY."""
    profile = view.get("profile")
    params = view.get("params")
    evaluation = view.get("evaluation")
    clock = now.time().replace(microsecond=0)
    session_start = profile.session_start if profile else clock
    session_end = profile.session_end if profile else clock
    time_start = params.time_start if params else config.time_start
    time_end = params.time_end if params else config.time_end
    time_enabled = params.time_enabled if params else True
    view["market_status"] = market_status(
        clock=clock,
        session_start=session_start,
        session_end=session_end,
        time_start=time_start,
        time_end=time_end,
        time_enabled=time_enabled,
        stale=bool(view.get("stale")),
        has_futures=view.get("futures_ltp") is not None,
        has_spot=view.get("ltp") is not None,
    )
    view["market_status_note"] = "Market Ready means session data is usable. It is not a BUY."
    conditions = []
    if evaluation is not None:
        conditions = [item.__dict__ for item in evaluation.conditions]
    view["direction_basis"] = direction_basis(conditions)
    configured = view.get("direction") or (params.direction if params else None)
    shown = implied_market_direction(
        close=evaluation.close if evaluation else None,
        ema=evaluation.ema if evaluation else None,
        vwap=evaluation.vwap if evaluation else None,
        momentum=evaluation.momentum if evaluation else None,
    )
    agreement = None
    if evaluation is not None and params is not None:
        _, agreement = direction_state(evaluation, params)
    view["configured_direction"] = configured
    view["displayed_direction"] = shown
    view["direction_agreement"] = agreement
    if configured and shown != configured:
        view["direction_note"] = (
            f"Live tape is {shown} from futures vs EMA/VWAP/momentum. "
            f"Configured bias remains {configured} and only grades those checks."
        )
    else:
        view["direction_note"] = "Market direction is the live futures structure, not a BUY."
    view["market_snapshot"] = {
        "ltp": view.get("ltp"),
        "futures": view.get("futures_ltp"),
        "vwap": view.get("vwap"),
        "ema20": view.get("ema"),
        "rsi": view.get("rsi"),
        "atr": view.get("atr"),
        "oi": view.get("futures_oi"),
        "volume": view.get("volume"),
    }
    threshold = float(config.min_candidate_score_percent or 0)
    span = max(0, int(config.candidate_strike_range or 0))
    ranked = _rank_candidates(db, runtime, config, now, chain, interval, futures, load_bars, stale, threshold, span)
    ce_cands = [row for row in ranked if row["option_type"] == "CE"]
    pe_cands = [row for row in ranked if row["option_type"] == "PE"]
    best_ce = _highest(ce_cands)
    best_pe = _highest(pe_cands)
    setting = db.get(PaperAutoSetting, 1)
    ce_on = True if setting is None else bool(getattr(setting, "ce_strategy_enabled", True))
    pe_on = True if setting is None else bool(getattr(setting, "pe_strategy_enabled", True))
    paper_threshold = float(setting.min_signal_score) if setting and setting.min_signal_score is not None else threshold
    if not ranked:
        best, reason = None, "Option-chain data unavailable"
    else:
        eligible = []
        if ce_on and best_ce:
            eligible.append(best_ce)
        if pe_on and best_pe:
            eligible.append(best_pe)
        best, reason = pick_best(eligible, paper_threshold)
        if best is None and not reason:
            reason = "No enabled strategy has reached the configured candidate threshold."
    view["ce_strategy_enabled"] = ce_on
    view["pe_strategy_enabled"] = pe_on
    view["entry_threshold"] = paper_threshold
    view["candidates"] = {
        "ce": best_ce,
        "pe": best_pe,
        "best": best,
        "threshold": paper_threshold,
        "range": span,
        "atm": chain.atm if chain else None,
        "expiry": chain.expiry.isoformat() if chain and chain.expiry else None,
        "reason": reason,
        "no_strong_candidate": best is None,
        "no_valid_candidate": not ranked,
        "ce_enabled": ce_on,
        "pe_enabled": pe_on,
    }
    view["best_setup"] = best_setup_copy(
        ce_enabled=ce_on, pe_enabled=pe_on, ce=best_ce, pe=best_pe, threshold=paper_threshold
    )
    ce_ok = bool(ce_on and best_ce and best_ce.get("is_strategy_valid"))
    pe_ok = bool(pe_on and best_pe and best_pe.get("is_strategy_valid"))
    view["trade_readiness"] = {
        "ce_passed": ce_ok,
        "pe_passed": pe_ok,
        "ce_passed_count": (best_ce or {}).get("passed_count", ce.passed_count),
        "ce_total": (best_ce or {}).get("total_count", ce.total_count),
        "pe_passed_count": (best_pe or {}).get("passed_count", pe.passed_count),
        "pe_total": (best_pe or {}).get("total_count", pe.total_count),
        "ce_score_percent": score_percent(
            int((best_ce or {}).get("passed_count", ce.passed_count) or 0),
            int((best_ce or {}).get("total_count", ce.total_count) or 0),
        ),
        "pe_score_percent": score_percent(
            int((best_pe or {}).get("passed_count", pe.passed_count) or 0),
            int((best_pe or {}).get("total_count", pe.total_count) or 0),
        ),
        "message": readiness_copy(ce_ok, pe_ok),
    }
    view["presentation"] = _dashboard_presentation(view, chain, ce, pe, ce_on, pe_on, paper_threshold)


def _dashboard_presentation(view: dict, chain, ce, pe, ce_on: bool, pe_on: bool, threshold: float) -> dict:
    evaluation = view.get("evaluation")
    trend = confirmed_trend(view.get("market_status"), view.get("displayed_direction"))
    confidence = trend_confidence(
        trend,
        close=evaluation.close if evaluation else None,
        ema=evaluation.ema if evaluation else view.get("ema"),
        vwap=evaluation.vwap if evaluation else view.get("vwap"),
        momentum=evaluation.momentum if evaluation else None,
    )
    side = preferred_side_for(trend)
    ce_dict = ce.as_dict() if hasattr(ce, "as_dict") else (ce or {})
    pe_dict = pe.as_dict() if hasattr(pe, "as_dict") else (pe or {})
    chosen = ce_dict if side == "CE" else pe_dict if side == "PE" else None
    quote = _side_quote(chain, side, (chosen or {}).get("strike")) if side else {}
    contract = _preferred_contract(chosen, quote, view.get("lot_size")) if chosen and side else None
    gaps = contract_gaps(contract) if side else []
    notes = unconfirmed_notes(view.get("market_status"), view.get("market_snapshot"), trend)
    enabled = ce_on if side == "CE" else pe_on if side == "PE" else False
    decision = dashboard_decision(
        trend=trend,
        market_status=view.get("market_status"),
        side=side,
        result=chosen,
        enabled=enabled,
        threshold=threshold,
        gaps=gaps,
        missing_notes=notes,
    )
    option_rows = (chosen or {}).get("conditions") if side else None
    indicators = indicator_summary(view.get("direction_basis") or view.get("conditions") or [], option_rows)
    return {
        "trend": trend,
        "trend_confidence": confidence,
        "preferred_side": side,
        "preferred_label": preferred_label(trend),
        "preferred_contract": contract,
        "contract_gaps": gaps,
        "decision": decision,
        "indicators": indicators,
        "market_ready_is_not_buy": True,
        "note": "HYPOTHETICAL / PAPER only. Trend direction is not a BUY. No broker orders.",
    }


def _preferred_contract(result: dict, quote: dict, fallback_lot) -> dict:
    ltp = quote.get("ltp") if quote.get("ltp") is not None else result.get("premium_ltp")
    lot = quote.get("lot_size") or fallback_lot
    oi = quote.get("oi") if quote.get("oi") is not None else result.get("option_oi")
    return {
        "tradingsymbol": quote.get("tradingsymbol") or result.get("option_symbol"),
        "option_type": result.get("option_type"),
        "strike": result.get("strike"),
        "expiry": result.get("expiry"),
        "ltp": ltp,
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "spread": quote.get("spread"),
        "spread_percent": quote.get("spread_percent"),
        "oi": oi,
        "volume": quote.get("volume"),
        "lot_size": lot,
        "liquidity": oi if oi is not None else quote.get("volume"),
        "token": quote.get("token") or result.get("token"),
    }


def _side_quote(chain, kind: str | None, strike) -> dict:
    if chain is None or kind is None or strike is None or not getattr(chain, "rows", None):
        return {}
    target = float(strike)
    for row in chain.rows:
        if abs(float(row.strike) - target) > 1e-6:
            continue
        side = row.ce if kind == "CE" else row.pe
        if side is None:
            return {}
        spread = side.spread
        spr_pct = None
        if side.bid is not None and side.ask is not None and (side.bid + side.ask):
            mid = (side.bid + side.ask) / 2
            if mid:
                spr_pct = round((side.ask - side.bid) / mid * 100.0, 4)
        return {
            "tradingsymbol": side.tradingsymbol,
            "ltp": side.ltp,
            "bid": side.bid,
            "ask": side.ask,
            "spread": spread,
            "spread_percent": spr_pct,
            "oi": side.oi,
            "volume": side.volume,
            "lot_size": side.lot_size or None,
            "token": side.token,
        }
    return {}


def _rank_candidates(db, runtime, config, now, chain, interval, futures, load_bars, stale, threshold, span):
    if chain is None or not chain.rows:
        return []
    price = chain.futures_ltp if chain.futures_ltp is not None else chain.spot
    strikes = [row.strike for row in chain.rows]
    window = around_atm(strikes, chain.atm, span)
    expiry = chain.expiry.isoformat() if chain.expiry else None
    by_strike = {row.strike: row for row in chain.rows}
    sides = []
    for strike in window:
        row = by_strike.get(strike)
        if row is None:
            continue
        for kind, side in (("CE", row.ce), ("PE", row.pe)):
            if side is None or side.token is None:
                continue
            sides.append((strike, kind, side))
    tokens = [int(side.token) for _, _, side in sides]
    from app.services.engine import load_bars_for_tokens

    by_token = load_bars_for_tokens(db, tokens, interval)
    minute_by_token = by_token if interval == "minute" else load_bars_for_tokens(db, tokens, "minute")
    out = []
    for strike, kind, side in sides:
        token = int(side.token)
        result = _evaluate_known_side(
            db,
            runtime,
            config,
            now,
            kind,
            side,
            strike,
            expiry,
            interval,
            futures,
            load_bars,
            stale,
            "",
            backfill=False,
            bars=by_token.get(token) or [],
            minute_bars=minute_by_token.get(token) or [],
        )
        out.append(_candidate_from_result(config.underlying, result, chain.atm, threshold, price))
    return out


def _highest(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return max(rows, key=lambda row: (row.get("score_percent") or 0, row.get("passed_count") or 0, -abs(row.get("atm_distance") or 0)))


def _candidate_from_result(symbol: str, result, atm, threshold: float, price) -> dict:
    score = score_percent(result.passed_count, result.total_count)
    strike = result.strike
    distance = 0.0
    if strike is not None and atm is not None:
        distance = abs(float(strike) - float(atm))
    elif strike is not None and price is not None:
        distance = abs(float(strike) - float(price))
    return {
        "symbol": symbol,
        "option_type": result.option_type,
        "strike": strike,
        "expiry": result.expiry,
        "tradingsymbol": result.option_symbol,
        "token": result.token,
        "ltp": result.premium_ltp,
        "passed": result.passed_count,
        "total": result.total_count,
        "passed_count": result.passed_count,
        "total_count": result.total_count,
        "score_percent": score,
        "is_strategy_valid": result.passed,
        "failed_conditions": [item.name for item in result.conditions if not item.passed],
        "conditions": [item.as_dict() for item in result.conditions],
        "atm_distance": distance,
        "label": candidate_label(passed=result.passed, score=score, threshold=threshold),
    }


def _side_result(
    db,
    runtime,
    config: OptionBuyConfig,
    now: datetime,
    chain,
    kind: str,
    method: str,
    offset: int,
    price: float | None,
    expiry: str | None,
    interval: str,
    futures,
    load_bars,
    ensure_today_minutes,
    stale: bool,
):
    strike, side, why = _pick_contract(chain, kind, method, offset, price)
    if side is None or side.token is None:
        result = empty_result(kind, config, why or f"Selected {kind} contract is unavailable", now)
        result.expiry = expiry
        result.strike = strike
        return _block_if_stale(result, stale)
    return _evaluate_known_side(
        db, runtime, config, now, kind, side, strike, expiry, interval, futures, load_bars, stale, why, backfill=True,
        ensure_today_minutes=ensure_today_minutes,
    )


def _evaluate_known_side(
    db,
    runtime,
    config: OptionBuyConfig,
    now,
    kind: str,
    side,
    strike,
    expiry,
    interval,
    futures,
    load_bars,
    stale: bool,
    why: str,
    *,
    backfill: bool,
    ensure_today_minutes=None,
    bars=None,
    minute_bars=None,
):
    if backfill and ensure_today_minutes is not None and side.token is not None:
        ensure_today_minutes(runtime, db, int(side.token))
    if bars is None:
        bars = load_bars(db, int(side.token), interval)
    if minute_bars is None:
        minute_bars = bars if interval == "minute" else load_bars(db, int(side.token), "minute")
    quality = why
    if interval != "minute" and not bars:
        quality = "; ".join(part for part in (quality, f"No {interval} premium candles; minute bars are not substituted") if part)
    quote = runtime.cache.get(int(side.token))
    option = option_premium_state(
        bars,
        minute_bars,
        config,
        symbol=side.tradingsymbol,
        expiry=expiry,
        strike=strike,
        option_type=kind,
        token=int(side.token),
        live_ltp=quote.get("ltp") if quote else side.ltp,
        live_oi=quote.get("oi") if quote else side.oi,
        quality=quality,
        session_date=now.date() if getattr(now, "date", None) else None,
    )
    if kind == "CE":
        result = evaluate_ce_buy_strategy(option, futures, config, now)
    else:
        result = evaluate_pe_buy_strategy(option, futures, config, now)
    return _block_if_stale(result, stale)


def _block_if_stale(result, stale: bool):
    if not stale:
        return result
    extra = "Live quotes are stale; BUY SETUP is blocked."
    result.passed = False
    result.status = "NO TRADE"
    result.data_quality = extra if not result.data_quality else f"{result.data_quality}; {extra}"
    return result


def _pick_contract(chain, option_type: str, method: str, strike_offset: int, price: float | None):
    """Strike selection only. Liquidity gates do not hide the contract from evaluation."""
    indexed = [(row.strike, row.ce if option_type == "CE" else row.pe) for row in chain.rows]
    indexed = [(strike, side) for strike, side in indexed if side is not None]
    if not indexed:
        return None, None, "no contracts in the chain window"
    strikes = [strike for strike, _ in indexed]
    center = atm_strike(strikes, price) if price is not None else strikes[len(strikes) // 2]
    if center is None:
        return None, None, "ATM strike is unavailable"
    ordered = sorted(strikes)
    center_index = ordered.index(center)
    if method == "atm":
        chosen = center
    elif method in {"atm_plus", "atm_minus"}:
        step = strike_offset or 1
        delta = step if method == "atm_plus" else -step
        target = center_index + delta
        if target < 0 or target >= len(ordered):
            return None, None, "strike offset is outside the chain window"
        chosen = ordered[target]
    elif method == "nearest_itm":
        if price is None:
            return None, None, "spot is required for ITM selection"
        candidates = [s for s in ordered if (s < price if option_type == "CE" else s > price)]
        if not candidates:
            return None, None, "no ITM strike in the window"
        chosen = min(candidates, key=lambda s: abs(s - price))
    elif method == "nearest_otm":
        if price is None:
            return None, None, "spot is required for OTM selection"
        candidates = [s for s in ordered if (s > price if option_type == "CE" else s < price)]
        if not candidates:
            return None, None, "no OTM strike in the window"
        chosen = min(candidates, key=lambda s: abs(s - price))
    else:
        return None, None, f"unknown selection method {method}"
    side = next(item for strike, item in indexed if strike == chosen)
    note = ""
    if side.ltp is None:
        note = "Selected contract premium LTP is unavailable"
    return chosen, side, note

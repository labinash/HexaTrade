from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta


DEFAULT_PRIORITY = [
    "EMERGENCY",
    "STOP_LOSS",
    "STRATEGY_INVALIDATED",
    "TARGET",
    "TRAILING_STOP",
    "MAX_HOLD",
    "TIME_EXIT",
]


@dataclass
class ExitPolicyParams:
    stop_enabled: bool = True
    stop_style: str = "points"
    stop_points: float = 10
    stop_percent: float = 20
    stop_atr_mult: float = 1
    target_enabled: bool = True
    target_style: str = "rr"
    target_points: float = 20
    target_percent: float = 40
    target_rr: float = 2
    trail_enabled: bool = False
    trail_style: str = "points"
    trail_points: float = 8
    trail_percent: float = 10
    trail_atr_mult: float = 1
    trail_arm_fraction: float = 0.5
    premium_trend_exit: bool = True
    reversal_ema: bool = True
    reversal_vwap: bool = True
    reversal_rsi: bool = False
    time_exit_enabled: bool = True
    time_exit: time = time(15, 15)
    max_hold_enabled: bool = False
    max_hold_minutes: int = 60
    priority: tuple[str, ...] = tuple(DEFAULT_PRIORITY)


@dataclass
class PositionState:
    entry_price: float
    entry_time: datetime
    stop_loss: float | None
    target: float | None
    trail_price: float | None
    atr_at_entry: float | None
    mfe_price: float | None = None


@dataclass
class ExitContext:
    premium: float | None
    now: datetime
    clock: time
    close: float | None
    ema: float | None
    vwap: float | None
    rsi: float | None
    rsi_min: float
    live_atr: float | None
    kill_switch: bool
    daily_loss_hit: bool
    data_stale: bool
    # The direction the position was taken in. Reversal tests mirror for BEARISH.
    direction: str = "BULLISH"
    rsi_max: float = 100.0
    premium_ema: float | None = None


def initial_levels(entry: float, atr_value: float | None, policy: ExitPolicyParams) -> tuple[float | None, float | None]:
    stop = None
    if policy.stop_enabled:
        if policy.stop_style == "percent":
            stop = entry * (1 - policy.stop_percent / 100)
        elif policy.stop_style == "atr" and atr_value is not None:
            stop = entry - atr_value * policy.stop_atr_mult
        else:
            stop = entry - policy.stop_points
    target = None
    if policy.target_enabled:
        risk = (entry - stop) if stop is not None else policy.stop_points
        if policy.target_style == "percent":
            target = entry * (1 + policy.target_percent / 100)
        elif policy.target_style == "rr":
            target = entry + max(risk, 0) * policy.target_rr
        else:
            target = entry + policy.target_points
    return stop, target


def trail_is_armed(state: PositionState, premium: float, policy: ExitPolicyParams) -> bool:
    """Trailing starts only after the configured share of the target distance."""
    if not policy.trail_enabled:
        return False
    if state.target is None or policy.trail_arm_fraction <= 0:
        return True
    span = state.target - state.entry_price
    if span <= 0:
        return False
    return premium >= state.entry_price + span * policy.trail_arm_fraction


def advance_trail(state: PositionState, premium: float, policy: ExitPolicyParams, live_atr: float | None) -> float | None:
    if not policy.trail_enabled:
        return state.trail_price
    if not trail_is_armed(state, premium, policy):
        return state.trail_price
    peak = premium if state.mfe_price is None else max(state.mfe_price, premium)
    atr_value = live_atr if policy.trail_style == "atr" else state.atr_at_entry
    if policy.trail_style == "percent":
        distance = peak * policy.trail_percent / 100
    elif policy.trail_style == "atr" and atr_value is not None:
        distance = atr_value * policy.trail_atr_mult
    else:
        distance = policy.trail_points
    candidate = peak - distance
    if state.trail_price is None:
        return candidate
    return max(state.trail_price, candidate)


def choose_exit(state: PositionState, ctx: ExitContext, policy: ExitPolicyParams) -> str | None:
    """Return the first matching reason in the saved priority. Stale data blocks protective fills."""
    if ctx.data_stale:
        return None
    invalidated = _invalidated(ctx, policy, state)
    held = ctx.now - state.entry_time
    matches = {
        "EMERGENCY": ctx.kill_switch or ctx.daily_loss_hit,
        "STOP_LOSS": policy.stop_enabled and state.stop_loss is not None and ctx.premium is not None and ctx.premium <= state.stop_loss,
        "STRATEGY_INVALIDATED": invalidated,
        "TARGET": policy.target_enabled and state.target is not None and ctx.premium is not None and ctx.premium >= state.target,
        "TRAILING_STOP": policy.trail_enabled and state.trail_price is not None and ctx.premium is not None and ctx.premium <= state.trail_price,
        "MAX_HOLD": policy.max_hold_enabled and held >= timedelta(minutes=policy.max_hold_minutes),
        "TIME_EXIT": policy.time_exit_enabled and ctx.clock >= policy.time_exit,
    }
    for name in policy.priority:
        if matches.get(name):
            return name
    return None


def _invalidated(ctx: ExitContext, policy: ExitPolicyParams, state: PositionState | None = None) -> bool:
    """Exit only on a real reversal, not EMA noise of a few points.

    VWAP is the session bias. A PE is not reversed while futures remain below
    VWAP even if they poke above EMA20. Premium-vs-EMA20 does not cut a winner.
    """
    bearish = ctx.direction == "BEARISH"
    vwap_against = False
    if policy.reversal_vwap and ctx.close is not None and ctx.vwap is not None:
        vwap_against = (ctx.close > ctx.vwap) if bearish else (ctx.close < ctx.vwap)
    ema_against = False
    if policy.reversal_ema and ctx.close is not None and ctx.ema is not None:
        ema_against = (ctx.close >= ctx.ema) if bearish else (ctx.close <= ctx.ema)
    still_with_vwap = False
    if ctx.close is not None and ctx.vwap is not None:
        still_with_vwap = (ctx.close < ctx.vwap) if bearish else (ctx.close > ctx.vwap)
    if vwap_against:
        index_reversal = True
    elif ema_against and still_with_vwap:
        index_reversal = False
    else:
        index_reversal = ema_against

    premium_exit = False
    if policy.premium_trend_exit and ctx.premium is not None and ctx.premium_ema is not None:
        below_ema = ctx.premium < ctx.premium_ema
        if state is not None:
            premium_exit = below_ema and ctx.premium <= state.entry_price
        else:
            premium_exit = below_ema
    if index_reversal or premium_exit:
        return True
    if policy.reversal_rsi and ctx.rsi is not None:
        if (ctx.rsi > ctx.rsi_max) if bearish else (ctx.rsi < ctx.rsi_min):
            return True
    return False

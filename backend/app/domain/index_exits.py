"""Index-futures structural exits. Paper / alert only. No broker orders.

Applied from:
- historical_backtest._walk_instrument (proxy BUY/SELL)
- paper_engine._manage_open (INDEX paper)
- index_hold_exit (delegates time/stop/target, then this module)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time


PARTIAL_1R = "PARTIAL_1R"
STRUCTURAL_STOP = "STOP_LOSS_HIT"
PREMIUM_STOP = "PREMIUM_STOP"
CHANDELIER_TRAIL = "TRAILING_STOP_HIT"
EMA_TRAIL = "EMA20_TRAIL"
FAILED_BREAKOUT = "FAILED_BREAKOUT"
VWAP_RECROSS = "VWAP_RECROSS"
OPPOSITE_SIGNAL = "OPPOSITE_SIGNAL"
TIME_STOP = "TIME_STOP"
FORCE_SQUARE = "FORCE_SQUARE_OFF"

EXIT_VARIANTS = ("fixed_rr", "partial_trail", "opposite_signal")


@dataclass
class IndexExitState:
    side: str
    entry: float
    stop: float | None
    target: float | None
    trail: float | None
    atr: float | None
    risk: float
    bars_held: int
    remaining_qty: int
    initial_qty: int
    partial_taken: bool
    breakout_level: float | None
    mfe: float


@dataclass
class IndexExitMarket:
    high: float
    low: float
    close: float
    ema20: float | None
    vwap: float | None
    premium: float | None
    premium_entry: float | None
    clock: time
    square: time
    expiry_square: time
    is_expiry: bool
    is_entry_bar: bool
    end_of_walk: bool
    end_of_session: bool
    opposite_score: float | None
    opposite_threshold: float
    htf_5_close_vs_ema: str | None = None


def clamp_atr_stop(atr_value: float | None, mult: float, lo: float, hi: float) -> float | None:
    if atr_value is None or atr_value <= 0:
        return None
    used = min(max(float(mult), float(lo)), float(hi))
    return atr_value * used


def structural_levels(entry: float, side: str, atr_value: float | None, *, atr_mult: float, atr_min: float, atr_max: float, target_rr: float) -> tuple[float | None, float | None, float]:
    risk = clamp_atr_stop(atr_value, atr_mult, atr_min, atr_max)
    if risk is None:
        return None, None, 0.0
    if str(side or "").upper() == "BUY":
        return entry - risk, entry + risk * target_rr, risk
    return entry + risk, entry - risk * target_rr, risk


def signed_r(side: str, entry: float, mark: float, risk: float) -> float | None:
    if risk <= 0:
        return None
    if str(side or "").upper() == "BUY":
        return (mark - entry) / risk
    return (entry - mark) / risk


def chandelier_stop(side: str, extreme: float, atr_value: float | None, mult: float) -> float | None:
    if atr_value is None:
        return None
    if str(side or "").upper() == "BUY":
        return extreme - atr_value * mult
    return extreme + atr_value * mult


def evaluate_index_exit(
    state: IndexExitState,
    market: IndexExitMarket,
    *,
    variant: str = "partial_trail",
    premium_stop_percent: float = 35,
    partial_at_r: float = 1.0,
    chandelier_mult: float = 2.0,
    time_stop_bars: int = 20,
    time_stop_min_r: float = 0.5,
    failed_breakout_bars: int = 3,
) -> tuple[str | None, dict]:
    """Return (reason, extras). extras may include partial=True and new_stop."""
    extras: dict = {}
    side = str(state.side or "").upper()
    buy = side == "BUY"
    if market.is_entry_bar:
        if market.end_of_walk:
            return "END_OF_DATA", extras
        return None, extras

    if not market.is_entry_bar:
        if buy:
            stop_hit = state.stop is not None and market.low <= state.stop
            target_hit = state.target is not None and market.high >= state.target
        else:
            stop_hit = state.stop is not None and market.high >= state.stop
            target_hit = state.target is not None and market.low <= state.target
        if stop_hit:
            return STRUCTURAL_STOP, extras
        if variant == "fixed_rr" and target_hit:
            return "TARGET_HIT", extras

    if market.premium is not None and market.premium_entry not in (None, 0):
        drop = (float(market.premium_entry) - float(market.premium)) / float(market.premium_entry) * 100
        if drop >= float(premium_stop_percent):
            return PREMIUM_STOP, extras

    r_now = signed_r(side, state.entry, market.close, state.risk)
    if variant == "partial_trail" and not state.partial_taken and r_now is not None and r_now >= float(partial_at_r) and state.remaining_qty > 1:
        extras["partial"] = True
        extras["partial_qty"] = max(1, state.remaining_qty // 2)
        extras["new_stop"] = state.entry
        return PARTIAL_1R, extras

    if variant == "partial_trail" and state.partial_taken:
        trail = chandelier_stop(side, state.mfe if buy else (state.mfe if state.mfe else market.low), state.atr, chandelier_mult)
        if trail is not None:
            extras["new_trail"] = trail
            if buy and market.low <= trail:
                return CHANDELIER_TRAIL, extras
            if not buy and market.high >= trail:
                return CHANDELIER_TRAIL, extras
        if market.htf_5_close_vs_ema == ("SELL" if buy else "BUY"):
            return EMA_TRAIL, extras

    if variant != "fixed_rr" and state.breakout_level is not None and state.bars_held <= int(failed_breakout_bars):
        failed = market.close < state.breakout_level if buy else market.close > state.breakout_level
        if failed:
            return FAILED_BREAKOUT, extras

    if variant != "fixed_rr" and market.vwap is not None:
        recross = market.close < market.vwap if buy else market.close > market.vwap
        if recross and state.bars_held > 0:
            return VWAP_RECROSS, extras

    if variant in {"opposite_signal", "partial_trail"} and market.opposite_score is not None:
        if float(market.opposite_score) >= float(market.opposite_threshold):
            return OPPOSITE_SIGNAL, extras

    if time_stop_bars > 0 and state.bars_held >= int(time_stop_bars):
        if r_now is None or r_now < float(time_stop_min_r):
            return TIME_STOP, extras

    square = market.expiry_square if market.is_expiry else market.square
    if market.clock >= square or market.end_of_session:
        return FORCE_SQUARE, extras
    if market.end_of_walk:
        return "END_OF_DATA", extras
    return None, extras

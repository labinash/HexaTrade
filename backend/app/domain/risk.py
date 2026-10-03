from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time


@dataclass
class RiskContext:
    mode: str
    kill_switch: bool
    data_stale: bool
    token_valid: bool
    now: time
    session_start: time
    session_end: time
    entry_start: time
    entry_stop: time
    square_off: time
    mis_cutoff: time
    daily_pnl: float
    max_daily_loss: float
    trades_today: int
    max_trades_per_day: int
    open_positions: int
    max_open_positions: int
    exposure: float
    max_exposure: float
    quantity: int
    lot_size: int
    max_quantity: int
    capital_required: float
    max_capital: float
    spread: float | None
    max_spread: float | None
    option_volume: int
    min_option_volume: int
    option_oi: int
    min_option_oi: int
    exchange: str
    expected_exchange: str
    product: str
    underlying_trades: int
    max_trades_per_underlying: int
    cooldown_active: bool
    duplicate_signal: bool
    redis_ok: bool
    database_ok: bool


@dataclass
class RiskDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def orders_allowed(mode: str) -> bool:
    return mode in {"PAPER", "LIVE"}


def evaluate_risk(ctx: RiskContext) -> RiskDecision:
    reasons: list[str] = []
    if not orders_allowed(ctx.mode):
        reasons.append("trading mode does not place orders")
    if ctx.kill_switch:
        reasons.append("kill switch is on")
    if not ctx.database_ok:
        reasons.append("database is unavailable")
    if ctx.mode == "LIVE" and not ctx.redis_ok:
        reasons.append("redis is unavailable")
    if ctx.data_stale:
        reasons.append("market data is stale")
    if ctx.mode == "LIVE" and not ctx.token_valid:
        reasons.append("broker session is not valid")
    if not (ctx.session_start <= ctx.now <= ctx.session_end):
        reasons.append("market is closed")
    if not (ctx.entry_start <= ctx.now <= ctx.entry_stop):
        reasons.append("outside the entry window")
    if ctx.now >= ctx.square_off:
        reasons.append("past forced square-off")
    if ctx.square_off > ctx.session_end or ctx.square_off < ctx.session_start:
        reasons.append("forced square-off is outside the exchange session")
    if ctx.mis_cutoff > ctx.session_end:
        reasons.append("broker MIS cutoff is outside the exchange session")
    if ctx.now >= ctx.mis_cutoff and ctx.product == "MIS":
        reasons.append("past the broker MIS cutoff")
    if ctx.daily_pnl <= -abs(ctx.max_daily_loss):
        reasons.append("daily loss limit reached")
    if ctx.trades_today >= ctx.max_trades_per_day:
        reasons.append("maximum trades for the day reached")
    if ctx.open_positions >= ctx.max_open_positions:
        reasons.append("maximum open positions reached")
    if ctx.underlying_trades >= ctx.max_trades_per_underlying:
        reasons.append("maximum trades for this underlying reached")
    if ctx.exposure + ctx.capital_required > ctx.max_exposure:
        reasons.append("maximum exposure exceeded")
    if ctx.capital_required > ctx.max_capital:
        reasons.append("maximum capital per trade exceeded")
    if ctx.quantity > ctx.max_quantity:
        reasons.append("maximum quantity exceeded")
    if ctx.lot_size <= 0 or ctx.quantity % ctx.lot_size != 0:
        reasons.append("quantity is not a multiple of lot size")
    if ctx.exchange != ctx.expected_exchange:
        reasons.append("exchange does not match the underlying profile")
    if ctx.max_spread is not None and ctx.spread is not None and ctx.spread > ctx.max_spread:
        reasons.append("spread is above the maximum")
    if ctx.option_volume < ctx.min_option_volume:
        reasons.append("option volume is below the minimum")
    if ctx.option_oi < ctx.min_option_oi:
        reasons.append("option OI is below the minimum")
    if ctx.cooldown_active:
        reasons.append("cooldown is active")
    if ctx.duplicate_signal:
        reasons.append("duplicate signal")
    return RiskDecision(allowed=not reasons, reasons=reasons)

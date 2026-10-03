from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from app.db.models import ExitPolicy, OptionSelectionRule, RiskPolicy, StrategyParameters, UnderlyingProfile
from app.domain.exits import ExitPolicyParams
from app.domain.option_buy import OptionBuyConfig
from app.domain.strategy import StrategyParams


def f(value) -> float | None:
    if value is None:
        return None
    return float(value)


def params_of(row: StrategyParameters, lot_size: int = 1) -> StrategyParams:
    return StrategyParams(
        direction=row.direction,
        ema_period=row.ema_period,
        ema_enabled=row.ema_enabled,
        momentum_time=_clock(row.momentum_time),
        momentum_min_points=float(row.momentum_min_points),
        momentum_min_percent=float(row.momentum_min_percent),
        momentum_unit=row.momentum_unit,
        oi_time=_clock(row.oi_time),
        oi_min_change=float(row.oi_min_change),
        oi_unit=row.oi_unit,
        volume_ema_period=row.volume_ema_period,
        volume_enabled=row.volume_enabled,
        vwap_enabled=row.vwap_enabled,
        rsi_period=row.rsi_period,
        rsi_min=float(row.rsi_min),
        rsi_max=float(row.rsi_max),
        rsi_min_bearish=float(row.rsi_min_bearish),
        rsi_max_bearish=float(row.rsi_max_bearish),
        rsi_enabled=row.rsi_enabled,
        atr_period=row.atr_period,
        atr_enabled=row.atr_enabled,
        candle_bullish_required=row.candle_bullish_required,
        time_start=_clock(row.time_start),
        time_end=_clock(row.time_end),
        time_enabled=row.time_enabled,
        timeframe_minutes=row.timeframe_minutes,
        evaluate_on=row.evaluate_on,
        confirmation_mode=row.confirmation_mode,
        min_conditions=row.min_conditions,
        price_source=row.price_source,
        oi_source=row.oi_source,
        bullish_option_type=row.bullish_option_type,
        bearish_option_type=row.bearish_option_type,
        lot_size=lot_size,
    )


def option_buy_config_of(row: StrategyParameters, profile: UnderlyingProfile | None = None) -> OptionBuyConfig:
    return OptionBuyConfig(
        underlying=row.underlying,
        ema_period=row.ema_period,
        momentum_time=_clock(row.momentum_time),
        midday_time=_clock(getattr(row, "pe_midday_time", None) or time(11, 0)),
        premium_momentum_percent=float(getattr(row, "option_momentum_percent", 3) or 3),
        pe_oi_since_midday=float(getattr(row, "pe_oi_since_midday", 10000) or 10000),
        volume_ema_period=row.volume_ema_period,
        rsi_period=row.rsi_period,
        ce_rsi_min=float(row.rsi_min),
        ce_rsi_max=float(row.rsi_max),
        pe_rsi_min=float(row.rsi_min_bearish),
        pe_rsi_max=float(row.rsi_max_bearish),
        atr_period=row.atr_period,
        atr_threshold=f(profile.atr_threshold) if profile is not None else None,
        time_start=_clock(row.time_start),
        time_end=_clock(row.time_end),
        timeframe_minutes=row.timeframe_minutes,
        candidate_strike_range=int(getattr(row, "candidate_strike_range", 5) or 5),
        min_candidate_score_percent=float(getattr(row, "min_candidate_score_percent", 60) or 60),
    )


def exit_of(row: ExitPolicy) -> ExitPolicyParams:
    priority = tuple(part.strip() for part in row.priority.split(",") if part.strip())
    return ExitPolicyParams(
        stop_enabled=row.stop_enabled,
        stop_style=row.stop_style,
        stop_points=float(row.stop_points),
        stop_percent=float(row.stop_percent),
        stop_atr_mult=float(row.stop_atr_mult),
        target_enabled=row.target_enabled,
        target_style=row.target_style,
        target_points=float(row.target_points),
        target_percent=float(row.target_percent),
        target_rr=float(row.target_rr),
        trail_enabled=row.trail_enabled,
        trail_style=row.trail_style,
        trail_points=float(row.trail_points),
        trail_percent=float(row.trail_percent),
        trail_atr_mult=float(row.trail_atr_mult),
        trail_arm_fraction=float(getattr(row, "trail_arm_fraction", 0.5) or 0.5),
        premium_trend_exit=bool(getattr(row, "premium_trend_exit", True)),
        reversal_ema=row.reversal_ema,
        reversal_vwap=row.reversal_vwap,
        reversal_rsi=row.reversal_rsi,
        time_exit_enabled=row.time_exit_enabled,
        time_exit=_clock(row.time_exit),
        max_hold_enabled=row.max_hold_enabled,
        max_hold_minutes=row.max_hold_minutes,
        priority=priority,
    )


def assign(row, data: dict, allowed: set[str]) -> None:
    for key, value in data.items():
        if key in allowed and value is not None:
            setattr(row, key, value)


def _clock(value: time) -> time:
    return value.replace(second=0, microsecond=0)


def jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return value

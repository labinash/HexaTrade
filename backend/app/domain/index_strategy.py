"""Independent BUY and SELL index-futures scores. Reuses ema/rsi/atr/vwap. Never looks ahead."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal

from app.core.clock import to_ist
from app.domain.dashboard_brief import implied_market_direction
from app.domain.index_exits import (
    EXIT_VARIANTS,
    evaluate_index_exit,
    structural_levels,
)
from app.domain.indicators import adx, atr, ema, median, rsi, session_vwap
from app.domain.paper_auto import PNL_FORMULA, futures_gross, futures_net, slippage_cost


SAME_CANDLE_RULE = (
    "Signals fire at candle close. Stop/target are not checked on the entry candle "
    "(that bar's high/low already happened). On later bars, if both stop and target "
    "would be touched on the same candle, STOP_LOSS_HIT is taken first (conservative)."
)

INDEX_PAPER_MINUTES = 3
INDEX_CONFIRM_MINUTES = 5

ENTRY_VALIDATION_RULES = (
    "Mandatory gates: trigger (20-bar breakout or VWAP/EMA20 pullback hold), 5m & 15m trend not opposing, proximity ≤ 1.5 ATR, ADX14≥18 or ATR above time-of-day median, session 09:30–14:45 (expiry buyers 09:30–13:30), premium candle+VWAP when premium exists, spread OK, R:R≥1.5, IV%≤80 on buys.",
    "Score groups: Trend 25, Trigger 20, OI/tape 15, Momentum 10, RelVolume 10, RSI 10, Premium 10. Single-bar close-to-close is display-only. Implied EMA/VWAP tape is display-only; OI buildup is scored.",
    "BUY and SELL scores are independent. Any failed gate is HOLD and the failed gate is named.",
    "After STOP_LOSS_HIT, 10-bar cooldown. Daily halt after 2 losses or −2R, or −max_daily_loss.",
    "5m and 15m trend use closed higher-TF EMA20 only. No lookahead.",
)


@dataclass
class IndexStrategyConfig:
    name: str = "Balanced"
    preset: str = "BALANCED"
    version: str = "1"
    buy_enabled: bool = True
    sell_enabled: bool = True
    buy_threshold: float = 80
    sell_threshold: float = 80
    ema_period: int = 20
    volume_ema_period: int = 20
    rsi_period: int = 14
    atr_period: int = 14
    vwap_enabled: bool = True
    volume_enabled: bool = True
    breakout_enabled: bool = True
    tape_enabled: bool = True
    rsi_enabled: bool = True
    breakout_lookback: int = 20
    buy_rsi_min: float = 50
    buy_rsi_max: float = 70
    sell_rsi_min: float = 30
    sell_rsi_max: float = 50
    stop_style: str = "atr"
    stop_percent: float = 0.35
    stop_points: float = 40
    stop_atr_mult: float = 1.0
    stop_atr_min: float = 0.5
    stop_atr_max: float = 1.2
    target_style: str = "rr"
    target_percent: float = 0.7
    target_points: float = 80
    target_rr: float = 1.5
    trail_enabled: bool = True
    trail_percent: float = 0.2
    max_hold_minutes: int = 180
    cooldown_minutes: int = 30
    cooldown_bars: int = 10
    max_trades_per_day: int = 2
    session_start: str = "09:30"
    session_end: str = "14:45"
    expiry_session_end: str = "13:30"
    square_off: str = "15:15"
    expiry_square_off: str = "14:30"
    max_daily_loss: float = 5000
    daily_loss_count: int = 2
    daily_loss_r: float = 2.0
    slippage_points: float = 0.5
    lots: int = 1
    allow_hedge: bool = False
    allow_reentry: bool = False
    no_reentry_after_stop: bool = True
    confirm_higher_tf: bool = True
    confirm_minutes: int = 5
    confirm_minutes_15: int = 15
    proximity_atr_mult: float = 1.5
    adx_period: int = 14
    adx_min: float = 18
    atr_median_days: int = 20
    rel_volume_min: float = 1.2
    rr_min: float = 1.5
    iv_pct_max: float = 80
    premium_stop_percent: float = 35
    partial_at_r: float = 1.0
    partial_fraction: float = 0.5
    chandelier_atr_mult: float = 2.0
    time_stop_bars: int = 20
    time_stop_min_r: float = 0.5
    failed_breakout_bars: int = 3
    exit_variant: str = "partial_trail"
    weight_trend: float = 25
    weight_trigger: float = 20
    weight_oi: float = 15
    weight_ema: float = 20
    weight_vwap: float = 15
    weight_momentum: float = 10
    weight_volume: float = 10
    weight_rsi: float = 10
    weight_breakout: float = 15
    weight_tape: float = 15
    weight_premium: float = 10

    def as_dict(self) -> dict:
        return asdict(self)


PRESETS = {
    "CONSERVATIVE": IndexStrategyConfig(
        name="Conservative",
        preset="CONSERVATIVE",
        buy_threshold=80,
        sell_threshold=80,
        stop_percent=0.25,
        target_rr=2,
        trail_enabled=True,
        max_trades_per_day=2,
        cooldown_minutes=20,
        max_hold_minutes=120,
    ),
    "BALANCED": IndexStrategyConfig(
        name="Balanced",
        preset="BALANCED",
        buy_threshold=80,
        sell_threshold=80,
        cooldown_minutes=30,
        cooldown_bars=10,
        max_trades_per_day=2,
        session_start="09:30",
        session_end="14:45",
        no_reentry_after_stop=True,
        confirm_higher_tf=True,
        confirm_minutes=5,
        confirm_minutes_15=15,
        exit_variant="partial_trail",
    ),
    "AGGRESSIVE": IndexStrategyConfig(
        name="Aggressive",
        preset="AGGRESSIVE",
        buy_threshold=60,
        sell_threshold=60,
        stop_percent=0.5,
        target_rr=1.2,
        trail_enabled=False,
        max_trades_per_day=6,
        cooldown_minutes=5,
        volume_enabled=False,
        max_hold_minutes=240,
    ),
}


def config_from_dict(data: dict | None) -> IndexStrategyConfig:
    base = IndexStrategyConfig()
    if not data:
        return base
    preset = str(data.get("preset") or data.get("name") or "").upper()
    if preset in PRESETS and str(data.get("preset") or "").upper() != "CUSTOM":
        base = PRESETS[preset]
    fields = {key: getattr(base, key) for key in base.as_dict()}
    for key, value in data.items():
        if key in fields and value is not None and key not in {"version"}:
            if isinstance(fields[key], bool):
                fields[key] = bool(value)
            else:
                fields[key] = value
    fields["preset"] = str(data.get("preset") or fields["preset"] or "CUSTOM").upper()
    if fields["preset"] not in PRESETS:
        fields["preset"] = "CUSTOM"
        fields["name"] = str(data.get("name") or "Custom")
    if data.get("allow_reentry") is not None:
        fields["allow_reentry"] = bool(data.get("allow_reentry"))
        fields["no_reentry_after_stop"] = not fields["allow_reentry"]
    else:
        fields["allow_reentry"] = not bool(fields.get("no_reentry_after_stop", True))
    variant = str(fields.get("exit_variant") or "partial_trail")
    fields["exit_variant"] = variant if variant in EXIT_VARIANTS else "partial_trail"
    return IndexStrategyConfig(**fields)


def parse_clock(value: str, fallback: time) -> time:
    try:
        hour, minute = str(value).split(":")[:2]
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return fallback


def infer_walk_minutes(bars) -> int:
    if not bars or len(bars) < 2:
        return 1
    for previous, current in zip(bars, bars[1:]):
        try:
            if to_ist(previous.start).date() != to_ist(current.start).date():
                continue
            minutes = int(round((current.start - previous.start).total_seconds() / 60))
        except (TypeError, ValueError, AttributeError):
            continue
        if minutes > 0:
            return minutes
    return 1


def confirm_with_higher_tf(walk_minutes: int, config: IndexStrategyConfig) -> bool:
    return bool(config.confirm_higher_tf) and int(walk_minutes or 0) in {1, 3}


def bar_close_time(bar, minutes: int) -> datetime:
    return bar.start + timedelta(minutes=max(1, int(minutes)))


def closed_bars_asof(bars, asof: datetime, tf_minutes: int):
    """Bars whose close is at or before asof. Never includes a still-forming higher-TF candle."""
    out = []
    width = max(1, int(tf_minutes))
    for bar in bars:
        if bar_close_time(bar, width) <= asof:
            out.append(bar)
    return out


def higher_tf_decision(higher_bars, asof: datetime, config: IndexStrategyConfig, tf_minutes: int) -> str:
    visible = closed_bars_asof(higher_bars, asof, tf_minutes)
    need = max(int(config.ema_period or 20), int(config.breakout_lookback or 20)) + 2
    if len(visible) < need:
        return "HOLD"
    return htf_trend_side(visible, config)


def htf_trend_side(bars, config: IndexStrategyConfig) -> str:
    """Closed-bar EMA20 trend only. HOLD if flat or data missing. Not a second engine."""
    if not bars:
        return "HOLD"
    closes = [float(bar.close) for bar in bars]
    series = ema(closes, int(config.ema_period or 20))
    close = closes[-1]
    ema_now = series[-1]
    if close is None or ema_now is None:
        return "HOLD"
    if close > ema_now:
        return "BUY"
    if close < ema_now:
        return "SELL"
    return "HOLD"


def trend_opposes(side: str, htf: str | None) -> bool:
    wanted = str(side or "").upper()
    got = str(htf or "HOLD").upper()
    if wanted not in {"BUY", "SELL"} or got not in {"BUY", "SELL"}:
        return False
    return got != wanted


def validate_index_entry(
    *,
    side: str,
    clock: time,
    config: IndexStrategyConfig,
    day_trades: int,
    day_pnl,
    stopped_today: bool,
    last_entry_at: datetime | None = None,
    now: datetime | None = None,
    higher_tf_side: str | None = None,
    htf_5_side: str | None = None,
    htf_15_side: str | None = None,
    walk_minutes: int = 3,
    score: float | None = None,
    is_expiry: bool = False,
    day_losses: int = 0,
    day_r: float = 0,
    bars_since_stop: int | None = None,
) -> tuple[bool, str]:
    """Hard filters after the score is already a rising-edge BUY/SELL. Same engine. No broker orders."""
    start = parse_clock(config.session_start, time(9, 30))
    end = parse_clock(config.expiry_session_end if is_expiry else config.session_end, time(14, 45))
    if is_expiry:
        end = parse_clock(config.expiry_session_end, time(13, 30))
    if not (start <= clock <= end):
        label = "expiry buyer window" if is_expiry else "entry window"
        return False, f"Outside {label} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}."
    needed = float(config.buy_threshold if str(side or "").upper() == "BUY" else config.sell_threshold)
    if score is not None and float(score) < needed:
        return False, f"Score {score} below threshold {needed}."
    if config.no_reentry_after_stop and not config.allow_reentry and stopped_today:
        return False, "No re-entry after a stop today."
    limit = Decimal(str(config.max_daily_loss or 0))
    if limit > 0 and Decimal(str(day_pnl or 0)) <= -limit:
        return False, f"Daily loss halt. Session P&L {day_pnl} reached limit {config.max_daily_loss}."
    max_per_day = int(config.max_trades_per_day or 0)
    if max_per_day > 0 and day_trades >= max_per_day:
        return False, f"Max {max_per_day} trades/day."
    cool = int(config.cooldown_minutes or 0)
    if cool > 0 and last_entry_at is not None and now is not None:
        try:
            elapsed = (now - last_entry_at).total_seconds()
        except TypeError:
            elapsed = 0
        if elapsed < cool * 60:
            return False, f"Cooldown {cool} min still active."
    if int(config.daily_loss_count or 0) > 0 and int(day_losses or 0) >= int(config.daily_loss_count):
        return False, f"Daily stop: {day_losses} losses (limit {config.daily_loss_count})."
    if float(config.daily_loss_r or 0) > 0 and float(day_r or 0) <= -float(config.daily_loss_r):
        return False, f"Daily stop: session R {day_r} reached −{config.daily_loss_r}R."
    cool_bars = int(config.cooldown_bars or 0)
    if cool_bars > 0 and bars_since_stop is not None and bars_since_stop < cool_bars:
        return False, f"Stop cooldown {cool_bars} bars still active ({bars_since_stop})."
    five = htf_5_side if htf_5_side is not None else higher_tf_side
    fifteen = htf_15_side
    if confirm_with_higher_tf(walk_minutes, config) or config.confirm_higher_tf:
        if trend_opposes(side, five):
            return False, f"5m trend opposing ({five})."
        if trend_opposes(side, fifteen):
            return False, f"15m trend opposing ({fifteen})."
    return True, ""


@dataclass
class ConditionScore:
    name: str
    weight: float
    passed: bool
    enabled: bool
    current: str
    required: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class SideScore:
    side: str
    score: float
    threshold: float
    passed: bool
    conditions: list[ConditionScore] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    gates: list[ConditionScore] = field(default_factory=list)
    failed_gate: str | None = None

    def as_dict(self) -> dict:
        return {
            "side": self.side,
            "score": self.score,
            "threshold": self.threshold,
            "passed": self.passed,
            "conditions": [item.as_dict() for item in self.conditions],
            "rejected": self.rejected,
            "gates": [item.as_dict() for item in self.gates],
            "failed_gate": self.failed_gate,
        }


@dataclass
class IndexDecision:
    decision: str
    buy: SideScore
    sell: SideScore
    tape: str
    indicators: dict
    reason: str
    conflict: bool = False

    def as_dict(self) -> dict:
        return {
            "decision": self.decision,
            "buy_score": self.buy.score,
            "sell_score": self.sell.score,
            "buy": self.buy.as_dict(),
            "sell": self.sell.as_dict(),
            "tape": self.tape,
            "indicators": self.indicators,
            "reason": self.reason,
            "conflict": self.conflict,
            "same_candle_rule": SAME_CANDLE_RULE,
            "failed_gate": self.buy.failed_gate or self.sell.failed_gate,
        }


def features_from_bars(bars, config: IndexStrategyConfig, *, tape: str | None = None, extras: dict | None = None) -> dict:
    """Indicators from bars[:current] only. Current bar is the last item."""
    closes = [float(bar.close) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]
    volumes = [float(bar.volume or 0) for bar in bars]
    last = bars[-1]
    ema_series = ema(closes, config.ema_period)
    ema9 = ema(closes, 9)
    vol_ema = ema(volumes, config.volume_ema_period)
    rsi_series = rsi(closes, config.rsi_period)
    atr_series = atr(highs, lows, closes, config.atr_period)
    adx_series = adx(highs, lows, closes, int(config.adx_period or 14))
    typical = [(bar.high + bar.low + bar.close) / 3 for bar in bars]
    keys = [to_ist(bar.start).date() for bar in bars]
    vwap_series = session_vwap(typical, volumes, keys)
    close = closes[-1]
    ema_now = ema_series[-1]
    prior = closes[-2] if len(closes) > 1 else None
    momentum = (close - prior) if prior is not None else None
    momentum_3 = (close - closes[-4]) if len(closes) >= 4 else None
    ema9_now = ema9[-1]
    ema9_prev = ema9[-2] if len(ema9) > 1 else None
    ema9_slope = (ema9_now - ema9_prev) if ema9_now is not None and ema9_prev is not None else None
    lookback = max(2, int(config.breakout_lookback))
    prior_high = max(highs[-lookback - 1 : -1]) if len(highs) > lookback else (max(highs[:-1]) if len(highs) > 1 else None)
    prior_low = min(lows[-lookback - 1 : -1]) if len(lows) > lookback else (min(lows[:-1]) if len(lows) > 1 else None)
    implied = tape or implied_market_direction(close=close, ema=ema_now, vwap=vwap_series[-1], momentum=momentum)
    vol_now = volumes[-1]
    vol_base = vol_ema[-1]
    rel_volume = (vol_now / vol_base) if vol_base else None
    tod_median = _tod_atr_median(bars, atr_series, last.start, int(config.atr_median_days or 20))
    extras = dict(extras or {})
    return {
        "close": close,
        "high": highs[-1],
        "low": lows[-1],
        "open": float(last.open),
        "volume": vol_now,
        "ema": ema_now,
        "ema9": ema9_now,
        "ema9_slope": ema9_slope,
        "volume_ema": vol_base,
        "rel_volume": rel_volume,
        "rsi": rsi_series[-1],
        "atr": atr_series[-1],
        "adx": adx_series[-1],
        "atr_tod_median": tod_median,
        "vwap": vwap_series[-1],
        "momentum": momentum,
        "momentum_3": momentum_3,
        "prior_high": prior_high,
        "prior_low": prior_low,
        "tape": implied,
        "bar_time": last.start,
        "oi_change": extras.get("oi_change"),
        "htf_5_side": extras.get("htf_5_side"),
        "htf_15_side": extras.get("htf_15_side"),
        "premium_close": extras.get("premium_close"),
        "premium_vwap": extras.get("premium_vwap"),
        "premium_open": extras.get("premium_open"),
        "spread_ok": extras.get("spread_ok"),
        "iv_pct": extras.get("iv_pct"),
        "is_expiry": extras.get("is_expiry"),
        **{key: value for key, value in extras.items() if key not in {"oi_change", "htf_5_side", "htf_15_side"}},
    }


def _tod_atr_median(bars, atr_series, stamp, days: int) -> float | None:
    try:
        clock = to_ist(stamp).time().replace(second=0, microsecond=0)
    except Exception:
        return median([v for v in atr_series if v is not None][-20:])
    seen: dict[object, float] = {}
    for bar, value in zip(bars, atr_series):
        if value is None:
            continue
        local = to_ist(bar.start)
        if local.time().replace(second=0, microsecond=0) != clock:
            continue
        seen[local.date()] = value
    recent = list(seen.values())[-max(1, days):]
    return median(recent)


def score_side(side: str, facts: dict, config: IndexStrategyConfig) -> SideScore:
    bull = side == "BUY"
    close = facts.get("close")
    ema_now = facts.get("ema")
    vwap_now = facts.get("vwap")
    atr_now = facts.get("atr")
    rows: list[ConditionScore] = []
    ema_ok = close is not None and ema_now is not None and ((close > ema_now) if bull else (close < ema_now))
    vwap_ok = close is not None and vwap_now is not None and ((close > vwap_now) if bull else (close < vwap_now))
    htf5 = facts.get("htf_5_side")
    htf15 = facts.get("htf_15_side")
    htf_ok = not trend_opposes(side, htf5) and not trend_opposes(side, htf15)
    level = facts["prior_high"] if bull else facts["prior_low"]
    breakout = False
    if close is not None and level is not None:
        breakout = close >= level if bull else close <= level
    pullback = _pullback_hold(bull, facts)
    trigger = breakout or pullback
    mom_ok = (facts.get("momentum_3") is not None and ((facts["momentum_3"] > 0) if bull else (facts["momentum_3"] < 0))) or (
        facts.get("ema9_slope") is not None and ((facts["ema9_slope"] > 0) if bull else (facts["ema9_slope"] < 0))
    )
    rel = facts.get("rel_volume")
    vol_ok = rel is not None and rel > float(config.rel_volume_min)
    lo, hi = (config.buy_rsi_min, config.buy_rsi_max) if bull else (config.sell_rsi_min, config.sell_rsi_max)
    rsi_now = facts.get("rsi")
    rsi_ok = rsi_now is not None and lo <= rsi_now <= hi
    oi = facts.get("oi_change")
    oi_ok = oi is not None and ((oi > 0 and bull) or (oi > 0 and not bull) or (oi > 0))
    if oi is not None:
        oi_ok = oi > 0
    prem_c = facts.get("premium_close")
    prem_v = facts.get("premium_vwap")
    prem_o = facts.get("premium_open")
    prem_available = prem_c is not None
    prem_candle = prem_available and prem_o is not None and ((prem_c > prem_o) if bull else (prem_c < prem_o))
    prem_vwap_ok = prem_available and prem_v is not None and ((prem_c > prem_v) if bull else (prem_c < prem_v))
    prem_ok = (prem_candle and prem_vwap_ok) if prem_available else None
    stop, target, risk = structural_levels(
        float(close or 0),
        side,
        atr_now,
        atr_mult=config.stop_atr_mult,
        atr_min=config.stop_atr_min,
        atr_max=config.stop_atr_max,
        target_rr=config.target_rr,
    )
    rr = (abs(target - close) / risk) if target is not None and risk else None

    gates = _mandatory_gates(side, facts, config, trigger, pullback, breakout, rr, prem_ok)
    failed = next((item.name for item in gates if item.enabled and not item.passed), None)

    rows.append(_cond("Trend group", config.weight_trend, True, _group_frac([ema_ok, vwap_ok, htf_ok]), 1, _group_frac([ema_ok, vwap_ok, htf_ok]) >= 0.5, "EMA20+VWAP+HTF not aligned"))
    rows.append(_cond("Trigger quality", config.weight_trigger, True, "breakout" if breakout else "pullback" if pullback else "none", "breakout or pullback hold", trigger, "No 20-bar breakout or VWAP/EMA20 pullback hold"))
    rows.append(_cond("OI buildup", config.weight_oi, oi is not None, oi, "> 0", bool(oi_ok) if oi is not None else False, "OI buildup missing or not rising"))
    rows.append(_cond("Momentum 3-bar/EMA9", config.weight_momentum, True, facts.get("momentum_3") if facts.get("momentum_3") is not None else facts.get("ema9_slope"), "3-bar or EMA9 slope", mom_ok, "3-bar change or 5m EMA9 slope not with the side"))
    rows.append(_cond("RelVolume", config.weight_volume, config.volume_enabled, rel, f"> {config.rel_volume_min}", bool(vol_ok), f"Volume/EMA ≤ {config.rel_volume_min}"))
    rows.append(_cond("RSI", config.weight_rsi, config.rsi_enabled, rsi_now, f"{lo}–{hi}", rsi_ok, f"RSI{config.rsi_period} outside {lo}–{hi}"))
    rows.append(_cond("Premium confirmation", config.weight_premium, prem_available, prem_c, "session candle + premium VWAP", bool(prem_ok), "Premium candle/VWAP not confirming"))
    rows.append(_cond("EMA trend", 0, True, close, ema_now, ema_ok, f"Close {'>' if bull else '<'} EMA{config.ema_period}"))
    rows.append(_cond("VWAP", 0, config.vwap_enabled, close, vwap_now, vwap_ok, f"Close {'>' if bull else '<'} session VWAP"))
    rows.append(_cond("Breakout" if bull else "Breakdown", 0, config.breakout_enabled, close, level, breakout, f"Close {'>= prior high' if bull else '<= prior low'} of last {config.breakout_lookback} completed bars"))
    rows.append(_cond("Momentum (legacy close-to-close)", 0, True, facts.get("momentum"), "display only", False, "Single-bar close-to-close is not scored"))
    rows.append(_cond("Implied tape (legacy EMA/VWAP)", 0, config.tape_enabled, facts.get("tape"), "display only — OI scored instead", False, "Implied tape from EMA/VWAP is not scored"))

    enabled = [item for item in rows if item.enabled and item.weight > 0]
    weight_total = sum(item.weight for item in enabled) or 1
    earned = 0.0
    for item in enabled:
        if item.name == "Trend group":
            earned += item.weight * _group_frac([ema_ok, vwap_ok, htf_ok])
        else:
            earned += item.weight if item.passed else 0
    score = round(100.0 * earned / weight_total, 2)
    threshold = config.buy_threshold if bull else config.sell_threshold
    rejected = [item.name for item in enabled if not item.passed]
    passed = score >= threshold and failed is None
    return SideScore(side=side, score=score, threshold=threshold, passed=passed, conditions=rows, rejected=rejected, gates=gates, failed_gate=failed)


def _group_frac(flags: list[bool]) -> float:
    if not flags:
        return 0.0
    return sum(1 for item in flags if item) / len(flags)


def _pullback_hold(bull: bool, facts: dict) -> bool:
    close = facts.get("close")
    ema_now = facts.get("ema")
    vwap_now = facts.get("vwap")
    atr_now = facts.get("atr") or 0
    low = facts.get("low")
    high = facts.get("high")
    if close is None:
        return False
    band = max(atr_now * 0.25, 1e-9)
    touched = False
    for level in (ema_now, vwap_now):
        if level is None or low is None or high is None:
            continue
        if low <= level <= high or abs(close - level) <= band:
            touched = True
    held = (close > ema_now and (vwap_now is None or close > vwap_now)) if bull else (close < ema_now and (vwap_now is None or close < vwap_now))
    return bool(touched and held and ema_now is not None)


def _mandatory_gates(side, facts, config: IndexStrategyConfig, trigger, pullback, breakout, rr, prem_ok) -> list[ConditionScore]:
    bull = side == "BUY"
    close = facts.get("close")
    ema_now = facts.get("ema")
    vwap_now = facts.get("vwap")
    atr_now = facts.get("atr")
    adx_now = facts.get("adx")
    tod = facts.get("atr_tod_median")
    prox = float(config.proximity_atr_mult or 1.5)
    near_vwap = close is not None and vwap_now is not None and atr_now and abs(close - vwap_now) <= prox * atr_now
    near_ema = close is not None and ema_now is not None and atr_now and abs(close - ema_now) <= prox * atr_now
    vol_ok = (adx_now is not None and adx_now >= float(config.adx_min)) or (atr_now is not None and tod is not None and atr_now > tod)
    clock = None
    stamp = facts.get("bar_time")
    if stamp is not None:
        try:
            clock = to_ist(stamp).time().replace(second=0, microsecond=0)
        except Exception:
            clock = None
    start = parse_clock(config.session_start, time(9, 30))
    end = parse_clock(config.expiry_session_end if facts.get("is_expiry") else config.session_end, time(14, 45))
    time_ok = clock is None or start <= clock <= end
    spread_ok = facts.get("spread_ok")
    iv = facts.get("iv_pct")
    iv_ok = True if not bull or iv is None else iv <= float(config.iv_pct_max)
    rr_ok = rr is not None and rr >= float(config.rr_min)
    htf_ok = not trend_opposes(side, facts.get("htf_5_side")) and not trend_opposes(side, facts.get("htf_15_side"))
    prem_available = facts.get("premium_close") is not None
    return [
        _cond("Gate: trigger", 0, True, "breakout" if breakout else "pullback" if pullback else "none", "20-bar breakout or pullback hold", trigger, "No trigger"),
        _cond("Gate: 5m/15m not opposing", 0, True, f"{facts.get('htf_5_side')}/{facts.get('htf_15_side')}", "not opposing", htf_ok, "Higher-TF trend opposing"),
        _cond("Gate: proximity", 0, atr_now is not None, f"VWAP {near_vwap} EMA {near_ema}", f"|Δ| ≤ {prox}*ATR", bool(near_vwap and near_ema), "Close too far from VWAP or EMA20"),
        _cond("Gate: ADX/ATR median", 0, True, adx_now if adx_now is not None else atr_now, f"ADX≥{config.adx_min} or ATR>TOD median", bool(vol_ok), "ADX and ATR median both weak"),
        _cond("Gate: session time", 0, True, clock.strftime("%H:%M") if clock else "—", f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}", time_ok, "Outside entry window"),
        _cond("Gate: premium candle/VWAP", 0, prem_available, facts.get("premium_close"), "session + VWAP", bool(prem_ok) if prem_available else True, "Premium not confirming"),
        _cond("Gate: spread", 0, spread_ok is not None, spread_ok, "OK", spread_ok is not False, "Spread too wide"),
        _cond("Gate: R:R", 0, rr is not None, rr, f">= {config.rr_min}", rr_ok if rr is not None else True, f"R:R below {config.rr_min}"),
        _cond("Gate: IV%", 0, bull and iv is not None, iv, f"<= {config.iv_pct_max}", iv_ok, f"IV% above {config.iv_pct_max}"),
    ]


def decide(facts: dict, config: IndexStrategyConfig) -> IndexDecision:
    buy = score_side("BUY", facts, config)
    sell = score_side("SELL", facts, config)
    buy_ok = bool(config.buy_enabled and buy.passed)
    sell_ok = bool(config.sell_enabled and sell.passed)
    conflict = buy_ok and sell_ok
    if conflict and not config.allow_hedge:
        decision = "HOLD"
        reason = f"Conflict: BUY {buy.score} and SELL {sell.score} both at/above threshold. HOLD."
    elif buy_ok and (not sell_ok or buy.score > sell.score):
        decision = "BUY"
        reason = f"BUY score {buy.score} >= {buy.threshold}. SELL {sell.score}."
    elif sell_ok and (not buy_ok or sell.score > buy.score):
        decision = "SELL"
        reason = f"SELL score {sell.score} >= {sell.threshold}. BUY {buy.score}."
    else:
        decision = "HOLD"
        gates = [item for item in (buy.failed_gate, sell.failed_gate) if item]
        extra = f" Failed gate: {', '.join(dict.fromkeys(gates))}." if gates else ""
        reason = f"HOLD. BUY {buy.score}/{buy.threshold}, SELL {sell.score}/{sell.threshold}.{extra}"
    if not config.buy_enabled and decision == "BUY":
        decision, reason = "HOLD", "BUY strategy is off."
    if not config.sell_enabled and decision == "SELL":
        decision, reason = "HOLD", "SELL strategy is off."
    return IndexDecision(
        decision=decision,
        buy=buy,
        sell=sell,
        tape=str(facts.get("tape") or "NEUTRAL"),
        indicators={key: facts.get(key) for key in ("close", "ema", "vwap", "rsi", "atr", "adx", "momentum", "momentum_3", "volume", "rel_volume", "prior_high", "prior_low", "htf_5_side", "htf_15_side")},
        reason=reason,
        conflict=conflict,
    )


def _cond(name: str, weight: float, enabled: bool, current, required, passed: bool, need: str) -> ConditionScore:
    if current is None or required is None:
        return ConditionScore(name, weight, False, enabled, "unavailable", str(required), "DATA UNAVAILABLE")
    return ConditionScore(
        name=name,
        weight=weight,
        passed=bool(passed),
        enabled=enabled,
        current=_fmt(current),
        required=_fmt(required) if not isinstance(required, str) else required,
        reason="" if passed else need,
    )


def _fmt(value) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def apply_slippage(price: float, side: str, points: float) -> float:
    slip = abs(float(points or 0))
    if side == "BUY":
        return price + slip
    return price - slip


def index_exit_fill(side: str, reason: str | None, close: float, stop: float | None, target: float | None, slip_points: float = 0) -> float:
    """Stop/target fills at the level, not the bar close. Cover side pays slippage."""
    mapped = map_exit_reason(reason)
    if mapped == "STOP_LOSS_HIT" and stop is not None:
        raw = float(stop)
    elif mapped == "TARGET_HIT" and target is not None:
        raw = float(target)
    else:
        raw = float(close)
    cover = "SELL" if str(side or "").upper() == "BUY" else "BUY"
    return apply_slippage(raw, cover, slip_points)


def levels_for(entry: float, side: str, config: IndexStrategyConfig, atr_value: float | None) -> tuple[float | None, float | None]:
    if str(config.stop_style or "").lower() == "atr":
        stop, target, _risk = structural_levels(
            entry,
            side,
            atr_value,
            atr_mult=config.stop_atr_mult,
            atr_min=config.stop_atr_min,
            atr_max=config.stop_atr_max,
            target_rr=float(config.target_rr or 1.5),
        )
        return stop, target
    if config.stop_style == "points":
        risk = config.stop_points
    else:
        risk = entry * config.stop_percent / 100
    if config.target_style == "percent":
        reward = entry * config.target_percent / 100
    elif config.target_style == "rr":
        reward = risk * config.target_rr
    else:
        reward = config.target_points
    if side == "BUY":
        return entry - risk, entry + reward
    return entry + risk, entry - reward


def next_bar_exit(side: str, high: float, low: float, stop: float | None, target: float | None) -> str | None:
    """Conservative: if both would hit on this completed bar, stop wins. No entry-bar check here."""
    if side == "BUY":
        stop_hit = stop is not None and low <= stop
        target_hit = target is not None and high >= target
    else:
        stop_hit = stop is not None and high >= stop
        target_hit = target is not None and low <= target
    if stop_hit and target_hit:
        return "STOP_LOSS_HIT"
    if stop_hit:
        return "STOP_LOSS_HIT"
    if target_hit:
        return "TARGET_HIT"
    return None


def index_hold_exit(
    side: str,
    high: float,
    low: float,
    stop: float | None,
    target: float | None,
    *,
    is_entry_bar: bool,
    clock: time,
    square: time,
    session_end: time,
    held_minutes: float,
    max_hold_minutes: int,
    end_of_walk: bool,
    end_of_session: bool = False,
) -> str | None:
    """Hold after BUY/SELL until stop, target, or time. Score going cold is not an exit."""
    if not is_entry_bar:
        hit = next_bar_exit(side, high, low, stop, target)
        if hit:
            return hit
    if clock >= square or clock >= session_end or end_of_session:
        return "FORCE_SQUARE_OFF"
    if max_hold_minutes > 0 and held_minutes >= max_hold_minutes:
        return "TIME_EXIT"
    if end_of_walk:
        return "END_OF_DATA"
    return None


def map_exit_reason(reason: str | None, *, end_of_data: bool = False) -> str:
    if end_of_data:
        return "END_OF_DATA"
    mapping = {
        "STOP_LOSS": "STOP_LOSS_HIT",
        "STOP_LOSS_HIT": "STOP_LOSS_HIT",
        "TARGET": "TARGET_HIT",
        "TARGET_HIT": "TARGET_HIT",
        "TRAILING_STOP": "TRAILING_STOP_HIT",
        "TRAILING_STOP_HIT": "TRAILING_STOP_HIT",
        "EMA20_TRAIL": "EMA20_TRAIL",
        "PREMIUM_STOP": "PREMIUM_STOP",
        "FAILED_BREAKOUT": "FAILED_BREAKOUT",
        "VWAP_RECROSS": "VWAP_RECROSS",
        "OPPOSITE_SIGNAL": "OPPOSITE_SIGNAL",
        "TIME_STOP": "TIME_STOP",
        "PARTIAL_1R": "PARTIAL_1R",
        "TIME_EXIT": "FORCE_SQUARE_OFF",
        "END_OF_SESSION": "FORCE_SQUARE_OFF",
        "MAX_HOLD": "TIME_EXIT",
        "END_OF_DATA": "END_OF_DATA",
        "MANUAL_EXIT": "MANUAL_EXIT",
    }
    return mapping.get(reason or "", reason or "FORCE_SQUARE_OFF")


def performance_from_trades(trades: list[dict]) -> dict:
    closed = [row for row in trades if row.get("status") != "OPEN"]
    open_rows = [row for row in trades if row.get("status") == "OPEN"]
    nets = [float(row.get("net_pnl") or 0) for row in closed]
    wins = [row for row in closed if float(row.get("net_pnl") or 0) > 0]
    losses = [row for row in closed if float(row.get("net_pnl") or 0) < 0]
    gross_profit = sum(max(float(row.get("gross_pnl") or 0), 0) for row in closed)
    gross_loss = abs(sum(min(float(row.get("gross_pnl") or 0), 0) for row in closed))
    costs = sum(float(row.get("total_charges") or row.get("charges") or 0) for row in closed)
    brokerage = sum(float(row.get("brokerage") or 0) for row in closed)
    slippage = sum(float(row.get("slippage") or row.get("slippage_cost") or 0) for row in closed)
    net = sum(nets)
    win_nets = [float(row.get("net_pnl") or 0) for row in wins]
    loss_nets = [float(row.get("net_pnl") or 0) for row in losses]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    best_w = 0
    best_l = 0
    last_win = None
    hold = []
    for row in sorted(closed, key=lambda item: str(item.get("exit_time") or item.get("entry_time") or "")):
        pnl = float(row.get("net_pnl") or 0)
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        won = pnl > 0
        if last_win is None or won == last_win:
            streak += 1
        else:
            streak = 1
        last_win = won
        if won:
            best_w = max(best_w, streak)
        else:
            best_l = max(best_l, streak)
        if row.get("holding_minutes") is not None:
            hold.append(float(row["holding_minutes"]))
    days = {str(row.get("date") or row.get("trade_date") or "")[:10] for row in closed}
    daily: dict[str, float] = {}
    for row in closed:
        day = str(row.get("date") or row.get("trade_date") or row.get("exit_time") or "")[:10]
        daily[day] = round(daily.get(day, 0) + float(row.get("net_pnl") or 0), 2)
    cumulative = []
    running = 0.0
    for day in sorted(k for k in daily if k):
        running += daily[day]
        cumulative.append({"date": day, "daily_pnl": daily[day], "cumulative_pnl": round(running, 2)})
    def _side(name: str) -> float:
        return sum(float(row.get("net_pnl") or 0) for row in closed if str(row.get("option_type") or row.get("side") or "").upper() == name)

    def _und(name: str) -> float:
        return sum(float(row.get("net_pnl") or 0) for row in closed if str(row.get("instrument") or row.get("underlying") or "") == name)

    reasons: dict[str, int] = {}
    for row in closed:
        key = str(row.get("exit_reason") or "UNKNOWN")
        reasons[key] = reasons.get(key, 0) + 1
    return {
        "label": "HISTORICAL PAPER RESULTS — NOT LIVE PERFORMANCE",
        "total_trades": len(trades),
        "open_trades": len(open_rows),
        "closed_trades": len(closed),
        "buy_trades": sum(1 for row in trades if str(row.get("option_type") or row.get("side") or "").upper() in {"BUY", "CE"}),
        "sell_trades": sum(1 for row in trades if str(row.get("option_type") or row.get("side") or "").upper() == "SELL"),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "breakeven_trades": max(0, len(closed) - len(wins) - len(losses)),
        "win_rate": round(100.0 * len(wins) / len(closed), 2) if closed else None,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(net, 2),
        "average_trade_result": round(net / len(closed), 2) if closed else None,
        "total_costs": round(costs, 2),
        "brokerage": round(brokerage, 2),
        "slippage": round(slippage, 2),
        "daily_pnl": cumulative,
        "average_profit": round(sum(win_nets) / len(win_nets), 2) if win_nets else None,
        "average_loss": round(sum(loss_nets) / len(loss_nets), 2) if loss_nets else None,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "largest_win": round(max(win_nets), 2) if win_nets else None,
        "largest_loss": round(min(loss_nets), 2) if loss_nets else None,
        "max_drawdown": round(max_dd, 2),
        "average_holding_minutes": round(sum(hold) / len(hold), 2) if hold else None,
        "longest_winning_streak": best_w,
        "longest_losing_streak": best_l,
        "trades_per_day": round(len(closed) / max(len(days), 1), 2) if closed else 0,
        "nifty_pnl": round(_und("NIFTY"), 2),
        "sensex_pnl": round(_und("SENSEX"), 2),
        "buy_pnl": round(_side("BUY") + _side("CE"), 2),
        "sell_pnl": round(_side("SELL"), 2),
        "pe_pnl": round(_side("PE"), 2),
        "exit_reasons": reasons,
        "same_candle_rule": SAME_CANDLE_RULE,
        "pnl_formula": PNL_FORMULA,
        "timezone": "Asia/Kolkata",
        "note": "HYPOTHETICAL / PAPER signals only. No broker orders.",
        "average_r": _avg([row.get("r_multiple") for row in closed]),
        "expectancy": round(net / len(closed), 2) if closed else None,
    }


def _avg(values) -> float | None:
    nums = [float(item) for item in values if item is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 4)


def is_index_expiry(symbol: str, day) -> bool:
    """Weekly expiry heuristic when the historical futures row is not dated."""
    weekday = day.weekday()
    name = str(symbol or "").upper()
    if name == "SENSEX":
        return weekday == 4
    from datetime import date as date_cls

    if isinstance(day, date_cls) and day >= date_cls(2024, 9, 1):
        return weekday == 1
    return weekday == 3


def signed_index_gross(side: str, entry, exit_px, qty: int, slippage_points: float = 0) -> Decimal:
    """Gross futures P&L only. Slippage is a cost, not part of gross."""
    return futures_gross(side, entry, exit_px, qty)


def index_paper_net(side: str, entry, exit_px, qty: int, *, slippage_points: float = 0, brokerage=0, transaction_costs=0) -> tuple[Decimal, Decimal, Decimal]:
    gross = signed_index_gross(side, entry, exit_px, qty)
    slip = slippage_cost(qty, slippage_points)
    net = futures_net(gross, brokerage=brokerage, transaction_costs=transaction_costs, slippage=slip)
    return gross, slip, net

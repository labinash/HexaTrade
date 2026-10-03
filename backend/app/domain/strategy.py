from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from app.core.clock import IST, to_ist
from app.domain.candles import Bar
from app.domain.indicators import atr, ema, rsi, session_vwap


BULLISH = "BULLISH"
BEARISH = "BEARISH"


@dataclass(frozen=True)
class StrategyParams:
    direction: str = BULLISH
    ema_period: int = 20
    ema_enabled: bool = True
    momentum_time: time = time(9, 20)
    momentum_min_points: float = 5
    momentum_min_percent: float = 0.05
    momentum_unit: str = "points"
    oi_time: time = time(9, 20)
    oi_min_change: float = 10000
    oi_unit: str = "contracts"
    volume_ema_period: int = 20
    volume_enabled: bool = True
    vwap_enabled: bool = True
    rsi_period: int = 14
    rsi_min: float = 40
    rsi_max: float = 70
    rsi_min_bearish: float = 30
    rsi_max_bearish: float = 60
    rsi_enabled: bool = True

    @property
    def bearish(self) -> bool:
        return self.direction == BEARISH

    def rsi_band(self) -> tuple[float, float]:
        """Bearish trends sit in a lower band, so the two are configured apart."""
        if self.bearish:
            return self.rsi_min_bearish, self.rsi_max_bearish
        return self.rsi_min, self.rsi_max

    def option_type(self) -> str:
        return self.bearish_option_type if self.bearish else self.bullish_option_type
    atr_period: int = 14
    atr_enabled: bool = True
    candle_bullish_required: bool = True
    time_start: time = time(9, 30)
    time_end: time = time(15, 0)
    time_enabled: bool = True
    timeframe_minutes: int = 1
    evaluate_on: str = "candle_close"
    confirmation_mode: str = "all"
    min_conditions: int = 9
    price_source: str = "front_futures"
    oi_source: str = "front_futures"
    bullish_option_type: str = "CE"
    bearish_option_type: str = "PE"
    lot_size: int = 1


@dataclass(frozen=True)
class MarketSnapshot:
    close: float | None
    open: float | None
    ema: float | None
    reference_close: float | None
    oi: float | None
    reference_oi: float | None
    volume: float | None
    volume_ema: float | None
    vwap: float | None
    rsi: float | None
    atr: float | None
    now: time | None
    reference_found: bool = True
    price_source_label: str = "front-month futures"
    oi_source_label: str = "front-month futures OI"


@dataclass
class Condition:
    name: str
    current: str
    required: str
    status: str
    is_directional: bool = False
    direction: str = ""
    failure_reason: str = ""


@dataclass
class Evaluation:
    conditions: list[Condition]
    direction: str
    passed: bool
    failed_names: list[str]

    @property
    def total_conditions(self) -> int:
        return len(self.conditions)

    @property
    def passed_conditions(self) -> int:
        return sum(1 for item in self.conditions if item.status == "PASS")

    @property
    def failed_conditions(self) -> int:
        return sum(1 for item in self.conditions if item.status == "FAIL")

    @property
    def disabled_conditions(self) -> int:
        return sum(1 for item in self.conditions if item.status == "OFF")

    def overall_signal(self) -> str:
        """BULLISH_SIGNAL, BEARISH_SIGNAL or NO_TRADE. Never a partial pass."""
        if not self.passed or self.direction == "NONE":
            return "NO_TRADE"
        return f"{self.direction}_SIGNAL"
    close: float | None
    ema: float | None
    momentum: float | None
    momentum_percent: float | None
    oi_change: float | None
    volume: float | None
    volume_ema: float | None
    vwap: float | None
    rsi: float | None
    atr: float | None


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def confirmation_passes(conditions: list[Condition], params: StrategyParams, live: bool) -> bool:
    enabled = [item for item in conditions if item.status != "OFF"]
    if not enabled:
        return False
    if live or params.confirmation_mode == "all":
        return all(item.status == "PASS" for item in enabled)
    passed = sum(1 for item in conditions if item.status == "PASS")
    return passed >= params.min_conditions


NO_DIRECTION = "NO_DIRECTION"


def direction_state(evaluation: Evaluation, params: StrategyParams) -> tuple[str, int]:
    """Graded label and a 0-100 condition agreement score.

    The score is the share of enabled conditions currently passing. It counts
    agreement between the conditions and nothing more: it is not a probability
    of profit and not a measure of expected return. It also does not loosen the
    entry gate, which remains Evaluation.passed, so a graded label short of a
    full pass never authorises a trade.
    """
    enabled = [item for item in evaluation.conditions if item.status != "OFF"]
    if not enabled:
        return NO_DIRECTION, 0
    share = sum(1 for item in enabled if item.status == "PASS") / len(enabled)
    score = round(share * 100)
    if evaluation.passed:
        return f"STRONG_{params.direction}", score
    if share >= 0.75:
        return params.direction, score
    if share >= 0.5:
        return f"WEAK_{params.direction}", score
    return NO_DIRECTION, score


def suggested_option_type(state: str, params: StrategyParams) -> str | None:
    """No side is suggested without a direction."""
    if state == NO_DIRECTION:
        return None
    return params.option_type()


def _directional(name: str, current: str, required: str, ok: bool | None, direction: str, reason: str = "") -> Condition:
    status = "FAIL" if ok is None else ("PASS" if ok else "FAIL")
    return Condition(
        name=name,
        current=current,
        required=required,
        status=status,
        is_directional=True,
        direction=direction,
        failure_reason="" if status == "PASS" else (reason or f"{name} does not satisfy {required}"),
    )


def _neutral(name: str, current: str, required: str, ok: bool | None, reason: str = "") -> Condition:
    status = "FAIL" if ok is None else ("PASS" if ok else "FAIL")
    return Condition(
        name=name,
        current=current,
        required=required,
        status=status,
        is_directional=False,
        direction="NEUTRAL",
        failure_reason="" if status == "PASS" else (reason or f"{name} does not satisfy {required}"),
    )


def _off(name: str, required: str, directional: bool, direction: str) -> Condition:
    return Condition(name, "—", required, "OFF", directional, direction if directional else "NEUTRAL")


def evaluate_ema(params: StrategyParams, close: float | None, ema: float | None) -> Condition:
    """Directional. Bullish wants close above the EMA, bearish below it."""
    comparison = "<" if params.bearish else ">"
    required = f"Close {comparison} EMA{params.ema_period}"
    if not params.ema_enabled:
        return _off("EMA Trend", required, True, params.direction)
    if close is None or ema is None:
        return _directional("EMA Trend", "—", required, None, params.direction, "EMA or close is unavailable")
    ok = close < ema if params.bearish else close > ema
    return _directional(
        "EMA Trend", _fmt(close), f"{required} ({_fmt(ema)})", ok, params.direction,
        f"Close {_fmt(close)} is not {comparison} EMA {_fmt(ema)}",
    )


def evaluate_momentum(
    params: StrategyParams, momentum: float | None, momentum_percent: float | None, reference_close: float | None
) -> Condition:
    """Directional. Bearish requires the move to be below the negated threshold,
    not merely below the positive one."""
    by_percent = params.momentum_unit == "percent"
    magnitude = params.momentum_min_percent if by_percent else params.momentum_min_points
    threshold = -magnitude if params.bearish else magnitude
    comparison = "<" if params.bearish else ">"
    label = params.momentum_time.strftime("%H:%M")
    required = f"{comparison} {threshold:g}{'%' if by_percent else ' pts'} from {label}"
    if by_percent and reference_close:
        required += f" (≈ {threshold / 100 * reference_close:,.1f} pts)"
    measured = momentum_percent if by_percent else momentum
    if measured is None:
        return _directional("Momentum", "missing", required, None, params.direction, f"No {label} reference close stored")
    current = f"{momentum:+,.2f} pts"
    if momentum_percent is not None:
        current += f" ({momentum_percent:+,.3f}%)"
    ok = measured < threshold if params.bearish else measured > threshold
    return _directional("Momentum", current, required, ok, params.direction, f"Move {current} is not {required}")


def evaluate_vwap(params: StrategyParams, close: float | None, vwap: float | None) -> Condition:
    """Directional. Bullish wants price above session VWAP, bearish below."""
    comparison = "<" if params.bearish else ">"
    required = f"Close {comparison} VWAP"
    if not params.vwap_enabled:
        return _off("VWAP", required, True, params.direction)
    if close is None or vwap is None:
        return _directional("VWAP", "—", required, None, params.direction, "VWAP or close is unavailable")
    ok = close < vwap if params.bearish else close > vwap
    relation = "Above" if close > vwap else ("Below" if close < vwap else "At")
    return _directional(
        "VWAP", f"{relation} {_fmt(vwap)}", required, ok, params.direction,
        f"Close is {relation.lower()} VWAP {_fmt(vwap)}",
    )


def evaluate_candle(params: StrategyParams, open_price: float | None, close: float | None) -> Condition:
    """Directional. A doji satisfies neither direction, so equality fails."""
    wanted = "Bearish" if params.bearish else "Bullish"
    if not params.candle_bullish_required:
        return _off("Candle", "not required", True, params.direction)
    if close is None or open_price is None:
        return _directional("Candle", "—", wanted, None, params.direction, "Candle open or close is unavailable")
    if close == open_price:
        return _directional("Candle", "Neutral", wanted, False, params.direction, "Neutral candle satisfies neither direction")
    bearish_candle = close < open_price
    current = "Bearish" if bearish_candle else "Bullish"
    ok = bearish_candle if params.bearish else not bearish_candle
    return _directional("Candle", current, wanted, ok, params.direction, f"Candle is {current.lower()}, not {wanted.lower()}")


def evaluate_rsi(params: StrategyParams, rsi_value: float | None) -> Condition:
    """Directional through configuration. The bullish and bearish bands are
    separate values, never the same range reused."""
    low, high = params.rsi_band()
    required = f"{low:g}–{high:g}"
    if not params.rsi_enabled:
        return _off("RSI", required, True, params.direction)
    if rsi_value is None:
        return _directional("RSI", "—", required, None, params.direction, "RSI is unavailable")
    ok = low <= rsi_value <= high
    return _directional(
        "RSI", _fmt(rsi_value), required, ok, params.direction,
        f"RSI {_fmt(rsi_value)} is outside {required}",
    )


def evaluate_volume(params: StrategyParams, volume: float | None, volume_ema: float | None) -> Condition:
    """Direction neutral. Participation is required either way."""
    required = f"> EMA{params.volume_ema_period}"
    if not params.volume_enabled:
        return _off("Futures Volume", required, False, "")
    if volume is None or volume_ema is None:
        return _neutral("Futures Volume", "—", required, None, "Volume or its EMA is unavailable")
    return _neutral(
        "Futures Volume", _fmt(volume, 0), f"{required} ({_fmt(volume_ema, 0)})", volume > volume_ema,
        f"Volume {_fmt(volume, 0)} is not above its EMA {_fmt(volume_ema, 0)}",
    )


def evaluate_oi(params: StrategyParams, oi_change: float | None) -> Condition:
    """Direction neutral by design. An OI increase is not proof of long buildup,
    so it is never flipped to a negative test for bearish."""
    unit = "lots" if params.oi_unit == "lots" else "contracts"
    label = params.oi_time.strftime("%H:%M")
    required = f"> {params.oi_min_change:,.0f} {unit} from {label}"
    if oi_change is None:
        return _neutral("OI Change", "missing", required, None, f"No {label} reference OI stored")
    return _neutral(
        "OI Change", f"{oi_change:+,.0f} {unit}", required, oi_change > params.oi_min_change,
        f"OI change {oi_change:+,.0f} {unit} is not {required}",
    )


def evaluate_atr(atr_value: float | None, atr_threshold: float | None) -> Condition:
    """Direction neutral. Volatility has to be present either way."""
    if atr_threshold is None:
        return _neutral("ATR", _fmt(atr_value), "threshold not configured", None, "No ATR threshold configured for this underlying")
    required = f"> {atr_threshold:g}"
    if atr_value is None:
        return _neutral("ATR", "—", required, None, "ATR is unavailable")
    return _neutral("ATR", _fmt(atr_value), required, atr_value > atr_threshold, f"ATR {_fmt(atr_value)} is not {required}")


def evaluate_time(params: StrategyParams, clock: time | None) -> Condition:
    """Direction neutral. The same window applies to both directions."""
    required = f"{_hm(params.time_start)}–{_hm(params.time_end)}"
    if not params.time_enabled:
        return _off("Time", "disabled", False, "")
    if clock is None:
        return _neutral("Time", "—", required, None, "No candle time available")
    return _neutral(
        "Time", _hm(clock), required, params.time_start <= clock <= params.time_end,
        f"{_hm(clock)} is outside {required}",
    )


def evaluate_snapshot(
    snap: MarketSnapshot,
    params: StrategyParams,
    atr_threshold: float | None,
    *,
    live: bool = False,
) -> Evaluation:
    momentum = None
    momentum_percent = None
    if snap.close is not None and snap.reference_close is not None:
        momentum = snap.close - snap.reference_close
        if snap.reference_close:
            momentum_percent = momentum / snap.reference_close * 100

    oi_change = None
    divisor = params.lot_size if params.oi_unit == "lots" and params.lot_size else 1
    if snap.oi is not None and snap.reference_oi is not None:
        oi_change = (snap.oi - snap.reference_oi) / divisor

    # Order matches the dashboard table.
    conditions = [
        evaluate_ema(params, snap.close, snap.ema),
        evaluate_momentum(params, momentum, momentum_percent, snap.reference_close),
        evaluate_oi(params, oi_change),
        evaluate_volume(params, snap.volume, snap.volume_ema),
        evaluate_vwap(params, snap.close, snap.vwap),
        evaluate_rsi(params, snap.rsi),
        evaluate_atr(snap.atr, atr_threshold),
        evaluate_candle(params, snap.open, snap.close),
        evaluate_time(params, snap.now),
    ]

    passed = confirmation_passes(conditions, params, live)
    failed = [item.name for item in conditions if item.status == "FAIL"]
    return Evaluation(
        conditions=conditions,
        direction=params.direction if passed else "NONE",
        passed=passed,
        failed_names=failed,
        close=snap.close,
        ema=snap.ema,
        momentum=momentum,
        momentum_percent=momentum_percent,
        oi_change=oi_change,
        volume=snap.volume,
        volume_ema=snap.volume_ema,
        vwap=snap.vwap,
        rsi=snap.rsi,
        atr=snap.atr,
    )


def snapshot_from_bars(bars: list[Bar], params: StrategyParams) -> MarketSnapshot:
    """Build a snapshot from bars the caller already decided are visible. No future bars."""
    if not bars:
        return MarketSnapshot(
            close=None, open=None, ema=None, reference_close=None, oi=None, reference_oi=None,
            volume=None, volume_ema=None, vwap=None, rsi=None, atr=None, now=None, reference_found=False,
        )
    last = bars[-1]
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    ema_series = ema(closes, params.ema_period)
    volume_series = ema(volumes, params.volume_ema_period)
    rsi_series = rsi(closes, params.rsi_period)
    atr_series = atr([bar.high for bar in bars], [bar.low for bar in bars], closes, params.atr_period)
    typical = [(bar.high + bar.low + bar.close) / 3 for bar in bars]
    keys = [to_ist(bar.start).date() for bar in bars]
    vwap_series = session_vwap(typical, volumes, keys)
    ref_close, ref_oi, found = _reference(bars, params.momentum_time, params.oi_time)
    clock = to_ist(last.start).time().replace(second=0, microsecond=0)
    price_label = "spot" if params.price_source == "spot" else "front-month futures"
    oi_label = "selected option OI" if params.oi_source == "selected_option" else "front-month futures OI"
    return MarketSnapshot(
        close=last.close,
        open=last.open,
        ema=ema_series[-1],
        reference_close=ref_close,
        oi=last.oi,
        reference_oi=ref_oi,
        volume=last.volume,
        volume_ema=volume_series[-1],
        vwap=vwap_series[-1],
        rsi=rsi_series[-1],
        atr=atr_series[-1],
        now=clock,
        reference_found=found,
        price_source_label=price_label,
        oi_source_label=oi_label,
    )


def _reference(bars: list[Bar], momentum_time: time, oi_time: time) -> tuple[float | None, float | None, bool]:
    last_day = to_ist(bars[-1].start).date()
    wanted = momentum_time.replace(second=0, microsecond=0)
    oi_wanted = oi_time.replace(second=0, microsecond=0)
    ref_close = None
    ref_oi = None
    for bar in bars:
        local = to_ist(bar.start)
        if local.date() != last_day:
            continue
        clock = local.time().replace(second=0, microsecond=0)
        if clock == wanted:
            ref_close = bar.close
        if clock == oi_wanted:
            ref_oi = bar.oi
    found = ref_close is not None and ref_oi is not None
    return ref_close, ref_oi, found


def _hm(value: time) -> str:
    return value.strftime("%H:%M")


def bar_clock(moment: datetime) -> time:
    return to_ist(moment).time().replace(second=0, microsecond=0)


def make_bar(moment: datetime, price: float, volume: float = 1000, oi: float = 0, spread: float = 1) -> Bar:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=IST)
    return Bar(start=aware, open=price - spread, high=price + spread, low=price - spread, close=price, volume=volume, oi=oi)

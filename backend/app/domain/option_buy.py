"""Independent CE and PE option-buy evaluators.

These engines read the selected option contract's premium and OI together with
index-futures filters. They do not share a final decision: a CE failure never
forces PE to fail, and a PE failure never forces CE to fail.

Missing 09:20 / 11:00 observations are reported as DATA UNAVAILABLE. The current
value is never substituted, observations from another session are never used,
and a zero or null reference premium never becomes a divisor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from app.core.clock import to_ist
from app.domain.candles import Bar
from app.domain.indicators import ema
from app.domain.market_facts import market_facts_from_bars


CE_TOTAL = 9
PE_TOTAL = 9
PASS = "PASS"
FAIL = "FAIL"
UNAVAILABLE = "DATA UNAVAILABLE"
BUY_SETUP = "BUY SETUP"
NO_TRADE = "NO TRADE"


@dataclass(frozen=True)
class OptionBuyConfig:
    underlying: str = "NIFTY"
    ema_period: int = 20
    momentum_time: time = time(9, 20)
    midday_time: time = time(11, 0)
    premium_momentum_percent: float = 3.0
    pe_oi_since_midday: float = 10000
    volume_ema_period: int = 20
    rsi_period: int = 14
    ce_rsi_min: float = 40
    ce_rsi_max: float = 70
    pe_rsi_min: float = 30
    pe_rsi_max: float = 60
    atr_period: int = 14
    atr_threshold: float | None = None
    time_start: time = time(9, 30)
    time_end: time = time(15, 0)
    timeframe_minutes: int = 1
    candidate_strike_range: int = 5
    min_candidate_score_percent: float = 60


@dataclass
class OptionPremiumState:
    close: float | None = None
    open: float | None = None
    ema: float | None = None
    oi: float | None = None
    prev_oi: float | None = None
    prev_oi_found: bool = False
    ref_0920_close: float | None = None
    ref_0920_found: bool = False
    ref_1100_close: float | None = None
    ref_1100_found: bool = False
    ref_1100_oi: float | None = None
    ref_1100_oi_found: bool = False
    bar_time: datetime | None = None
    symbol: str | None = None
    expiry: str | None = None
    strike: float | None = None
    option_type: str = ""
    token: int | None = None
    live_ltp: float | None = None
    live_oi: float | None = None
    data_quality: str = ""


@dataclass
class FuturesState:
    close: float | None = None
    volume: float | None = None
    volume_ema: float | None = None
    vwap: float | None = None
    rsi: float | None = None
    atr: float | None = None
    bar_time: datetime | None = None
    symbol: str | None = None
    data_quality: str = ""


@dataclass
class ConditionResult:
    name: str
    current: str
    required: str
    passed: bool
    status: str
    failure_reason: str
    data_timestamp: str | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "current": self.current,
            "required": self.required,
            "passed": self.passed,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "data_timestamp": self.data_timestamp,
        }


@dataclass
class OptionStrategyResult:
    strategy: str
    status: str
    passed: bool
    passed_count: int
    total_count: int
    conditions: list[ConditionResult]
    option_symbol: str | None = None
    expiry: str | None = None
    strike: float | None = None
    option_type: str = ""
    token: int | None = None
    premium_ltp: float | None = None
    premium_close: float | None = None
    premium_ema20: float | None = None
    premium_momentum_percent: float | None = None
    option_oi: float | None = None
    option_oi_change: float | None = None
    futures_volume: float | None = None
    futures_volume_ema20: float | None = None
    futures_close: float | None = None
    futures_vwap: float | None = None
    futures_rsi14: float | None = None
    futures_atr14: float | None = None
    premium_candle: str = "UNAVAILABLE"
    trading_time_ok: bool | None = None
    data_quality: str = ""
    timeframe: str = "1 minute"
    failed: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "status": self.status,
            "passed": self.passed,
            "passed_count": self.passed_count,
            "total_count": self.total_count,
            "option_symbol": self.option_symbol,
            "expiry": self.expiry,
            "strike": self.strike,
            "option_type": self.option_type,
            "token": self.token,
            "premium_ltp": self.premium_ltp,
            "premium_close": self.premium_close,
            "premium_ema20": self.premium_ema20,
            "premium_momentum_percent": self.premium_momentum_percent,
            "option_oi": self.option_oi,
            "option_oi_change": self.option_oi_change,
            "futures_volume": self.futures_volume,
            "futures_volume_ema20": self.futures_volume_ema20,
            "futures_close": self.futures_close,
            "futures_vwap": self.futures_vwap,
            "futures_rsi14": self.futures_rsi14,
            "futures_atr14": self.futures_atr14,
            "premium_candle": self.premium_candle,
            "trading_time_ok": self.trading_time_ok,
            "data_quality": self.data_quality,
            "timeframe": self.timeframe,
            "conditions": [item.as_dict() for item in self.conditions],
            "failed": self.failed,
            "score_percent": round(100.0 * self.passed_count / self.total_count, 2) if self.total_count else 0.0,
        }


def observation_at(bars: list[Bar], wanted: time, session_date=None) -> Bar | None:
    """Exact clock on the requested session date. No nearest-bar substitute."""
    if not bars:
        return None
    last_day = session_date if session_date is not None else to_ist(bars[-1].start).date()
    wanted = wanted.replace(second=0, microsecond=0)
    match = None
    for bar in bars:
        local = to_ist(bar.start)
        if local.date() != last_day:
            continue
        if local.time().replace(second=0, microsecond=0) == wanted:
            match = bar
    return match


def previous_session_bar(bars: list[Bar]) -> Bar | None:
    if len(bars) < 2:
        return None
    last_day = to_ist(bars[-1].start).date()
    prev = bars[-2]
    if to_ist(prev.start).date() != last_day:
        return None
    return prev


def option_premium_state(
    bars: list[Bar],
    minute_bars: list[Bar],
    config: OptionBuyConfig,
    *,
    symbol: str | None = None,
    expiry: str | None = None,
    strike: float | None = None,
    option_type: str = "",
    token: int | None = None,
    live_ltp: float | None = None,
    live_oi: float | None = None,
    quality: str = "",
    session_date=None,
) -> OptionPremiumState:
    notes = [quality] if quality else []
    if not bars:
        return OptionPremiumState(
            symbol=symbol, expiry=expiry, strike=strike, option_type=option_type, token=token,
            live_ltp=live_ltp, live_oi=live_oi,
            data_quality="; ".join(n for n in notes if n) or "No premium candles for the selected contract",
        )
    last_day = to_ist(bars[-1].start).date()
    if session_date is not None and last_day != session_date:
        notes.append("Premium candles are not from the current session")
    last = bars[-1]
    closes = [bar.close for bar in bars]
    ema_series = ema(closes, config.ema_period)
    prev = previous_session_bar(bars)
    ref_source = minute_bars if minute_bars else bars
    ref_day = session_date or last_day
    ref_0920 = observation_at(ref_source, config.momentum_time, ref_day)
    ref_1100 = observation_at(ref_source, config.midday_time, ref_day)
    if live_ltp is None and last.close is None:
        notes.append("Option premium is unavailable")
    return OptionPremiumState(
        close=last.close,
        open=last.open,
        ema=ema_series[-1],
        oi=last.oi,
        prev_oi=None if prev is None else prev.oi,
        prev_oi_found=prev is not None,
        ref_0920_close=None if ref_0920 is None else ref_0920.close,
        ref_0920_found=ref_0920 is not None,
        ref_1100_close=None if ref_1100 is None else ref_1100.close,
        ref_1100_found=ref_1100 is not None,
        ref_1100_oi=None if ref_1100 is None else ref_1100.oi,
        ref_1100_oi_found=ref_1100 is not None,
        bar_time=last.start,
        symbol=symbol,
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        token=token,
        live_ltp=live_ltp,
        live_oi=live_oi if live_oi is not None else last.oi,
        data_quality="; ".join(n for n in notes if n),
    )


def futures_state(bars: list[Bar], config: OptionBuyConfig, *, symbol: str | None = None, quality: str = "") -> FuturesState:
    if not bars:
        return FuturesState(symbol=symbol, data_quality=quality or "Index futures candles are unavailable")
    last = bars[-1]
    facts = market_facts_from_bars(
        bars,
        ema_period=config.ema_period,
        rsi_period=config.rsi_period,
        atr_period=config.atr_period,
        volume_ema_period=config.volume_ema_period,
    )
    return FuturesState(
        close=facts.close,
        volume=facts.volume,
        volume_ema=facts.volume_ema,
        vwap=facts.vwap,
        rsi=facts.rsi,
        atr=facts.atr,
        bar_time=last.start,
        symbol=symbol,
        data_quality=quality,
    )


def available_score_percent(result: OptionStrategyResult) -> float:
    """Share of conditions that can be evaluated. Skips DATA UNAVAILABLE rows."""
    rows = [item for item in result.conditions if item.status != UNAVAILABLE]
    if not rows:
        return 0.0
    return round(100.0 * sum(1 for item in rows if item.status == PASS) / len(rows), 2)


def evaluate_ce_buy_strategy(
    option: OptionPremiumState,
    futures: FuturesState,
    config: OptionBuyConfig,
    now: datetime,
) -> OptionStrategyResult:
    stamp = _stamp(option.bar_time or futures.bar_time)
    momentum = _momentum_percent(option.close, option.ref_0920_close, option.ref_0920_found)
    conditions = [
        _premium_trend(option, config, stamp),
        _premium_momentum(option, config, stamp, momentum),
        _futures_volume(futures, config, stamp),
        _option_oi_step(option, stamp),
        _futures_vwap(futures, stamp, above=True),
        _futures_rsi(futures, config.ce_rsi_min, config.ce_rsi_max, stamp),
        _futures_atr(futures, config, stamp),
        _premium_candle(option, stamp),
        _trading_time(config, now, stamp),
    ]
    return _result("CE BUY", "CE", CE_TOTAL, option, futures, config, conditions, momentum)


def evaluate_pe_buy_strategy(
    option: OptionPremiumState,
    futures: FuturesState,
    config: OptionBuyConfig,
    now: datetime,
) -> OptionStrategyResult:
    stamp = _stamp(option.bar_time or futures.bar_time)
    momentum = _momentum_percent(option.close, option.ref_0920_close, option.ref_0920_found)
    conditions = [
        _premium_trend(option, config, stamp),
        _premium_momentum(option, config, stamp, momentum),
        _futures_volume(futures, config, stamp),
        _option_oi_step(option, stamp),
        _futures_vwap(futures, stamp, above=False),
        _futures_rsi(futures, config.pe_rsi_min, config.pe_rsi_max, stamp),
        _futures_atr(futures, config, stamp),
        _premium_candle(option, stamp),
        _trading_time(config, now, stamp),
    ]
    return _result("PE BUY", "PE", PE_TOTAL, option, futures, config, conditions, momentum)


def empty_result(kind: str, config: OptionBuyConfig, reason: str, now: datetime) -> OptionStrategyResult:
    option = OptionPremiumState(option_type=kind, data_quality=reason)
    futures = FuturesState(data_quality=reason)
    if kind == "CE":
        return evaluate_ce_buy_strategy(option, futures, config, now)
    return evaluate_pe_buy_strategy(option, futures, config, now)


def _result(
    strategy: str,
    kind: str,
    total: int,
    option: OptionPremiumState,
    futures: FuturesState,
    config: OptionBuyConfig,
    conditions: list[ConditionResult],
    momentum: float | None,
) -> OptionStrategyResult:
    passed_count = sum(1 for item in conditions if item.status == PASS)
    passed = all(item.status == PASS for item in conditions)
    failed = [
        {"name": item.name, "reason": item.failure_reason}
        for item in conditions
        if item.status != PASS
    ]
    quality = "; ".join(part for part in (option.data_quality, futures.data_quality) if part)
    oi_change = None
    if option.oi is not None and option.prev_oi is not None and option.prev_oi_found:
        oi_change = option.oi - option.prev_oi
    candle = _candle_label(option.open, option.close)
    time_row = next((item for item in conditions if item.name == "Trading Time"), None)
    return OptionStrategyResult(
        strategy=strategy,
        status=BUY_SETUP if passed else NO_TRADE,
        passed=passed,
        passed_count=passed_count,
        total_count=total,
        conditions=conditions,
        option_symbol=option.symbol,
        expiry=option.expiry,
        strike=option.strike,
        option_type=kind,
        token=option.token,
        premium_ltp=option.live_ltp if option.live_ltp is not None else option.close,
        premium_close=option.close,
        premium_ema20=option.ema,
        premium_momentum_percent=momentum,
        option_oi=option.live_oi if option.live_oi is not None else option.oi,
        option_oi_change=oi_change,
        futures_volume=futures.volume,
        futures_volume_ema20=futures.volume_ema,
        futures_close=futures.close,
        futures_vwap=futures.vwap,
        futures_rsi14=futures.rsi,
        futures_atr14=futures.atr,
        premium_candle=candle,
        trading_time_ok=None if time_row is None else time_row.passed,
        data_quality=quality,
        timeframe=f"{config.timeframe_minutes} minute",
        failed=failed,
    )


def _cond(name: str, current: str, required: str, status: str, reason: str, stamp: str | None) -> ConditionResult:
    passed = status == PASS
    return ConditionResult(
        name=name,
        current=current,
        required=required,
        passed=passed,
        status=status,
        failure_reason="" if passed else reason,
        data_timestamp=stamp,
    )


def _premium_trend(option: OptionPremiumState, config: OptionBuyConfig, stamp: str | None) -> ConditionResult:
    required = f"Close > EMA{config.ema_period}"
    if option.close is None or option.ema is None:
        return _cond("Premium Trend", "—", required, UNAVAILABLE, "Option premium EMA20 is unavailable", stamp)
    ok = option.close > option.ema
    return _cond(
        "Premium Trend",
        _fmt(option.close),
        f"{required} ({_fmt(option.ema)})",
        PASS if ok else FAIL,
        f"Premium close {_fmt(option.close)} is not above EMA {_fmt(option.ema)}.",
        stamp,
    )


def _premium_momentum(
    option: OptionPremiumState, config: OptionBuyConfig, stamp: str | None, momentum: float | None
) -> ConditionResult:
    label = config.momentum_time.strftime("%H:%M")
    required = f"> {config.premium_momentum_percent:g}% from {label}"
    if not option.ref_0920_found or option.ref_0920_close is None:
        return _cond(
            "Premium Momentum",
            "missing",
            required,
            UNAVAILABLE,
            f"No {label} premium observation stored for this contract and session.",
            stamp,
        )
    if option.ref_0920_close == 0 or option.close is None:
        return _cond(
            "Premium Momentum",
            "undefined",
            required,
            UNAVAILABLE,
            f"The {label} reference premium is zero or the current premium is missing, so percent change cannot be computed.",
            stamp,
        )
    ok = momentum is not None and momentum > config.premium_momentum_percent
    return _cond(
        "Premium Momentum",
        f"{momentum:+.3f}%" if momentum is not None else "—",
        required,
        PASS if ok else FAIL,
        f"Premium change from {label} is {momentum:+.3f}%, which is not above {config.premium_momentum_percent:g}%.",
        stamp,
    )


def _premium_since_midday(option: OptionPremiumState, config: OptionBuyConfig, stamp: str | None) -> ConditionResult:
    label = config.midday_time.strftime("%H:%M")
    required = f"Close > Close({label})"
    if not option.ref_1100_found or option.ref_1100_close is None:
        return _cond(
            "Premium Since 11:00",
            "missing",
            required,
            UNAVAILABLE,
            f"No {label} premium observation stored for this contract and session.",
            stamp,
        )
    if option.close is None:
        return _cond("Premium Since 11:00", "—", required, UNAVAILABLE, "Current PE premium is unavailable.", stamp)
    delta = option.close - option.ref_1100_close
    ok = delta > 0
    return _cond(
        "Premium Since 11:00",
        f"{delta:+.2f}",
        required,
        PASS if ok else FAIL,
        f"PE premium {_fmt(option.close)} is not above the {label} premium {_fmt(option.ref_1100_close)}.",
        stamp,
    )


def _oi_since_midday(option: OptionPremiumState, config: OptionBuyConfig, stamp: str | None) -> ConditionResult:
    label = config.midday_time.strftime("%H:%M")
    required = f"OI - OI({label}) > {config.pe_oi_since_midday:g}"
    if not option.ref_1100_oi_found or option.ref_1100_oi is None:
        return _cond(
            "OI Since 11:00",
            "missing",
            required,
            UNAVAILABLE,
            f"No {label} OI observation stored for this contract and session.",
            stamp,
        )
    if option.oi is None:
        return _cond("OI Since 11:00", "—", required, UNAVAILABLE, "Current PE OI is unavailable.", stamp)
    change = option.oi - option.ref_1100_oi
    ok = change > config.pe_oi_since_midday
    return _cond(
        "OI Since 11:00",
        f"{change:+,.0f}",
        required,
        PASS if ok else FAIL,
        f"OI change since {label} is {change:+,.0f}, which is not above {config.pe_oi_since_midday:g}. Rising OI alone is not proof of long buildup.",
        stamp,
    )


def _futures_volume(futures: FuturesState, config: OptionBuyConfig, stamp: str | None) -> ConditionResult:
    required = f"Index futures volume > EMA{config.volume_ema_period}"
    if futures.volume is None or futures.volume_ema is None:
        return _cond("Futures Volume", "—", required, UNAVAILABLE, "Index futures volume EMA20 is unavailable.", stamp)
    ok = futures.volume > futures.volume_ema
    return _cond(
        "Futures Volume",
        _fmt(futures.volume),
        f"{required} ({_fmt(futures.volume_ema)})",
        PASS if ok else FAIL,
        f"Index futures volume {_fmt(futures.volume)} is not above volume EMA {_fmt(futures.volume_ema)}.",
        stamp,
    )


def _option_oi_step(option: OptionPremiumState, stamp: str | None) -> ConditionResult:
    required = "OI(0) > OI(-1)"
    if not option.prev_oi_found or option.oi is None or option.prev_oi is None:
        return _cond("Option OI", "—", required, UNAVAILABLE, "Previous comparable OI for this contract is unavailable.", stamp)
    ok = option.oi > option.prev_oi
    return _cond(
        "Option OI",
        f"{option.oi:,.0f} vs {option.prev_oi:,.0f}",
        required,
        PASS if ok else FAIL,
        f"Current OI {option.oi:,.0f} is not greater than the previous observation {option.prev_oi:,.0f}.",
        stamp,
    )


def _futures_vwap(futures: FuturesState, stamp: str | None, *, above: bool) -> ConditionResult:
    comparison = ">" if above else "<"
    required = f"Index futures close {comparison} VWAP"
    if futures.close is None or futures.vwap is None:
        return _cond("Futures VWAP", "—", required, UNAVAILABLE, "Index futures close or VWAP is unavailable.", stamp)
    ok = futures.close > futures.vwap if above else futures.close < futures.vwap
    relation = "above" if futures.close > futures.vwap else ("below" if futures.close < futures.vwap else "at")
    return _cond(
        "Futures VWAP",
        f"{relation} {_fmt(futures.vwap)}",
        required,
        PASS if ok else FAIL,
        f"Index futures close is {relation} VWAP {_fmt(futures.vwap)}.",
        stamp,
    )


def _futures_rsi(futures: FuturesState, low: float, high: float, stamp: str | None) -> ConditionResult:
    required = f"{low:g} < RSI(14) < {high:g}"
    if futures.rsi is None:
        return _cond("Futures RSI", "—", required, UNAVAILABLE, "Index futures RSI14 is unavailable.", stamp)
    ok = futures.rsi > low and futures.rsi < high
    return _cond(
        "Futures RSI",
        _fmt(futures.rsi),
        required,
        PASS if ok else FAIL,
        f"Index futures RSI {_fmt(futures.rsi)} is not strictly between {low:g} and {high:g}.",
        stamp,
    )


def _futures_atr(futures: FuturesState, config: OptionBuyConfig, stamp: str | None) -> ConditionResult:
    required = f"ATR(14) > {config.atr_threshold:g}" if config.atr_threshold is not None else "ATR threshold configured"
    if config.atr_threshold is None:
        return _cond(
            "Futures ATR",
            _fmt(futures.atr),
            f"threshold for {config.underlying}",
            UNAVAILABLE,
            f"No ATR threshold is configured for {config.underlying}. The NIFTY value is not reused.",
            stamp,
        )
    if futures.atr is None:
        return _cond("Futures ATR", "—", required, UNAVAILABLE, "Index futures ATR14 is unavailable.", stamp)
    ok = futures.atr > config.atr_threshold
    return _cond(
        "Futures ATR",
        _fmt(futures.atr),
        required,
        PASS if ok else FAIL,
        f"Index futures ATR {_fmt(futures.atr)} is not above {config.atr_threshold:g}.",
        stamp,
    )


def _premium_candle(option: OptionPremiumState, stamp: str | None) -> ConditionResult:
    required = "Close > Open"
    if option.close is None or option.open is None:
        return _cond("Premium Candle", "—", required, UNAVAILABLE, "Option premium candle open or close is unavailable.", stamp)
    ok = option.close > option.open
    label = _candle_label(option.open, option.close)
    return _cond(
        "Premium Candle",
        label,
        required,
        PASS if ok else FAIL,
        f"Premium candle is {label.lower()}; a close above the open is required.",
        stamp,
    )


def _trading_time(config: OptionBuyConfig, now: datetime, stamp: str | None) -> ConditionResult:
    clock = to_ist(now).time().replace(second=0, microsecond=0)
    start = config.time_start.replace(second=0, microsecond=0)
    end = config.time_end.replace(second=0, microsecond=0)
    required = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
    ok = start <= clock <= end
    return _cond(
        "Trading Time",
        clock.strftime("%H:%M"),
        required,
        PASS if ok else FAIL,
        f"Current time {clock.strftime('%H:%M')} is outside the trading window {required}.",
        stamp,
    )


def _momentum_percent(close: float | None, reference: float | None, found: bool) -> float | None:
    if not found or close is None or reference is None or reference == 0:
        return None
    return ((close - reference) / reference) * 100


def _candle_label(open_price: float | None, close: float | None) -> str:
    if open_price is None or close is None:
        return "UNAVAILABLE"
    if close > open_price:
        return "BULLISH"
    if close < open_price:
        return "BEARISH"
    return "NEUTRAL"


def _stamp(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    local = to_ist(moment)
    return local.strftime("%Y-%m-%d %H:%M")


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"

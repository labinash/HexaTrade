"""Option strike selection engine.

Produces ranked candidate strikes under configured criteria. The score is a
suitability and risk-analysis score. It is not a probability of profit, and no
candidate here is safe or guaranteed. Every stop, target and expected move is a
hypothetical calculation, not a forecast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.greeks import Greeks, black76_greeks, years_to_expiry
from app.domain.oi_scanner import classify_price_oi

ALLOW = "allow"
WARN = "warn"
REJECT = "reject"


@dataclass(frozen=True)
class ScoreWeights:
    liquidity: float = 20
    spread: float = 15
    delta: float = 15
    reward_risk: float = 20
    volume_oi: float = 10
    iv: float = 5
    premium: float = 5
    expiry: float = 5
    atm_distance: float = 5

    def total(self) -> float:
        return (
            self.liquidity + self.spread + self.delta + self.reward_risk
            + self.volume_oi + self.iv + self.premium + self.expiry + self.atm_distance
        )


@dataclass(frozen=True)
class SelectionConfig:
    strikes_each_side: int = 2
    min_volume: int = 1000
    min_oi: int = 5000
    max_spread_percent: float = 2.0
    max_quote_age_seconds: int = 10
    min_premium: float = 20
    max_premium: float = 400
    min_reward_risk: float = 1.5
    min_score: float = 50
    preferred_delta_min: float = 0.35
    preferred_delta_max: float = 0.60
    acceptable_delta_min: float = 0.25
    acceptable_delta_max: float = 0.70
    delta_missing_action: str = WARN
    iv_high_ratio: float = 1.15
    iv_high_action: str = WARN
    atr_multiplier: float = 1.0
    stop_method: str = "option_percent"
    stop_option_percent: float = 20
    stop_atr_multiplier: float = 1.0
    target_reward_risk: float = 2.0
    max_holding_minutes: int = 60
    same_day_warn: bool = True
    near_expiry_days: int = 2
    far_expiry_days: int = 21
    account_capital: float = 100000
    max_risk_percent: float = 1.0
    max_lots: int = 5
    max_capital_allocation: float = 50000
    max_daily_loss: float = 5000
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    bullish_option_type: str = "CE"
    bearish_option_type: str = "PE"


@dataclass
class OptionQuote:
    token: int
    tradingsymbol: str
    strike: float
    option_type: str
    expiry: date | None
    lot_size: int
    ltp: float | None
    bid: float | None
    ask: float | None
    bid_quantity: int | None
    ask_quantity: int | None
    volume: int | None
    oi: int | None
    day_open_oi: int | None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    change: float | None = None
    iv: float | None = None
    quote_age_seconds: float | None = None


@dataclass
class UnderlyingInput:
    symbol: str
    direction: str
    signal_valid: bool
    conditions_passed: int
    conditions_failed: int
    spot: float | None
    futures_ltp: float | None
    futures_volume: float | None
    futures_oi: float | None
    futures_oi_change: float | None
    vwap: float | None
    ema: float | None
    rsi: float | None
    atr: float | None
    reference_close: float | None
    reference_oi: float | None
    strike_interval: float | None
    stale: bool
    risk_free_rate: float = 0.07
    daily_loss_used: float = 0.0
    swing_low: float | None = None
    swing_high: float | None = None


@dataclass
class Candidate:
    tradingsymbol: str
    token: int
    strike: float
    option_type: str
    expiry: date | None
    lot_size: int
    ltp: float | None
    bid: float | None
    ask: float | None
    bid_quantity: int | None
    ask_quantity: int | None
    volume: int | None
    oi: int | None
    oi_change: int | None
    oi_change_percent: float | None
    volume_oi_ratio: float | None
    premium_change: float | None
    spread: float | None
    spread_percent: float | None
    moneyness: str
    strikes_from_atm: int
    distance_from_atm: float | None
    iv: float | None
    iv_relative: float | None
    iv_band: str
    iv_percentile: float | None
    greeks: Greeks
    days_to_expiry: int | None
    expiry_band: str
    quote_age_seconds: float | None
    entry: float | None
    stop_loss: float | None
    target: float | None
    risk_per_unit: float | None
    reward_per_unit: float | None
    reward_risk: float | None
    underlying_stop: float | None
    expected_move: float | None
    expected_underlying_target: float | None
    expected_option_response: float | None
    allowed_lots: int
    quantity: int
    risk_amount: float | None
    capital_required: float | None
    oi_interpretation: str
    status: str = "PASS"
    reject_reasons: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    subscores: dict[str, float] = field(default_factory=dict)
    score: float = 0.0


@dataclass
class SelectionResult:
    underlying: str
    direction: str
    option_type: str | None
    atm: float | None
    strike_interval: float | None
    expiry: date | None
    available: bool
    selected: Candidate | None
    top: list[Candidate]
    rejected: list[Candidate]
    all_candidates: list[Candidate]
    no_trade_reasons: list[str]
    expected_move: float | None
    evaluated_at: datetime
    disclaimer: str = (
        "Selection score measures relative suitability under the configured criteria. "
        "It is not a probability of profit and no candidate is safe or guaranteed."
    )


def option_type_for(direction: str, config: SelectionConfig) -> str | None:
    if direction == "BULLISH":
        return config.bullish_option_type
    if direction == "BEARISH":
        return config.bearish_option_type
    return None


def atm_from(strikes: list[float], price: float) -> float | None:
    if not strikes:
        return None
    return min(strikes, key=lambda strike: (abs(strike - price), strike))


def candidate_strikes(strikes: list[float], atm: float, each_side: int) -> list[float]:
    ordered = sorted(strikes)
    if atm not in ordered:
        return []
    index = ordered.index(atm)
    start = max(0, index - each_side)
    end = min(len(ordered), index + each_side + 1)
    return ordered[start:end]


def strike_ladder(strikes: list[float], atm: float | None, option_type: str) -> dict[str, float | None]:
    """The adjacent real strikes for 1 ITM, ATM and 1 OTM.

    Steps to the neighbouring strikes present in the instrument master rather
    than adding a fixed interval, so a missing strike is reported as missing
    instead of being invented. CE and PE step opposite ways.
    """
    empty: dict[str, float | None] = {"ITM": None, "ATM": None, "OTM": None}
    ordered = sorted(set(strikes))
    if atm is None or atm not in ordered:
        return empty
    index = ordered.index(atm)
    lower = ordered[index - 1] if index >= 1 else None
    higher = ordered[index + 1] if index + 1 < len(ordered) else None
    if option_type == "CE":
        return {"ITM": lower, "ATM": atm, "OTM": higher}
    return {"ITM": higher, "ATM": atm, "OTM": lower}


def moneyness_of(option_type: str, strike: float, price: float) -> str:
    if math.isclose(strike, price, rel_tol=0, abs_tol=1e-9):
        return "ATM"
    if option_type == "CE":
        return "ITM" if strike < price else "OTM"
    return "ITM" if strike > price else "OTM"


def oi_interpretation(premium_change: float | None, oi_change: int | None) -> str:
    """Wording for the selection card. The quadrant rule itself lives in
    oi_scanner so both screens read OI the same way."""
    code, label = classify_price_oi(premium_change, oi_change)
    if code == "UNKNOWN":
        return "OI and price evidence incomplete, no interpretation offered"
    sentences = {
        "LONG_BUILDUP": "Price up with OI up, possible long buildup",
        "SHORT_BUILDUP": "Price down with OI up, possible short buildup",
        "SHORT_COVERING": "Price up with OI down, possible short covering",
        "LONG_UNWINDING": "Price down with OI down, possible long unwinding",
    }
    return sentences.get(code, label)


def expiry_band(days: int | None, config: SelectionConfig) -> str:
    if days is None:
        return "UNKNOWN"
    if days <= 0:
        return "SAME_DAY"
    if days <= config.near_expiry_days:
        return "NEAR"
    if days >= config.far_expiry_days:
        return "FAR"
    return "NORMAL"


def stop_and_target(
    entry: float,
    option_type: str,
    underlying: UnderlyingInput,
    config: SelectionConfig,
    delta: float | None,
) -> tuple[float | None, float | None, float | None]:
    """Hypothetical levels. Returns (option stop, option target, underlying stop)."""
    underlying_stop = None
    stop = None
    if config.stop_method == "option_percent":
        stop = entry * (1 - config.stop_option_percent / 100)
    elif config.stop_method == "underlying_atr":
        if underlying.atr is None or underlying.futures_ltp is None or not delta:
            return None, None, None
        move = underlying.atr * config.stop_atr_multiplier
        underlying_stop = (
            underlying.futures_ltp - move if option_type == "CE" else underlying.futures_ltp + move
        )
        stop = entry - abs(delta) * move
    elif config.stop_method == "underlying_level":
        level = underlying.swing_low if option_type == "CE" else underlying.swing_high
        if level is None:
            level = underlying.vwap
        if level is None or underlying.futures_ltp is None or not delta:
            return None, None, None
        move = abs(underlying.futures_ltp - level)
        underlying_stop = level
        stop = entry - abs(delta) * move
    else:
        return None, None, None
    if stop is None or stop <= 0 or stop >= entry:
        return None, None, underlying_stop
    risk = entry - stop
    target = entry + risk * config.target_reward_risk
    return stop, target, underlying_stop


def position_size(
    entry: float,
    stop: float,
    lot_size: int,
    config: SelectionConfig,
    daily_loss_used: float,
) -> tuple[int, float | None, float | None]:
    risk_per_unit = entry - stop
    if risk_per_unit <= 0 or lot_size <= 0:
        return 0, None, None
    risk_budget = config.account_capital * config.max_risk_percent / 100
    loss_room = max(config.max_daily_loss - abs(daily_loss_used), 0)
    risk_budget = min(risk_budget, loss_room) if config.max_daily_loss > 0 else risk_budget
    risk_per_lot = risk_per_unit * lot_size
    by_risk = math.floor(risk_budget / risk_per_lot) if risk_per_lot > 0 else 0
    capital_per_lot = entry * lot_size
    by_capital = math.floor(config.max_capital_allocation / capital_per_lot) if capital_per_lot > 0 else 0
    lots = max(0, min(by_risk, by_capital, config.max_lots))
    quantity = lots * lot_size
    return lots, risk_per_unit * quantity if quantity else None, entry * quantity if quantity else None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _relative(value: float | None, values: list[float]) -> float:
    if value is None or not values:
        return 0.0
    top = max(values)
    if top <= 0:
        return 0.0
    return _clamp(value / top)


def _delta_score(abs_delta: float | None, config: SelectionConfig) -> float:
    if abs_delta is None:
        return 0.0
    if config.preferred_delta_min <= abs_delta <= config.preferred_delta_max:
        return 1.0
    if abs_delta < config.acceptable_delta_min or abs_delta > config.acceptable_delta_max:
        return 0.0
    if abs_delta < config.preferred_delta_min:
        span = config.preferred_delta_min - config.acceptable_delta_min
        return _clamp((abs_delta - config.acceptable_delta_min) / span) * 0.7 if span > 0 else 0.5
    span = config.acceptable_delta_max - config.preferred_delta_max
    return _clamp((config.acceptable_delta_max - abs_delta) / span) * 0.7 if span > 0 else 0.5


def _premium_score(ltp: float | None, config: SelectionConfig) -> float:
    if ltp is None:
        return 0.0
    low, high = config.min_premium, config.max_premium
    if high <= low:
        return 0.5
    middle = (low + high) / 2
    half = (high - low) / 2
    return _clamp(1 - abs(ltp - middle) / half)


def _expiry_score(band: str) -> float:
    return {"NORMAL": 1.0, "FAR": 0.6, "NEAR": 0.4, "SAME_DAY": 0.15, "UNKNOWN": 0.0}[band]


def _iv_score(relative: float | None) -> float:
    if relative is None:
        return 0.5
    return _clamp(1 - abs(relative - 1.0) / 0.5)


def evaluate(
    underlying: UnderlyingInput,
    quotes: list[OptionQuote],
    config: SelectionConfig,
    now: datetime,
    *,
    require_signal: bool = True,
) -> SelectionResult:
    """Rank candidate strikes.

    require_signal=False lets a screen price and score the strikes while the
    strategy has not confirmed a signal. It never produces a `selected`
    candidate in that case, so nothing downstream can read it as a
    recommendation.
    """
    option_type = option_type_for(underlying.direction, config)
    blocked: list[str] = []
    advisory: list[str] = []
    if not underlying.signal_valid:
        (blocked if require_signal else advisory).append("strategy signal is not valid")
    if option_type is None:
        blocked.append("signal direction does not map to an option type")
    if underlying.stale:
        blocked.append("underlying market data is stale")
    price = underlying.futures_ltp if underlying.futures_ltp is not None else underlying.spot
    if price is None:
        blocked.append("underlying price is unavailable")
    if blocked:
        return SelectionResult(
            underlying=underlying.symbol, direction=underlying.direction, option_type=option_type,
            atm=None, strike_interval=underlying.strike_interval, expiry=None, available=False,
            selected=None, top=[], rejected=[], all_candidates=[], no_trade_reasons=blocked,
            expected_move=None, evaluated_at=now,
        )

    same_type = [q for q in quotes if q.option_type == option_type]
    strikes = sorted({q.strike for q in same_type})
    atm = atm_from(strikes, price)
    if atm is None:
        return SelectionResult(
            underlying=underlying.symbol, direction=underlying.direction, option_type=option_type,
            atm=None, strike_interval=underlying.strike_interval, expiry=None, available=False,
            selected=None, top=[], rejected=[], all_candidates=[],
            no_trade_reasons=["no strikes available for this expiry"], expected_move=None, evaluated_at=now,
        )
    chosen = candidate_strikes(strikes, atm, config.strikes_each_side)
    pool = [q for q in same_type if q.strike in chosen]
    expected_move = underlying.atr * config.atr_multiplier if underlying.atr is not None else None
    interval = underlying.strike_interval

    ivs = [q.iv for q in pool if q.iv is not None and q.iv > 0]
    median_iv = sorted(ivs)[len(ivs) // 2] if ivs else None

    candidates: list[Candidate] = []
    for quote in pool:
        candidates.append(_build(quote, underlying, config, price, atm, interval, median_iv, expected_move, now))

    passing = [c for c in candidates if c.status == "PASS"]
    rejected = [c for c in candidates if c.status == "REJECT"]
    _score(passing, config)
    passing.sort(key=lambda c: (-c.score, abs(c.strikes_from_atm)))

    reasons: list[str] = list(advisory)
    selected = None
    if not passing:
        reasons += _summarize_rejections(rejected)
    elif passing[0].score < config.min_score:
        reasons.append(f"best candidate score {passing[0].score:.0f} is below the configured minimum {config.min_score:.0f}")
    elif not advisory:
        selected = passing[0]

    return SelectionResult(
        underlying=underlying.symbol,
        direction=underlying.direction,
        option_type=option_type,
        atm=atm,
        strike_interval=interval,
        expiry=pool[0].expiry if pool else None,
        available=True,
        selected=selected,
        top=passing[:3],
        rejected=rejected,
        all_candidates=passing + rejected,
        no_trade_reasons=reasons,
        expected_move=expected_move,
        evaluated_at=now,
    )


def _build(
    quote: OptionQuote,
    underlying: UnderlyingInput,
    config: SelectionConfig,
    price: float,
    atm: float,
    interval: float | None,
    median_iv: float | None,
    expected_move: float | None,
    now: datetime,
) -> Candidate:
    spread = None
    spread_percent = None
    if quote.bid is not None and quote.ask is not None and quote.bid > 0 and quote.ask > 0:
        spread = quote.ask - quote.bid
        mid = (quote.ask + quote.bid) / 2
        spread_percent = (spread / mid * 100) if mid > 0 else None
    oi_change = None
    oi_change_percent = None
    if quote.oi is not None and quote.day_open_oi is not None:
        oi_change = quote.oi - quote.day_open_oi
        oi_change_percent = (oi_change / quote.day_open_oi * 100) if quote.day_open_oi else None
    volume_oi_ratio = None
    if quote.volume is not None and quote.oi:
        volume_oi_ratio = quote.volume / quote.oi
    strikes_from_atm = 0
    if interval and interval > 0:
        strikes_from_atm = int(round((quote.strike - atm) / interval))
    days = (quote.expiry - now.date()).days if quote.expiry else None
    band = expiry_band(days, config)
    years = years_to_expiry(days if days is not None else 0)
    greeks = black76_greeks(underlying.futures_ltp, quote.strike, years, underlying.risk_free_rate, quote.iv, quote.option_type == "CE")
    iv_relative = (quote.iv / median_iv) if quote.iv and median_iv else None
    iv_band = "UNKNOWN"
    if iv_relative is not None:
        iv_band = "HIGH" if iv_relative > config.iv_high_ratio else ("LOW" if iv_relative < 1 / config.iv_high_ratio else "NORMAL")

    entry = quote.ask if quote.ask and quote.ask > 0 else quote.ltp
    stop, target, underlying_stop = (None, None, None)
    if entry:
        stop, target, underlying_stop = stop_and_target(entry, quote.option_type, underlying, config, greeks.delta)
    risk = (entry - stop) if entry and stop else None
    reward = (target - entry) if entry and target else None
    reward_risk = (reward / risk) if risk and reward and risk > 0 else None
    lots, risk_amount, capital = (0, None, None)
    if entry and stop:
        lots, risk_amount, capital = position_size(entry, stop, quote.lot_size, config, underlying.daily_loss_used)
    option_response = None
    if expected_move is not None and greeks.delta is not None:
        option_response = abs(greeks.delta) * expected_move
    expected_target = None
    if expected_move is not None and underlying.futures_ltp is not None:
        expected_target = (
            underlying.futures_ltp + expected_move if quote.option_type == "CE" else underlying.futures_ltp - expected_move
        )

    candidate = Candidate(
        tradingsymbol=quote.tradingsymbol, token=quote.token, strike=quote.strike,
        option_type=quote.option_type, expiry=quote.expiry, lot_size=quote.lot_size,
        ltp=quote.ltp, bid=quote.bid, ask=quote.ask, bid_quantity=quote.bid_quantity,
        ask_quantity=quote.ask_quantity, volume=quote.volume, oi=quote.oi,
        oi_change=oi_change, oi_change_percent=oi_change_percent, volume_oi_ratio=volume_oi_ratio,
        premium_change=quote.change, spread=spread, spread_percent=spread_percent,
        moneyness=moneyness_of(quote.option_type, quote.strike, price),
        strikes_from_atm=strikes_from_atm, distance_from_atm=quote.strike - atm,
        iv=quote.iv, iv_relative=iv_relative, iv_band=iv_band, iv_percentile=None,
        greeks=greeks, days_to_expiry=days, expiry_band=band,
        quote_age_seconds=quote.quote_age_seconds, entry=entry, stop_loss=stop, target=target,
        risk_per_unit=risk, reward_per_unit=reward, reward_risk=reward_risk,
        underlying_stop=underlying_stop, expected_move=expected_move,
        expected_underlying_target=expected_target, expected_option_response=option_response,
        allowed_lots=lots, quantity=lots * quote.lot_size, risk_amount=risk_amount,
        capital_required=capital,
        oi_interpretation=oi_interpretation(quote.change, oi_change),
    )
    _apply_hard_filters(candidate, config)
    _notes(candidate, config)
    return candidate


def _apply_hard_filters(c: Candidate, config: SelectionConfig) -> None:
    fails: list[str] = []
    if c.quote_age_seconds is None:
        fails.append("quote timestamp is missing")
    elif c.quote_age_seconds > config.max_quote_age_seconds:
        fails.append(f"quote age {c.quote_age_seconds:.0f}s exceeds maximum {config.max_quote_age_seconds}s")
    if c.ltp is None or c.ltp <= 0:
        fails.append("last traded price is not available")
    if c.bid is None or c.bid <= 0:
        fails.append("no valid bid")
    if c.ask is None or c.ask <= 0:
        fails.append("no valid ask")
    if c.volume is None:
        fails.append("volume is unavailable")
    elif c.volume < config.min_volume:
        fails.append(f"volume {c.volume:,} below minimum {config.min_volume:,}")
    if c.oi is None:
        fails.append("open interest is unavailable")
    elif c.oi < config.min_oi:
        fails.append(f"OI {c.oi:,} below minimum {config.min_oi:,}")
    if c.spread_percent is None:
        fails.append("spread cannot be computed")
    elif c.spread_percent > config.max_spread_percent:
        fails.append(f"spread {c.spread_percent:.2f}% exceeds maximum {config.max_spread_percent:.2f}%")
    if c.ltp is not None and c.ltp > 0:
        if c.ltp < config.min_premium:
            fails.append(f"premium {c.ltp:.2f} below minimum {config.min_premium:.2f}")
        if c.ltp > config.max_premium:
            fails.append(f"premium {c.ltp:.2f} above maximum {config.max_premium:.2f}")
    if c.expiry is None:
        fails.append("expiry is unknown")
    if c.reward_risk is None:
        fails.append("reward/risk cannot be computed")
    elif c.reward_risk < config.min_reward_risk:
        fails.append(f"reward/risk {c.reward_risk:.2f} below minimum {config.min_reward_risk:.2f}")
    abs_delta = c.greeks.abs_delta
    if abs_delta is None:
        if config.delta_missing_action == REJECT:
            fails.append("delta is unavailable")
    elif abs_delta < config.acceptable_delta_min or abs_delta > config.acceptable_delta_max:
        fails.append(
            f"delta {abs_delta:.2f} outside acceptable range "
            f"{config.acceptable_delta_min:.2f}-{config.acceptable_delta_max:.2f}"
        )
    if c.iv_band == "HIGH" and config.iv_high_action == REJECT:
        fails.append("IV is elevated relative to nearby strikes and the configuration rejects high IV")
    if c.allowed_lots <= 0:
        fails.append("position sizing allows zero lots under the configured risk limits")
    if fails:
        c.status = "REJECT"
        c.reject_reasons = fails


def _notes(c: Candidate, config: SelectionConfig) -> None:
    abs_delta = c.greeks.abs_delta
    if c.volume is not None and c.volume >= config.min_volume:
        c.reasons.append("Volume meets the configured minimum")
    if c.oi is not None and c.oi >= config.min_oi:
        c.reasons.append("Open interest meets the configured minimum")
    if c.spread_percent is not None and c.spread_percent <= config.max_spread_percent:
        c.reasons.append(f"Spread {c.spread_percent:.2f}% within the configured maximum")
    if abs_delta is not None and config.preferred_delta_min <= abs_delta <= config.preferred_delta_max:
        c.reasons.append(f"Delta {abs_delta:.2f} inside the preferred range")
    if c.reward_risk is not None and c.reward_risk >= config.min_reward_risk:
        c.reasons.append(f"Reward/risk {c.reward_risk:.2f} meets the configured minimum")
    if c.ltp is not None and config.min_premium <= c.ltp <= config.max_premium:
        c.reasons.append("Premium inside the configured range")

    if abs_delta is None:
        c.warnings.append("Delta unavailable, so sensitivity could not be modelled")
    elif abs_delta < config.preferred_delta_min:
        c.warnings.append("Delta below the preferred range, so the option responds less to the underlying move")
    if c.iv_band == "HIGH":
        c.warnings.append("High IV relative to nearby strikes, premium may be expensive")
    if c.iv_band == "LOW":
        c.warnings.append("IV low relative to nearby strikes")
    if c.expiry_band == "SAME_DAY":
        c.warnings.append("Same-day expiry carries high time-decay exposure")
    elif c.expiry_band == "NEAR":
        c.warnings.append("Near expiry, time decay is elevated")
    if c.greeks.theta_per_day is not None and c.entry:
        daily = abs(c.greeks.theta_per_day) / c.entry * 100
        if daily >= 5:
            c.warnings.append(f"Modelled theta is about {daily:.1f}% of premium per day")
    if c.moneyness == "OTM" and abs(c.strikes_from_atm) >= 2:
        c.warnings.append("Two or more strikes out of the money, a larger underlying move is needed")
    if c.oi_change is None:
        c.warnings.append("OI change unavailable for this session")


def _score(candidates: list[Candidate], config: SelectionConfig) -> None:
    if not candidates:
        return
    weights = config.weights
    total_weight = weights.total() or 1
    liquidity_pool = [((c.volume or 0) * (c.oi or 0)) for c in candidates]
    volume_pool = [float(c.volume or 0) for c in candidates]
    oi_pool = [float(c.oi or 0) for c in candidates]
    max_distance = max((abs(c.strikes_from_atm) for c in candidates), default=0)
    best_rr = max((c.reward_risk or 0) for c in candidates) or 1

    for c in candidates:
        liquidity = _relative((c.volume or 0) * (c.oi or 0), liquidity_pool)
        spread = 0.0
        if c.spread_percent is not None and config.max_spread_percent > 0:
            spread = _clamp(1 - c.spread_percent / config.max_spread_percent)
        delta = _delta_score(c.greeks.abs_delta, config)
        rr = _clamp((c.reward_risk or 0) / best_rr)
        volume_oi = (_relative(float(c.volume or 0), volume_pool) + _relative(float(c.oi or 0), oi_pool)) / 2
        iv = _iv_score(c.iv_relative)
        premium = _premium_score(c.ltp, config)
        expiry = _expiry_score(c.expiry_band)
        distance = 1.0 if max_distance == 0 else _clamp(1 - abs(c.strikes_from_atm) / max_distance)
        c.subscores = {
            "liquidity": round(liquidity * 100, 1),
            "spread": round(spread * 100, 1),
            "delta": round(delta * 100, 1),
            "reward_risk": round(rr * 100, 1),
            "volume_oi": round(volume_oi * 100, 1),
            "iv": round(iv * 100, 1),
            "premium": round(premium * 100, 1),
            "expiry": round(expiry * 100, 1),
            "atm_distance": round(distance * 100, 1),
        }
        weighted = (
            liquidity * weights.liquidity
            + spread * weights.spread
            + delta * weights.delta
            + rr * weights.reward_risk
            + volume_oi * weights.volume_oi
            + iv * weights.iv
            + premium * weights.premium
            + expiry * weights.expiry
            + distance * weights.atm_distance
        )
        c.score = round(weighted / total_weight * 100, 1)


def _summarize_rejections(rejected: list[Candidate]) -> list[str]:
    if not rejected:
        return ["no candidate strikes were found around ATM"]
    buckets: dict[str, int] = {}
    for candidate in rejected:
        for reason in candidate.reject_reasons:
            key = reason.split(" ")[0].lower()
            label = {
                "volume": "insufficient volume",
                "oi": "insufficient open interest",
                "spread": "excessive bid/ask spread",
                "premium": "premium outside the configured range",
                "reward/risk": "insufficient reward/risk",
                "quote": "stale or missing quotes",
                "delta": "delta outside the acceptable range",
                "position": "position sizing allows zero lots",
                "expiry": "no valid expiry",
                "no": "missing bid or ask",
                "last": "missing last traded price",
                "iv": "IV rejected by configuration",
            }.get(key, reason)
            buckets[label] = buckets.get(label, 0) + 1
    return [f"{label} ({count} of {len(rejected)} candidates)" for label, count in sorted(buckets.items(), key=lambda kv: -kv[1])]

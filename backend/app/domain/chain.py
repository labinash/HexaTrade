from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76_price(futures: float, strike: float, years: float, rate: float, sigma: float, is_call: bool) -> float:
    if years <= 0 or sigma <= 0 or futures <= 0 or strike <= 0:
        intrinsic = max(futures - strike, 0.0) if is_call else max(strike - futures, 0.0)
        return math.exp(-rate * max(years, 0.0)) * intrinsic
    vol_sqrt = sigma * math.sqrt(years)
    d1 = (math.log(futures / strike) + 0.5 * sigma * sigma * years) / vol_sqrt
    d2 = d1 - vol_sqrt
    discount = math.exp(-rate * years)
    if is_call:
        return discount * (futures * norm_cdf(d1) - strike * norm_cdf(d2))
    return discount * (strike * norm_cdf(-d2) - futures * norm_cdf(-d1))


def implied_vol(
    price: float | None,
    futures: float | None,
    strike: float,
    years: float,
    rate: float,
    is_call: bool,
) -> float | None:
    if price is None or futures is None or price <= 0 or futures <= 0 or years <= 0:
        return None
    discount = math.exp(-rate * years)
    intrinsic = discount * (max(futures - strike, 0.0) if is_call else max(strike - futures, 0.0))
    upper = discount * futures
    if price < intrinsic - 1e-6 or price > upper + 1e-6:
        return None
    low, high = 1e-4, 5.0
    for _ in range(60):
        mid = (low + high) / 2
        theoretical = black76_price(futures, strike, years, rate, mid, is_call)
        if theoretical > price:
            high = mid
        else:
            low = mid
    sigma = (low + high) / 2
    if sigma <= 1e-3 or sigma >= 4.99:
        return None
    return sigma


def strike_interval(strikes: list[float]) -> float | None:
    unique = sorted(set(strikes))
    diffs = [round(b - a, 4) for a, b in zip(unique, unique[1:]) if b > a]
    if not diffs:
        return None
    return Counter(diffs).most_common(1)[0][0]


def atm_strike(strikes: list[float], spot: float) -> float | None:
    if not strikes:
        return None
    return min(strikes, key=lambda strike: (abs(strike - spot), strike))


def max_pain(strikes: list[float], call_oi: dict[float, float], put_oi: dict[float, float]) -> float | None:
    if not strikes:
        return None
    best_strike = None
    best_pain = None
    for settle in strikes:
        pain = 0.0
        for strike, oi in call_oi.items():
            pain += max(settle - strike, 0.0) * oi
        for strike, oi in put_oi.items():
            pain += max(strike - settle, 0.0) * oi
        if best_pain is None or pain < best_pain:
            best_pain = pain
            best_strike = settle
    return best_strike


@dataclass
class QuoteSide:
    token: int | None
    tradingsymbol: str
    ltp: float | None
    bid: float | None
    ask: float | None
    oi: int | None
    prev_oi: int | None
    volume: int | None
    change: float | None
    iv: float | None
    lot_size: int

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def oi_change(self) -> int | None:
        if self.oi is None or self.prev_oi is None:
            return None
        return self.oi - self.prev_oi


@dataclass
class ChainRow:
    strike: float
    moneyness: str
    ce: QuoteSide | None
    pe: QuoteSide | None


@dataclass
class ChainSnapshot:
    underlying: str
    expiry: date | None
    spot: float | None
    futures_ltp: float | None
    atm: float | None
    as_of: datetime | None
    stale: bool
    rows: list[ChainRow]
    total_ce_oi: int
    total_pe_oi: int
    pcr: float | None
    max_pain: float | None
    max_pain_scope: str
    highest_oi_strike: float | None
    highest_oi_change_strike: float | None
    strike_interval: float | None
    window_limited: bool


def liquidity_score(side: QuoteSide) -> float:
    volume = side.volume or 0
    oi = side.oi or 0
    spread = side.spread if side.spread is not None else 0.0
    return (volume * max(oi, 1)) / (1 + max(spread, 0.0))


def build_chain(
    *,
    underlying: str,
    expiry: date | None,
    spot: float | None,
    futures_ltp: float | None,
    contracts: list[dict],
    quotes: dict[int, dict],
    prev_oi: dict[int, int],
    strikes_above: int,
    strikes_below: int,
    as_of: datetime | None,
    stale: bool,
    rate: float,
    years_to_expiry: float,
) -> ChainSnapshot:
    by_strike: dict[float, dict[str, dict]] = {}
    for contract in contracts:
        strike = float(contract["strike"])
        by_strike.setdefault(strike, {})[contract["instrument_type"]] = contract
    all_strikes = sorted(by_strike)
    interval = strike_interval(all_strikes)
    center = atm_strike(all_strikes, spot) if spot is not None else None
    window_limited = False
    selected = all_strikes
    if center is not None and all_strikes:
        index = all_strikes.index(center)
        start = max(0, index - strikes_below)
        end = min(len(all_strikes), index + strikes_above + 1)
        selected = all_strikes[start:end]
        window_limited = len(selected) < len(all_strikes)
    elif all_strikes:
        mid = len(all_strikes) // 2
        start = max(0, mid - strikes_below)
        end = min(len(all_strikes), mid + strikes_above + 1)
        selected = all_strikes[start:end]
        window_limited = True

    rows: list[ChainRow] = []
    call_oi: dict[float, float] = {}
    put_oi: dict[float, float] = {}
    total_ce = 0
    total_pe = 0
    best_oi = None
    best_oi_strike = None
    best_change = None
    best_change_strike = None

    for strike in selected:
        sides = {}
        for option_type in ("CE", "PE"):
            contract = by_strike[strike].get(option_type)
            if not contract:
                sides[option_type] = None
                continue
            token = int(contract["instrument_token"])
            quote = quotes.get(token, {})
            ltp = _num(quote.get("ltp"))
            is_call = option_type == "CE"
            iv = implied_vol(ltp, futures_ltp, strike, years_to_expiry, rate, is_call)
            side = QuoteSide(
                token=token,
                tradingsymbol=contract["tradingsymbol"],
                ltp=ltp,
                bid=_num(quote.get("bid")),
                ask=_num(quote.get("ask")),
                oi=_int(quote.get("oi")),
                prev_oi=prev_oi.get(token),
                volume=_int(quote.get("volume")),
                change=_num(quote.get("change")),
                iv=iv,
                lot_size=int(contract["lot_size"]),
            )
            sides[option_type] = side
            oi_value = side.oi or 0
            if option_type == "CE":
                total_ce += oi_value
                call_oi[strike] = oi_value
            else:
                total_pe += oi_value
                put_oi[strike] = oi_value
            if best_oi is None or oi_value > best_oi:
                best_oi = oi_value
                best_oi_strike = strike
            change = side.oi_change
            if change is not None and (best_change is None or change > best_change):
                best_change = change
                best_change_strike = strike
        if spot is None:
            moneyness = "UNKNOWN"
        elif strike < spot:
            moneyness = "ITM_CALL"
        elif strike > spot:
            moneyness = "OTM_CALL"
        else:
            moneyness = "ATM"
        rows.append(ChainRow(strike=strike, moneyness=moneyness, ce=sides.get("CE"), pe=sides.get("PE")))

    pcr = (total_pe / total_ce) if total_ce else None
    return ChainSnapshot(
        underlying=underlying,
        expiry=expiry,
        spot=spot,
        futures_ltp=futures_ltp,
        atm=center,
        as_of=as_of,
        stale=stale,
        rows=rows,
        total_ce_oi=total_ce,
        total_pe_oi=total_pe,
        pcr=pcr,
        max_pain=max_pain(selected, call_oi, put_oi),
        max_pain_scope="subscribed window",
        highest_oi_strike=best_oi_strike,
        highest_oi_change_strike=best_change_strike,
        strike_interval=interval,
        window_limited=window_limited,
    )


def select_contract(
    rows: list[ChainRow],
    *,
    option_type: str,
    method: str,
    strike_offset: int,
    spot: float | None,
    premium_min: float | None,
    premium_max: float | None,
    min_volume: int,
    min_oi: int,
    max_spread: float | None,
    min_liquidity: float,
) -> tuple[QuoteSide | None, str]:
    indexed = [(row.strike, row.ce if option_type == "CE" else row.pe) for row in rows]
    indexed = [(strike, side) for strike, side in indexed if side is not None]
    if not indexed:
        return None, "no contracts in the chain window"
    strikes = [strike for strike, _ in indexed]
    center = atm_strike(strikes, spot) if spot is not None else strikes[len(strikes) // 2]
    if center is None:
        return None, "ATM strike is unavailable"
    ordered = sorted(strikes)
    center_index = ordered.index(center)
    if method == "atm":
        chosen_strike = center
    elif method in {"atm_plus", "atm_minus"}:
        step = strike_offset or 1
        delta = step if method == "atm_plus" else -step
        target = center_index + delta
        if target < 0 or target >= len(ordered):
            return None, "strike offset is outside the chain window"
        chosen_strike = ordered[target]
    elif method == "nearest_itm":
        if spot is None:
            return None, "spot is required for ITM selection"
        candidates = [s for s in ordered if (s < spot if option_type == "CE" else s > spot)]
        if not candidates:
            return None, "no ITM strike in the window"
        chosen_strike = min(candidates, key=lambda s: abs(s - spot))
    elif method == "nearest_otm":
        if spot is None:
            return None, "spot is required for OTM selection"
        candidates = [s for s in ordered if (s > spot if option_type == "CE" else s < spot)]
        if not candidates:
            return None, "no OTM strike in the window"
        chosen_strike = min(candidates, key=lambda s: abs(s - spot))
    else:
        return None, f"unknown selection method {method}"

    side = next(item for strike, item in indexed if strike == chosen_strike)
    reasons = []
    if side.ltp is None:
        reasons.append("premium unavailable")
    if premium_min is not None and side.ltp is not None and side.ltp < premium_min:
        reasons.append("premium below minimum")
    if premium_max is not None and side.ltp is not None and side.ltp > premium_max:
        reasons.append("premium above maximum")
    if (side.volume or 0) < min_volume:
        reasons.append("volume below minimum")
    if (side.oi or 0) < min_oi:
        reasons.append("OI below minimum")
    if max_spread is not None and side.spread is not None and side.spread > max_spread:
        reasons.append("spread above maximum")
    if liquidity_score(side) < min_liquidity:
        reasons.append("liquidity below minimum")
    if reasons:
        return None, "; ".join(reasons)
    return side, ""


def _num(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _int(value) -> int | None:
    if value is None:
        return None
    return int(value)

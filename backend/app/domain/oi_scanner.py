"""Open interest scanning calculations.

Everything here is descriptive market analysis. A price and OI combination is
labelled as a possible interpretation, never as a buy or sell instruction, and
nothing in this module places or proposes an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

LIVE = "LIVE"
PRE_OPEN = "MARKET_NOT_OPEN"
CLOSED = "MARKET_CLOSED"

ALL = "ALL"
INCREASE = "INCREASE"
DECREASE = "DECREASE"
SIGNIFICANT_INCREASE = "SIGNIFICANT_INCREASE"
SIGNIFICANT_DECREASE = "SIGNIFICANT_DECREASE"

STATE_LABELS = {LIVE: "Live", PRE_OPEN: "Market not open", CLOSED: "Market closed"}


@dataclass(frozen=True)
class ScannerConfig:
    interval_minutes: int = 5
    reference_time: time = time(10, 0)
    strikes_above: int = 10
    strikes_below: int = 10
    show_all: bool = False
    option_type: str = ALL
    expiry_mode: str = "nearest"
    min_oi_change_interval: float = 10000
    min_oi_change_reference: float = 10000
    direction: str = ALL
    refresh_seconds: int = 5
    snapshot_tolerance_minutes: int = 3


@dataclass(frozen=True)
class Contract:
    instrument_token: int
    tradingsymbol: str
    underlying: str
    expiry: date | None
    strike: float
    option_type: str
    lot_size: int


@dataclass(frozen=True)
class Snapshot:
    """One stored minute of history for a single instrument token."""

    oi: int | None = None
    ltp: float | None = None
    volume: int | None = None
    at: datetime | None = None

    @property
    def usable(self) -> bool:
        return self.at is not None and self.oi is not None


EMPTY = Snapshot()


@dataclass
class ScanRow:
    instrument_token: int
    tradingsymbol: str
    underlying: str
    expiry: date | None
    strike: float
    option_type: str
    moneyness: str
    strikes_from_atm: int
    ltp: float | None
    bid: float | None
    ask: float | None
    volume: int | None
    oi: int | None
    day_change: float | None
    timestamp: datetime | None
    oi_change_interval: int | None
    oi_change_interval_percent: float | None
    interval_from: datetime | None
    interval_note: str
    oi_change_reference: int | None
    oi_change_reference_percent: float | None
    reference_at: datetime | None
    reference_note: str
    price_change: float | None
    price_change_percent: float | None
    interpretation: str
    interpretation_code: str
    significant_increase: bool = False
    significant_decrease: bool = False
    notes: list[str] = field(default_factory=list)


def market_state(now: time, session_start: time, session_end: time) -> str:
    if now < session_start:
        return PRE_OPEN
    if now > session_end:
        return CLOSED
    return LIVE


def safe_percent(change: float | None, base: float | None) -> float | None:
    """Percent change, or None when the base cannot support one.

    A zero or missing base would produce infinity, so it returns None instead.
    """
    if change is None or base is None or base == 0:
        return None
    return change / abs(base) * 100


def classify_price_oi(price_change: float | None, oi_change: float | None) -> tuple[str, str]:
    """The four price and OI quadrants, worded as possibilities.

    An OI increase on its own proves nothing about direction, so price movement
    is always required before any label is offered.
    """
    if price_change is None or oi_change is None:
        return "UNKNOWN", "Price and OI evidence incomplete"
    if price_change == 0 or oi_change == 0:
        return "FLAT", "No clear price and OI pattern"
    if price_change > 0 and oi_change > 0:
        return "LONG_BUILDUP", "Potential long buildup"
    if price_change < 0 and oi_change > 0:
        return "SHORT_BUILDUP", "Potential short buildup"
    if price_change > 0 and oi_change < 0:
        return "SHORT_COVERING", "Potential short covering"
    return "LONG_UNWINDING", "Potential long unwinding"


def moneyness_of(option_type: str, strike: float, price: float) -> str:
    if strike == price:
        return "ATM"
    if option_type == "CE":
        return "ITM" if strike < price else "OTM"
    return "ITM" if strike > price else "OTM"


def build_row(
    contract: Contract,
    live: Snapshot,
    previous: Snapshot,
    reference: Snapshot,
    config: ScannerConfig,
    state: str,
    atm: float | None,
    strike_interval: float | None,
    historical: bool = False,
) -> ScanRow:
    interval_change = None
    interval_percent = None
    if state == PRE_OPEN:
        interval_note = "Market not open"
    elif state == CLOSED and not historical:
        # A rolling window against the clock is meaningless once ticks stop, so
        # it is withheld until the user asks for a specific past time.
        interval_note = f"Market closed, {config.interval_minutes}-minute change not calculated"
    elif not live.usable:
        interval_note = "Instrument data unavailable"
    elif not previous.usable:
        interval_note = f"No {config.interval_minutes}-minute reference snapshot available"
    else:
        interval_change = live.oi - previous.oi
        interval_percent = safe_percent(interval_change, previous.oi)
        interval_note = "" if interval_percent is not None else "Previous OI was zero, percent not shown"

    reference_change = None
    reference_percent = None
    label = config.reference_time.strftime("%H:%M")
    if state == PRE_OPEN:
        reference_note = "Market not open"
    elif not live.usable:
        reference_note = "Instrument data unavailable"
    elif not reference.usable:
        reference_note = f"No {label} reference snapshot available"
    else:
        reference_change = live.oi - reference.oi
        reference_percent = safe_percent(reference_change, reference.oi)
        reference_note = "" if reference_percent is not None else "Reference OI was zero, percent not shown"

    price_change = None
    price_percent = None
    if interval_change is not None and live.ltp is not None and previous.ltp is not None:
        price_change = live.ltp - previous.ltp
        price_percent = safe_percent(price_change, previous.ltp)

    code, interpretation = classify_price_oi(price_change, interval_change)
    row = ScanRow(
        instrument_token=contract.instrument_token,
        tradingsymbol=contract.tradingsymbol,
        underlying=contract.underlying,
        expiry=contract.expiry,
        strike=contract.strike,
        option_type=contract.option_type,
        moneyness=moneyness_of(contract.option_type, contract.strike, atm) if atm is not None else "",
        strikes_from_atm=_steps(contract.strike, atm, strike_interval),
        ltp=live.ltp,
        bid=None,
        ask=None,
        volume=live.volume,
        oi=live.oi,
        day_change=None,
        timestamp=live.at,
        oi_change_interval=interval_change,
        oi_change_interval_percent=interval_percent,
        interval_from=previous.at,
        interval_note=interval_note,
        oi_change_reference=reference_change,
        oi_change_reference_percent=reference_percent,
        reference_at=reference.at,
        reference_note=reference_note,
        price_change=price_change,
        price_change_percent=price_percent,
        interpretation=interpretation,
        interpretation_code=code,
    )
    threshold = abs(config.min_oi_change_interval)
    if interval_change is not None and threshold > 0:
        row.significant_increase = interval_change >= threshold
        row.significant_decrease = interval_change <= -threshold
    if state == CLOSED:
        row.notes.append("Market closed, values are from the last stored snapshots")
        if not historical:
            row.notes.append("Pick a historical time to measure an interval change for a past moment")
    return row


def _steps(strike: float, atm: float | None, interval: float | None) -> int:
    if atm is None or not interval:
        return 0
    return int(round((strike - atm) / interval))


def passes(row: ScanRow, config: ScannerConfig) -> bool:
    if config.option_type in {"CE", "PE"} and row.option_type != config.option_type:
        return False
    change = row.oi_change_interval
    reference = row.oi_change_reference
    if config.min_oi_change_interval and change is not None:
        if not _meets(change, config.min_oi_change_interval):
            return False
    if config.min_oi_change_reference and reference is not None:
        if not _meets(reference, config.min_oi_change_reference):
            return False
    if config.direction == INCREASE:
        return change is not None and change > 0
    if config.direction == DECREASE:
        return change is not None and change < 0
    if config.direction == SIGNIFICANT_INCREASE:
        return row.significant_increase
    if config.direction == SIGNIFICANT_DECREASE:
        return row.significant_decrease
    return True


def _meets(change: float, threshold: float) -> bool:
    """A positive threshold keeps changes at or above it in magnitude.

    A negative threshold keeps only reductions at or below it, so the user can
    scan specifically for unwinding.
    """
    if threshold < 0:
        return change <= threshold
    return abs(change) >= threshold


def summarise(rows: list[ScanRow]) -> dict:
    def pick(option_type: str, largest: bool):
        candidates = [
            row for row in rows
            if row.option_type == option_type and row.oi_change_interval is not None
            and (row.oi_change_interval > 0 if largest else row.oi_change_interval < 0)
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda row: row.oi_change_interval if largest else -row.oi_change_interval)
        return {
            "strike": best.strike,
            "option_type": best.option_type,
            "tradingsymbol": best.tradingsymbol,
            "oi_change": best.oi_change_interval,
            "oi_change_percent": best.oi_change_interval_percent,
        }

    return {
        "largest_ce_increase": pick("CE", True),
        "largest_pe_increase": pick("PE", True),
        "largest_ce_decrease": pick("CE", False),
        "largest_pe_decrease": pick("PE", False),
    }


def top_movers(rows: list[ScanRow], limit: int = 10) -> dict:
    scored = [row for row in rows if row.oi_change_interval is not None]
    additions = sorted(
        (row for row in scored if row.oi_change_interval > 0),
        key=lambda row: -row.oi_change_interval,
    )[:limit]
    reductions = sorted(
        (row for row in scored if row.oi_change_interval < 0),
        key=lambda row: row.oi_change_interval,
    )[:limit]
    return {"additions": [_mover(row) for row in additions], "reductions": [_mover(row) for row in reductions]}


def _mover(row: ScanRow) -> dict:
    return {
        "underlying": row.underlying,
        "tradingsymbol": row.tradingsymbol,
        "strike": row.strike,
        "option_type": row.option_type,
        "expiry": row.expiry.isoformat() if row.expiry else None,
        "oi_change": row.oi_change_interval,
        "oi_change_percent": row.oi_change_interval_percent,
        "oi": row.oi,
        "ltp": row.ltp,
        "price_change": row.price_change,
        "interpretation": row.interpretation,
    }


def window_strikes(strikes: list[float], atm: float, above: int, below: int) -> list[float]:
    ordered = sorted(strikes)
    if atm not in ordered:
        return ordered
    index = ordered.index(atm)
    return ordered[max(0, index - below) : index + above + 1]

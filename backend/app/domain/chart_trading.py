"""Chart CE/PE trading helpers. Reuses ATM ladder and risk rules. Does not compute signals."""

from __future__ import annotations

import hashlib
from datetime import time

from app.domain.execution import OrderRefused
from app.domain.strike_selection import strike_ladder

ALLOWED_UNDERLYINGS = ("NIFTY", "SENSEX")
ALLOWED_MONEYNESS = ("ITM", "ATM", "OTM")
ALLOWED_OPTION_TYPES = ("CE", "PE")
ALLOWED_SIDES = ("BUY", "SELL")
ALLOWED_ORDER_TYPES = ("MARKET", "LIMIT")
ALLOWED_PRODUCTS = ("MIS", "NRML")
LIVE_ENABLE_PHRASE = "PLACE REAL ORDERS"
LIVE_ORDER_PHRASE = "CONFIRM LIVE BUY"
DEFAULT_MARKET_PROTECTION_PERCENT = 2.0
KITE_TAG_MAX = 20
POSITION_KIND = "net"

STATUS_SUBMITTED = "ORDER SUBMITTED"
STATUS_OPEN = "ORDER OPEN"
STATUS_COMPLETE = "ORDER COMPLETE"
STATUS_REJECTED = "ORDER REJECTED"
STATUS_CANCELLED = "ORDER CANCELLED"
STATUS_BLOCKED = "LIVE ORDER BLOCKED"

BROKER_STATUS_MAP = {
    "OPEN": STATUS_OPEN,
    "TRIGGER PENDING": STATUS_OPEN,
    "PUT ORDER REQ RECEIVED": STATUS_SUBMITTED,
    "VALIDATION PENDING": STATUS_SUBMITTED,
    "OPEN PENDING": STATUS_SUBMITTED,
    "MODIFY PENDING": STATUS_OPEN,
    "CANCEL PENDING": STATUS_OPEN,
    "COMPLETE": STATUS_COMPLETE,
    "FILLED": STATUS_COMPLETE,
    "REJECTED": STATUS_REJECTED,
    "CANCELLED": STATUS_CANCELLED,
    "CANCELED": STATUS_CANCELLED,
}


def recommended_option(trend: str | None) -> str | None:
    """Same mapping the dashboard uses. Does not re-run the 9-condition engine."""
    if trend == "BULLISH":
        return "CE"
    if trend == "BEARISH":
        return "PE"
    return None


def recommended_action(trend: str | None, passed: bool | None = None) -> str:
    side = recommended_option(trend)
    if not side:
        return "NO TRADE"
    if passed is False:
        return f"WATCH {side}"
    return f"BUY {side}"


def assert_underlying(symbol: str) -> str:
    name = str(symbol or "").upper()
    if name not in ALLOWED_UNDERLYINGS:
        raise OrderRefused("Allowed instruments are NIFTY and SENSEX.")
    return name


def assert_option_type(kind: str) -> str:
    value = str(kind or "").upper()
    if value not in ALLOWED_OPTION_TYPES:
        raise OrderRefused("optionType must be CE or PE.")
    return value


def assert_moneyness(label: str) -> str:
    value = str(label or "").upper()
    if value not in ALLOWED_MONEYNESS:
        raise OrderRefused("moneyness must be ITM, ATM, or OTM.")
    return value


def assert_side(side: str) -> str:
    value = str(side or "").upper()
    if value not in ALLOWED_SIDES:
        raise OrderRefused("transactionType must be BUY or SELL.")
    return value


def assert_order_type(order_type: str) -> str:
    value = str(order_type or "MARKET").upper()
    if value not in ALLOWED_ORDER_TYPES:
        raise OrderRefused("orderType must be MARKET or LIMIT.")
    return value


def assert_product(product: str) -> str:
    value = str(product or "MIS").upper()
    if value not in ALLOWED_PRODUCTS:
        raise OrderRefused("product must be MIS or NRML.")
    return value


def quantity_from_lots(lots: int, lot_size: int) -> int:
    """Lot size comes from the instrument master. Browser quantity is ignored."""
    if lots < 1:
        raise OrderRefused("lots must be at least 1.")
    if lot_size < 1:
        raise OrderRefused("Instrument lot size is unavailable. Refresh the instrument master.")
    return int(lots) * int(lot_size)


def market_protection_percent(value) -> float:
    """Zerodha MARKET orders need a positive protection percentage. Zero is not valid."""
    if value is None:
        raise OrderRefused("MARKET orders require a non-zero market protection field.")
    percent = float(value)
    if percent <= 0:
        raise OrderRefused("MARKET orders require a non-zero market protection field.")
    return percent


def chart_order_tag(client_order_id: str) -> str:
    digest = hashlib.sha1(str(client_order_id).encode("utf-8")).hexdigest()
    return f"C{digest}"[:KITE_TAG_MAX]


def display_status(raw: str | None, *, submitted: bool = False) -> str:
    key = str(raw or "").upper()
    if key in BROKER_STATUS_MAP:
        return BROKER_STATUS_MAP[key]
    if submitted and not key:
        return STATUS_SUBMITTED
    return key or STATUS_SUBMITTED


def live_unrealized_pnl(average: float | None, ltp: float | None, quantity: int) -> float | None:
    """(LTP − average) × signed quantity. Label as LIVE UNREALIZED P&L in the UI."""
    if average is None or ltp is None:
        return None
    return round((float(ltp) - float(average)) * int(quantity), 2)


def invested_value(average: float | None, quantity: int) -> float | None:
    if average is None:
        return None
    return round(abs(float(average) * int(quantity)), 2)


def current_value(ltp: float | None, quantity: int) -> float | None:
    if ltp is None:
        return None
    return round(abs(float(ltp) * int(quantity)), 2)


def three_strikes(strikes: list[float], atm: float | None, option_type: str) -> dict[str, float | None]:
    """ITM / ATM / OTM from listed strikes. CE and PE step in opposite directions."""
    return strike_ladder(strikes, atm, option_type)


def validate_hint(*, expected: dict, strike=None, tradingsymbol=None, quantity=None, option_type=None) -> None:
    """Reject browser-supplied identity fields that do not match the server contract."""
    if option_type is not None and str(option_type).upper() != expected["option_type"]:
        raise OrderRefused("optionType does not match the resolved contract.")
    if strike is not None and float(strike) != float(expected["strike"]):
        raise OrderRefused("strike does not match the current ATM ladder.")
    if tradingsymbol and str(tradingsymbol).upper() != str(expected["tradingsymbol"]).upper():
        raise OrderRefused("tradingsymbol does not match the resolved contract.")
    if quantity is not None and int(quantity) != int(expected["quantity"]):
        raise OrderRefused("quantity is derived from lot size and lots. Do not send a different quantity.")


def inside_window(now: time, start: time, stop: time) -> bool:
    return start <= now <= stop


def live_badge(enabled: bool) -> str:
    return "LIVE TRADING ENABLED" if enabled else "PAPER TRADING"


def parse_option_type(tradingsymbol: str) -> str | None:
    text = str(tradingsymbol or "").upper()
    if text.endswith("CE"):
        return "CE"
    if text.endswith("PE"):
        return "PE"
    return None

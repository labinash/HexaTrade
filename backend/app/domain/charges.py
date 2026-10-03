"""Zerodha-style F&O option charges. Rates come from configuration, not literals in callers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


TWOPLACE = Decimal("0.01")
FOUR = Decimal("0.0001")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(TWOPLACE, rounding=ROUND_HALF_UP)


def _dec(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class ChargeRate:
    charge_type: str
    side: str
    rate_percent: Decimal
    flat_amount: Decimal
    cap_amount: Decimal | None
    taxable: bool


@dataclass(frozen=True)
class ChargeBreakdown:
    brokerage: Decimal
    stt: Decimal
    transaction: Decimal
    sebi: Decimal
    gst: Decimal
    stamp: Decimal
    other: Decimal

    @property
    def total(self) -> Decimal:
        return money(
            self.brokerage + self.stt + self.transaction + self.sebi + self.gst + self.stamp + self.other
        )

    def as_dict(self) -> dict:
        return {
            "brokerage": self.brokerage,
            "stt": self.stt,
            "transaction": self.transaction,
            "sebi": self.sebi,
            "gst": self.gst,
            "stamp": self.stamp,
            "other": self.other,
            "total": self.total,
        }


def _zero() -> ChargeBreakdown:
    z = money(0)
    return ChargeBreakdown(z, z, z, z, z, z, z)


def _matches(rate: ChargeRate, side: str) -> bool:
    wanted = side.upper()
    return rate.side.upper() in {wanted, "BOTH"}


class ZerodhaChargeCalculator:
    """Applies configured rates. Does not place an order."""

    def __init__(self, rates: list[ChargeRate], gst_percent: Decimal = Decimal("18")):
        self.rates = list(rates)
        self.gst_percent = _dec(gst_percent)

    def calculate_buy(self, premium, quantity: int) -> ChargeBreakdown:
        return self._side("BUY", premium, quantity)

    def calculate_sell(self, premium, quantity: int) -> ChargeBreakdown:
        return self._side("SELL", premium, quantity)

    def calculate_round_trip(self, entry_premium, exit_premium, quantity: int) -> ChargeBreakdown:
        buy = self.calculate_buy(entry_premium, quantity)
        sell = self.calculate_sell(exit_premium, quantity)
        return self.combine(buy, sell)

    def combine(self, left: ChargeBreakdown, right: ChargeBreakdown) -> ChargeBreakdown:
        return ChargeBreakdown(
            brokerage=money(left.brokerage + right.brokerage),
            stt=money(left.stt + right.stt),
            transaction=money(left.transaction + right.transaction),
            sebi=money(left.sebi + right.sebi),
            gst=money(left.gst + right.gst),
            stamp=money(left.stamp + right.stamp),
            other=money(left.other + right.other),
        )

    def _side(self, side: str, premium, quantity: int) -> ChargeBreakdown:
        if quantity <= 0:
            return _zero()
        turnover = money(_dec(premium) * quantity)
        if turnover <= 0:
            return _zero()
        buckets = {
            "BROKERAGE": money(0),
            "STT": money(0),
            "EXCHANGE": money(0),
            "SEBI": money(0),
            "STAMP": money(0),
            "OTHER": money(0),
        }
        taxable = money(0)
        for rate in self.rates:
            if not _matches(rate, side):
                continue
            kind = rate.charge_type.upper()
            if kind == "GST":
                continue
            amount = _apply_rate(turnover, rate)
            key = kind if kind in buckets else "OTHER"
            buckets[key] = money(buckets[key] + amount)
            if rate.taxable:
                taxable = money(taxable + amount)
        gst = money(taxable * self.gst_percent / Decimal("100"))
        return ChargeBreakdown(
            brokerage=buckets["BROKERAGE"],
            stt=buckets["STT"],
            transaction=buckets["EXCHANGE"],
            sebi=buckets["SEBI"],
            gst=gst,
            stamp=buckets["STAMP"],
            other=buckets["OTHER"],
        )


def _apply_rate(turnover: Decimal, rate: ChargeRate) -> Decimal:
    percent = money(turnover * _dec(rate.rate_percent) / Decimal("100")) if rate.rate_percent else money(0)
    flat = money(rate.flat_amount) if rate.flat_amount else money(0)
    kind = rate.charge_type.upper()
    if kind == "BROKERAGE":
        cap = money(rate.cap_amount) if rate.cap_amount is not None else None
        # ₹20 per executed F&O options order, or 2.5% of premium if that is lower.
        if percent > 0 and cap is not None:
            return min(cap, percent)
        if cap is not None:
            return cap
        return flat or percent
    return money(percent + flat)


DEFAULT_OPTION_RATES = [
    ChargeRate("BROKERAGE", "BOTH", Decimal("2.5"), Decimal("0"), Decimal("20"), True),
    ChargeRate("STT", "SELL", Decimal("0.15"), Decimal("0"), None, False),
    ChargeRate("EXCHANGE", "BOTH", Decimal("0.05065"), Decimal("0"), None, True),
    ChargeRate("SEBI", "BOTH", Decimal("0.0001"), Decimal("0"), None, True),
    ChargeRate("STAMP", "BUY", Decimal("0.003"), Decimal("0"), None, False),
    ChargeRate("OTHER", "BOTH", Decimal("0.0001"), Decimal("0"), None, True),
]


def default_calculator() -> ZerodhaChargeCalculator:
    return ZerodhaChargeCalculator(DEFAULT_OPTION_RATES)

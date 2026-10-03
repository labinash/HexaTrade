from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PaperFill:
    price: float
    quantity: int
    charges: float


def slippage_price(side: str, ltp: float, slippage_points: float) -> float:
    if side.upper() == "BUY":
        return ltp + slippage_points
    return max(0.0, ltp - slippage_points)


def trade_charges(premium: float, quantity: int, brokerage_per_order: float, statutory_bps: float, sides: int = 2) -> float:
    turnover = abs(premium) * quantity * sides
    return brokerage_per_order * sides + turnover * statutory_bps / 10000


def simulate_fill(
    side: str,
    ltp: float | None,
    quantity: int,
    slippage_points: float,
    brokerage_per_order: float,
    statutory_bps: float,
    *,
    data_stale: bool,
) -> PaperFill | None:
    """Simulated fill. This module never submits a broker order."""
    if data_stale or ltp is None:
        return None
    price = slippage_price(side, ltp, slippage_points)
    charges = trade_charges(price, quantity, brokerage_per_order, statutory_bps, sides=1)
    return PaperFill(price=price, quantity=quantity, charges=charges)

"""Black-76 greeks on the front-month futures price.

Kite does not publish greeks, so these are model estimates. A delta from this
module describes modelled sensitivity to the underlying. It is not a
probability of profit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.chain import norm_cdf


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


@dataclass(frozen=True)
class Greeks:
    delta: float | None
    gamma: float | None
    theta_per_day: float | None
    vega_per_point: float | None

    @property
    def abs_delta(self) -> float | None:
        return None if self.delta is None else abs(self.delta)


EMPTY = Greeks(None, None, None, None)


def black76_greeks(
    futures: float | None,
    strike: float,
    years: float,
    rate: float,
    sigma: float | None,
    is_call: bool,
) -> Greeks:
    if futures is None or sigma is None or futures <= 0 or strike <= 0 or years <= 0 or sigma <= 0:
        return EMPTY
    vol_sqrt = sigma * math.sqrt(years)
    d1 = (math.log(futures / strike) + 0.5 * sigma * sigma * years) / vol_sqrt
    d2 = d1 - vol_sqrt
    discount = math.exp(-rate * years)
    pdf = norm_pdf(d1)
    if is_call:
        delta = discount * norm_cdf(d1)
        price = discount * (futures * norm_cdf(d1) - strike * norm_cdf(d2))
    else:
        delta = -discount * norm_cdf(-d1)
        price = discount * (strike * norm_cdf(-d2) - futures * norm_cdf(-d1))
    gamma = discount * pdf / (futures * vol_sqrt)
    vega = discount * futures * pdf * math.sqrt(years)
    theta_year = -discount * futures * pdf * sigma / (2 * math.sqrt(years)) + rate * price
    return Greeks(
        delta=delta,
        gamma=gamma,
        theta_per_day=theta_year / 365,
        vega_per_point=vega / 100,
    )


def years_to_expiry(days: float, minutes_left_today: float = 0) -> float:
    """Calendar-year fraction. Zero or negative returns 0 so callers fail closed."""
    total = max(days, 0) + max(minutes_left_today, 0) / (60 * 24)
    return max(total, 0) / 365

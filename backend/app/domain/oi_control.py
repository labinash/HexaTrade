"""OI concentration totals. Raw contracts only; lakhs are a display unit.

This is not a directional forecast. Call OI > Put OI means call-side
concentration in the selected strikes, nothing more.
"""

from __future__ import annotations

LAKH = 100_000.0


def sum_raw(values: list[float | None]) -> float:
    return float(sum(value for value in values if value is not None))


def difference_percent(call_oi: float, put_oi: float) -> float | None:
    denom = call_oi + put_oi
    if denom == 0:
        return None
    return ((call_oi - put_oi) / denom) * 100


def concentration(difference: float, threshold_raw: float) -> str:
    if abs(difference) < threshold_raw:
        return "BALANCED"
    return "CALL" if difference > 0 else "PUT"


def combined(structure: str, flow: str) -> str:
    if structure == "BALANCED" and flow == "BALANCED":
        return "BALANCED"
    if structure == "BALANCED":
        return flow
    if flow == "BALANCED":
        return structure
    if structure != flow:
        return "MIXED"
    return structure


def around_atm(strikes: list[float], atm: float | None, each_side: int) -> list[float]:
    ordered = sorted(set(strikes))
    if not ordered or atm is None or each_side < 0:
        return []
    try:
        index = ordered.index(atm)
    except ValueError:
        index = min(range(len(ordered)), key=lambda i: (abs(ordered[i] - atm), ordered[i]))
    start = max(0, index - each_side)
    end = min(len(ordered), index + each_side + 1)
    return ordered[start:end]

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SignalDecision:
    action: str
    reason: str


def classify_signal(previous_passed: bool, current_passed: bool) -> SignalDecision:
    """A condition set that stays true does not create another entry signal."""
    if current_passed and not previous_passed:
        return SignalDecision("NEW", "conditions transitioned to pass")
    if current_passed and previous_passed:
        return SignalDecision("DUPLICATE", "signal already active")
    return SignalDecision("NONE", "conditions are not all passing")

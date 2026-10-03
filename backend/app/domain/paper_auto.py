"""Paper auto-trade rules. Eligibility only — this module never places an order."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal

from app.domain.charges import money
from app.domain.dashboard_brief import pick_best, score_percent

PNL_FORMULA = (
    "BUY gross = (exitPrice − entryPrice) × quantity; "
    "SELL gross = (entryPrice − exitPrice) × quantity; "
    "NET = gross − brokerage − transactionCosts − slippage. "
    "HYPOTHETICAL / PAPER only. No broker orders."
)
OPTION_HISTORY_UNAVAILABLE = (
    "Option history unavailable for this period. Index close is being used as the futures proxy."
)


@dataclass
class PaperAutoConfig:
    enabled: bool = False
    min_signal_score: Decimal = Decimal("75")
    candidate_strike_range: int = 5
    max_open_positions: int = 1
    max_trades_per_day: int = 3
    max_daily_loss: Decimal = Decimal("5000")
    max_loss_per_trade: Decimal = Decimal("2000")
    square_off: time = time(15, 20)
    allow_reentry: bool = False
    cooldown_minutes: int = 15
    entry_price_source: str = "LTP"
    lots: int = 1
    min_hold_minutes: int = 5
    ce_enabled: bool = True
    pe_enabled: bool = True
    paper_instrument: str = "OPTIONS"
    paper_mode: str = "MANUAL"
    auto_paused: bool = False
    index_strategy: dict = field(default_factory=dict)


def eligible_score(passed: int, total: int, threshold: Decimal) -> bool:
    return Decimal(str(score_percent(passed, total))) >= threshold


def complete_setup(candidate: dict | None) -> bool:
    """Paper auto only enters a fully passed CE/PE setup, not a 75% watch."""
    return bool(candidate and candidate.get("is_strategy_valid"))


def aligned_with_tape(option_type: str | None, live_direction: str | None) -> bool:
    """CE/BUY only on a bullish tape, PE/SELL only on a bearish tape."""
    if not option_type or not live_direction:
        return False
    if live_direction == "BEARISH":
        return option_type in {"PE", "SELL"}
    if live_direction == "BULLISH":
        return option_type in {"CE", "BUY"}
    return False


def index_side_for_tape(live_direction: str | None) -> str | None:
    """Index futures paper: BUY on bullish tape, SELL on bearish tape."""
    if live_direction == "BULLISH":
        return "BUY"
    if live_direction == "BEARISH":
        return "SELL"
    return None


def side_enabled(ce_enabled: bool, pe_enabled: bool, option_type: str | None) -> bool:
    """OFF means no new entries for that side. Open positions are unchanged."""
    if option_type in {"CE", "BUY"}:
        return bool(ce_enabled)
    if option_type in {"PE", "SELL"}:
        return bool(pe_enabled)
    return False


def gated_sides(ce: dict | None, pe: dict | None, ce_enabled: bool, pe_enabled: bool) -> tuple[dict | None, dict | None]:
    """Drop disabled sides from new-entry and best-candidate selection."""
    return (ce if ce_enabled else None, pe if pe_enabled else None)


def choose_eligible_candidate(ce: dict | None, pe: dict | None, threshold: float) -> tuple[dict | None, str]:
    """Highest normalized score at or above the threshold. Not a live BUY."""
    rows = [row for row in (ce, pe) if row]
    if not rows:
        return None, "No candidate is available."
    return pick_best(rows, threshold)


def idempotency_key(session_date, underlying: str, option_type: str, strike, expiry) -> str:
    return f"paper|{session_date}|{underlying}|{option_type}|{strike}|{expiry}"


def pick_entry_price(quote: dict | None, source: str):
    if not quote:
        return None
    wanted = (source or "LTP").upper()
    if wanted == "ASK":
        return quote.get("ask") if quote.get("ask") is not None else quote.get("ltp")
    if wanted == "MID":
        bid, ask = quote.get("bid"), quote.get("ask")
        if bid is not None and ask is not None:
            return (Decimal(str(bid)) + Decimal(str(ask))) / Decimal("2")
        return quote.get("ltp")
    return quote.get("ltp")


def gross_pnl(exit_price, entry_price, quantity: int) -> Decimal:
    return money((Decimal(str(exit_price)) - Decimal(str(entry_price))) * quantity)


def signed_gross(exit_price, entry_price, quantity: int, *, short: bool) -> Decimal:
    """Long: exit − entry. Index SELL: entry − exit. Option PE stays long premium."""
    if short:
        return money((Decimal(str(entry_price)) - Decimal(str(exit_price))) * quantity)
    return gross_pnl(exit_price, entry_price, quantity)


def futures_gross(side: str, entry_price, exit_price, quantity: int) -> Decimal:
    return signed_gross(exit_price, entry_price, quantity, short=str(side or "").upper() in {"SELL", "PE"})


def slippage_cost(quantity: int, slippage_points=0) -> Decimal:
    return money(Decimal(str(abs(slippage_points or 0))) * int(quantity or 0))


def net_pnl(gross: Decimal, charges: Decimal) -> Decimal:
    return money(Decimal(str(gross)) - Decimal(str(charges)))


def futures_net(gross, *, brokerage=0, transaction_costs=0, slippage=0) -> Decimal:
    return money(
        Decimal(str(gross or 0))
        - Decimal(str(brokerage or 0))
        - Decimal(str(transaction_costs or 0))
        - Decimal(str(slippage or 0))
    )


def daily_summary(trades: list[dict], now: datetime | None = None) -> dict:
    open_rows = [row for row in trades if row.get("status") == "OPEN"]
    closed = [row for row in trades if str(row.get("status") or "").upper() not in {"OPEN", ""}]
    gross = money(sum((Decimal(str(row.get("gross_pnl") or 0)) for row in closed), Decimal("0")))
    charges = money(sum((Decimal(str(row.get("total_charges") or 0)) for row in closed), Decimal("0")))
    net = money(sum((Decimal(str(row.get("net_pnl") or 0)) for row in closed), Decimal("0")))
    open_mtm = money(sum((Decimal(str(row.get("net_pnl") or 0)) for row in open_rows), Decimal("0")))
    wins = sum(1 for row in closed if Decimal(str(row.get("net_pnl") or 0)) > 0)
    losses = sum(1 for row in closed if Decimal(str(row.get("net_pnl") or 0)) < 0)
    win_rate = money(Decimal(wins) * Decimal("100") / Decimal(len(closed))) if closed else None
    return {
        "trades": len(trades),
        "open": len(open_rows),
        "closed": len(closed),
        "winning_trades": wins,
        "losing_trades": losses,
        "gross_pnl": gross,
        "charges": charges,
        "net_pnl": money(net + open_mtm),
        "closed_net_pnl": net,
        "open_mtm": open_mtm,
        "win_rate": win_rate,
        "pnl_formula": PNL_FORMULA,
    }

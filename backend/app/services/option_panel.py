"""Dashboard option trading panel.

This module only assembles a payload. Every number in it is produced by an
existing service: the strategy evaluation supplies the direction and the
conditions, the strike selection engine supplies the candidate score, stop,
target and reward/risk, and the shared quote cache supplies the prices. No
strategy or scoring logic lives here.

The candidate score is a suitability ranking under the configured criteria. It
is not a probability of profit, and no strike offered here is safe or
guaranteed.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import now_ist
from app.db.models import Instrument, UnderlyingProfile
from app.domain.chain import strike_interval
from app.domain.oi_scanner import LIVE, STATE_LABELS, market_state
from app.domain.dashboard_brief import implied_market_direction
from app.domain.strategy import direction_state
from app.domain.strike_selection import (
    Candidate,
    OptionQuote,
    SelectionResult,
    atm_from,
    evaluate,
    strike_ladder,
)
from app.services.mapping import jsonable
from app.services.selection import candidate_payload, run_selection

LADDER = ("ITM", "ATM", "OTM")


def preferred_option(params) -> str:
    """Configured direction maps to a default option type. BULLISH stays CE."""
    return params.option_type()


def buy_signal(passed: bool, preferred: str) -> str:
    """BUY CE / BUY PE only when the existing 9-condition gate has passed."""
    return f"BUY {preferred}" if passed else "NO TRADE"


def panel(db: Session, runtime, symbol: str, expiry: date | None = None) -> dict:
    """The full payload for one underlying's tab."""
    result, view, quotes = run_selection(db, runtime, symbol, expiry, require_signal=False)
    params = view["params"]
    evaluation = view["evaluation"]
    direction, score = direction_state(evaluation, params)
    live = implied_market_direction(
        close=evaluation.close if evaluation else None,
        ema=evaluation.ema if evaluation else None,
        vwap=evaluation.vwap if evaluation else None,
        momentum=evaluation.momentum if evaluation else None,
    )
    preferred = preferred_option(params)
    if live == "BEARISH":
        preferred = "PE"
    elif live == "BULLISH":
        preferred = "CE"
    passed = bool(view.get("passed")) and live == params.direction

    # Same price the ranking used, so the header ATM, the cards and the mini
    # chain cannot disagree with each other by a strike.
    price = view.get("futures_ltp") if view.get("futures_ltp") is not None else view.get("ltp")
    strikes = sorted({quote.strike for quote in quotes})
    atm = result.atm if result.atm is not None else (atm_from(strikes, price) if price is not None else None)
    interval = result.strike_interval if result.strike_interval is not None else strike_interval(strikes)

    ladders = {kind: strike_ladder(strikes, atm, kind) for kind in ("CE", "PE")}
    scored = _score_both_sides(view, quotes)

    # Session hours come from the underlying's own profile, not a shared constant.
    profile = view.get("profile") or profile_of(db, symbol)
    session = LIVE
    if profile is not None:
        session = market_state(now_ist().time().replace(microsecond=0), profile.session_start, profile.session_end)
    closed = session != LIVE
    stale = bool(view.get("stale"))
    contracts = {
        kind: _side_map(_cards(ladders, kind, scored[kind], quotes))
        for kind in ("CE", "PE")
    }

    return {
        "underlying": symbol,
        "underlying_ltp": view.get("ltp"),
        "futures_ltp": view.get("futures_ltp"),
        "atm_strike": atm,
        "strike_interval": interval,
        "expiry": jsonable(result.expiry or expiry),
        "lot_size": _lot_size(quotes),
        "direction": live,
        "graded_direction": direction,
        "configured_direction": params.direction,
        "direction_score": score,
        "condition_count": evaluation.passed_conditions,
        "condition_total": evaluation.total_conditions,
        "signal_confirmed": passed,
        "signal": buy_signal(passed, preferred),
        "buy_signal": buy_signal(passed, preferred),
        "preferred_option": preferred,
        "option_side": preferred,
        "market_state": STATE_LABELS.get(session, session),
        "market_closed": closed,
        "stale": stale,
        "can_trade": not stale and not closed,
        "blocked_reason": _blocked_reason(stale, closed, price, result),
        "ladder": {kind: {label: ladders[kind][label] for label in LADDER} for kind in ladders},
        "options": contracts,
        "chain": _mini_chain(ladders, quotes, atm),
        "no_trade_reasons": result.no_trade_reasons,
        "timestamp": jsonable(now_ist().replace(tzinfo=None, microsecond=0)),
        "score_note": (
            "Direction score is the share of enabled strategy conditions currently passing. "
            "Candidate score ranks suitability under the configured criteria. Neither is a "
            "probability of profit, and no strike here is safe or guaranteed."
        ),
    }


def _score_both_sides(view: dict, quotes: list[OptionQuote]) -> dict[str, dict[int, Candidate]]:
    """Score CE and PE with the existing candidate engine.

    Direction here only selects which option type is ranked. It does not re-run
    the 9 strategy conditions. BULLISH still maps to CE and BEARISH to PE.
    """
    base = view.get("selection_underlying")
    config = view.get("selection_config")
    empty: dict[str, dict[int, Candidate]] = {"CE": {}, "PE": {}}
    if base is None or config is None:
        return empty
    clock = now_ist().replace(tzinfo=None)
    out = {}
    for kind, mapped in (("CE", "BULLISH"), ("PE", "BEARISH")):
        ranked = evaluate(replace(base, direction=mapped), quotes, config, clock, require_signal=False)
        out[kind] = {candidate.token: candidate for candidate in ranked.all_candidates}
    return out


def _side_map(cards: list[dict]) -> dict[str, dict]:
    return {card["moneyness_label"]: card for card in cards}


def _lot_size(quotes: list[OptionQuote]) -> int | None:
    """From the instrument master. Lot sizes change, so it is never assumed."""
    sizes = {quote.lot_size for quote in quotes if quote.lot_size}
    if len(sizes) == 1:
        return sizes.pop()
    return None


def _blocked_reason(stale: bool, closed: bool, price: float | None, result: SelectionResult) -> str:
    if price is None:
        return "LIVE PRICE UNAVAILABLE"
    if stale:
        return "STALE QUOTE"
    if closed:
        return "MARKET CLOSED"
    return ""


def candidate_for_token(view: dict, quotes: list[OptionQuote], token: int) -> Candidate | None:
    """Look up a contract on either side. Toggle-selected PE must still size."""
    scored = _score_both_sides(view, quotes)
    for by_token in scored.values():
        found = by_token.get(token)
        if found is not None:
            return found
    return None


def _cards(
    ladders: dict[str, dict[str, float | None]],
    side: str | None,
    by_token: dict[int, Candidate],
    quotes: list[OptionQuote],
) -> list[dict]:
    """One card per ladder rung on the strategy side, ranked by candidate score.

    The ITM/ATM/OTM label always travels with the card, so a high-scoring OTM
    strike ranking first is still recognisable as OTM. ATM is not forced to the
    top.
    """
    if side is None:
        return []
    rungs = ladders.get(side, {})
    cards = []
    for label in LADDER:
        strike = rungs.get(label)
        if strike is None:
            cards.append({"moneyness_label": label, "strike": None, "available": False,
                          "unavailable_reason": f"no strike one step {'in' if label == 'ITM' else 'out of'} the money in this expiry"})
            continue
        quote = next((q for q in quotes if q.strike == strike and q.option_type == side), None)
        if quote is None:
            cards.append({"moneyness_label": label, "strike": strike, "available": False,
                          "unavailable_reason": "contract not found in the instrument master"})
            continue
        candidate = by_token.get(quote.token)
        if candidate is None:
            oi_change = None
            if quote.oi is not None and quote.day_open_oi is not None:
                oi_change = quote.oi - quote.day_open_oi
            cards.append({
                "moneyness_label": label, "strike": strike, "option_type": side,
                "tradingsymbol": quote.tradingsymbol, "instrument_token": quote.token,
                "expiry": jsonable(quote.expiry),
                "ltp": quote.ltp, "bid": quote.bid, "ask": quote.ask,
                "oi": quote.oi, "oi_change": oi_change, "volume": quote.volume,
                "iv": quote.iv, "lot_size": quote.lot_size, "available": False,
                "unavailable_reason": "outside the scored window, widen strikes each side in the selection config",
            })
            continue
        card = candidate_payload(candidate)
        card["moneyness_label"] = label
        card["available"] = candidate.status == "PASS"
        card["max_lots"] = candidate.allowed_lots
        card["unavailable_reason"] = "" if candidate.status == "PASS" else "; ".join(candidate.reject_reasons)
        cards.append(card)
    ranked = [card for card in cards if card.get("score") is not None]
    ranked.sort(key=lambda card: -float(card["score"]))
    for position, card in enumerate(ranked, start=1):
        card["rank"] = position
    return cards


def _mini_chain(ladders: dict[str, dict[str, float | None]], quotes: list[OptionQuote], atm: float | None) -> list[dict]:
    """The three ladder strikes with both CE and PE.

    The opposite side is never hidden, so the full market context stays visible
    even once a direction is confirmed.
    """
    wanted = sorted({strike for rungs in ladders.values() for strike in rungs.values() if strike is not None})
    rows = []
    for strike in wanted:
        row: dict = {"strike": strike, "is_atm": atm is not None and strike == atm}
        for kind in ("CE", "PE"):
            quote = next((q for q in quotes if q.strike == strike and q.option_type == kind), None)
            label = next((name for name, value in ladders[kind].items() if value == strike), None)
            row[kind.lower()] = None if quote is None else {
                "tradingsymbol": quote.tradingsymbol,
                "instrument_token": quote.token,
                "ltp": quote.ltp,
                "bid": quote.bid,
                "ask": quote.ask,
                "oi": quote.oi,
                "volume": quote.volume,
                "iv": quote.iv,
                "moneyness_label": label,
            }
        rows.append(row)
    return rows


def profile_of(db: Session, symbol: str) -> UnderlyingProfile | None:
    return db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))


def instrument_lot_size(db: Session, token: int) -> int | None:
    """Authoritative lot size for one contract, read from the instrument master."""
    row = db.scalar(
        select(Instrument).where(Instrument.instrument_token == token).order_by(Instrument.id.desc())
    )
    if row is None or not row.lot_size:
        return None
    return int(row.lot_size)


__all__ = ["panel", "instrument_lot_size", "profile_of", "candidate_for_token", "preferred_option", "buy_signal", "STATE_LABELS"]

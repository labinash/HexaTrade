"""Dashboard decision briefing. Explains existing evaluations; it does not trade."""

from __future__ import annotations

from datetime import time


READY = "READY"
WAITING = "WAITING"
NOT_READY = "NOT READY"

BASIS_SHORT = {
    "EMA Trend": "EMA",
    "Momentum": "Futures",
    "VWAP": "VWAP",
    "RSI": "RSI",
    "OI Change": "OI",
    "Futures Volume": "Volume",
    "ATR": "ATR",
    "Candle": "Candle",
    "Time": "Time",
}


def implied_market_direction(
    *,
    close: float | None,
    ema: float | None,
    vwap: float | None,
    momentum: float | None,
) -> str:
    """Live structure from futures vs EMA/VWAP/momentum. Not the configured bias."""
    bull = 0
    bear = 0
    if close is not None and ema is not None and _clear_of_noise(close, ema):
        if close > ema:
            bull += 1
        elif close < ema:
            bear += 1
    if close is not None and vwap is not None:
        if close > vwap:
            bull += 1
        elif close < vwap:
            bear += 1
    if momentum is not None:
        if momentum > 0:
            bull += 1
        elif momentum < 0:
            bear += 1
    if bear > bull:
        return "BEARISH"
    if bull > bear:
        return "BULLISH"
    return "NEUTRAL"


def _clear_of_noise(close: float, level: float) -> bool:
    """Ignore a few points of EMA chop so a down day is not called bullish."""
    return abs(close - level) > abs(close) * 0.0005


def score_percent(passed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * passed / total, 2)


def market_status(
    *,
    clock: time,
    session_start: time,
    session_end: time,
    time_start: time,
    time_end: time,
    time_enabled: bool,
    stale: bool,
    has_futures: bool,
    has_spot: bool,
) -> str:
    """READY means the session and data are usable. It is not a BUY."""
    if stale:
        return NOT_READY
    if clock < session_start or clock > session_end:
        return NOT_READY
    if not has_futures and not has_spot:
        return WAITING
    window_ok = (not time_enabled) or (time_start <= clock <= time_end)
    if window_ok and has_futures:
        return READY
    return WAITING


def direction_basis(conditions: list[dict]) -> list[dict]:
    """Pass-through of the existing index evaluation. No second engine."""
    out = []
    for item in conditions:
        status = item.get("status") or ""
        name = item.get("name")
        out.append(
            {
                "name": name,
                "short_name": BASIS_SHORT.get(name or "", name),
                "passed": status == "PASS",
                "status": status,
                "current": item.get("current"),
                "required": item.get("required"),
                "failure_reason": item.get("failure_reason") or "",
                "is_directional": bool(item.get("is_directional")),
            }
        )
    return out


def candidate_label(*, passed: bool, score: float, threshold: float) -> str:
    if passed:
        return "SETUP VALID"
    if score >= threshold:
        return "WATCH"
    return "BELOW THRESHOLD"


def pick_best(candidates: list[dict], threshold: float) -> tuple[dict | None, str]:
    """Highest normalized score at or above the threshold. Not a BUY by itself."""
    eligible = [row for row in candidates if (row.get("score_percent") or 0) >= threshold]
    if not eligible:
        return None, "No side reaches the configured candidate threshold."
    ranked = sorted(
        eligible,
        key=lambda row: (
            row.get("score_percent") or 0,
            row.get("passed_count") or 0,
            -abs(row.get("atm_distance") or 0),
        ),
        reverse=True,
    )
    return ranked[0], ""


def best_setup_copy(
    *,
    ce_enabled: bool,
    pe_enabled: bool,
    ce: dict | None,
    pe: dict | None,
    threshold: float,
) -> dict:
    """Eligible new setup from enabled sides only. Not a broker order."""
    if not ce_enabled and not pe_enabled:
        return {
            "label": "NO TRADE",
            "option_type": None,
            "score_percent": None,
            "passed_count": None,
            "total_count": None,
            "reason": "CE and PE strategies are off. No new entries will be generated.",
        }
    rows = []
    if ce_enabled and ce:
        rows.append(ce)
    if pe_enabled and pe:
        rows.append(pe)
    best, reason = pick_best(rows, threshold)
    if best is None:
        return {
            "label": "NO TRADE",
            "option_type": None,
            "score_percent": None,
            "passed_count": None,
            "total_count": None,
            "reason": reason or f"No enabled strategy has reached the {threshold:g}% entry threshold.",
        }
    return {
        "label": f"BUY {best.get('option_type')}",
        "option_type": best.get("option_type"),
        "score_percent": best.get("score_percent"),
        "passed_count": best.get("passed_count"),
        "total_count": best.get("total_count"),
        "reason": "",
    }


def readiness_copy(ce_passed: bool, pe_passed: bool) -> str:
    if ce_passed and pe_passed:
        return "Both setups satisfy their configured conditions."
    if ce_passed:
        return "CE setup satisfies all configured conditions."
    if pe_passed:
        return "PE setup satisfies all configured conditions."
    return "No strategy currently satisfies all required conditions."


INDICATOR_CATALOG = (
    ("EMA", ("EMA Trend",)),
    ("Futures price direction", ("Momentum",)),
    ("Open interest", ("OI Change",)),
    ("Volume", ("Futures Volume",)),
    ("VWAP", ("VWAP", "Futures VWAP")),
    ("RSI", ("RSI", "Futures RSI")),
    ("ATR", ("ATR", "Futures ATR")),
    ("Candle confirmation", ("Candle",)),
    ("Trading time", ("Time", "Trading Time")),
    ("Premium trend", ("Premium Trend",)),
    ("Premium momentum", ("Premium Momentum",)),
    ("Option OI", ("Option OI",)),
    ("Premium candle", ("Premium Candle",)),
)


def confirmed_trend(market_status: str | None, displayed: str | None) -> str:
    """Live tape trend. NOT READY when session data is unusable. Not a BUY."""
    if market_status == NOT_READY:
        return "NOT READY"
    if displayed in {"BULLISH", "BEARISH", "NEUTRAL"}:
        return displayed
    if market_status == WAITING and not displayed:
        return "NOT READY"
    return "NEUTRAL"


def trend_confidence(
    trend: str,
    *,
    close: float | None,
    ema: float | None,
    vwap: float | None,
    momentum: float | None,
) -> dict:
    votes: list[str] = []
    if close is not None and ema is not None and _clear_of_noise(close, ema):
        votes.append("BULLISH" if close > ema else "BEARISH")
    if close is not None and vwap is not None:
        votes.append("BULLISH" if close > vwap else "BEARISH")
    if momentum is not None:
        if momentum > 0:
            votes.append("BULLISH")
        elif momentum < 0:
            votes.append("BEARISH")
        else:
            votes.append("NEUTRAL")
    total = len(votes)
    if not total:
        return {"passed": 0, "total": 0, "percent": None, "votes": []}
    if trend in {"BULLISH", "BEARISH"}:
        passed = sum(1 for vote in votes if vote == trend)
    elif trend == "NEUTRAL":
        bull = votes.count("BULLISH")
        bear = votes.count("BEARISH")
        passed = total - abs(bull - bear)
    else:
        passed = 0
    return {"passed": passed, "total": total, "percent": round(100.0 * passed / total, 1), "votes": votes}


def preferred_side_for(trend: str) -> str | None:
    if trend == "BULLISH":
        return "CE"
    if trend == "BEARISH":
        return "PE"
    return None


def preferred_label(trend: str) -> str:
    if trend == "BULLISH":
        return "PREFERRED: CE — BULLISH TREND"
    if trend == "BEARISH":
        return "PREFERRED: PE — BEARISH TREND"
    return "NO PREFERRED CONTRACT — MARKET NOT CONFIRMED"


def contract_gaps(contract: dict | None) -> list[str]:
    if not contract:
        return ["selected option contract"]
    missing = []
    if not contract.get("tradingsymbol") and not contract.get("option_symbol"):
        missing.append("symbol")
    if contract.get("strike") is None:
        missing.append("strike")
    if not contract.get("expiry"):
        missing.append("expiry")
    if contract.get("ltp") is None and contract.get("premium_ltp") is None:
        missing.append("premium LTP")
    if contract.get("bid") is None or contract.get("ask") is None:
        missing.append("bid/ask spread")
    if contract.get("oi") is None and contract.get("volume") is None:
        missing.append("liquidity (OI/volume)")
    if contract.get("lot_size") is None:
        missing.append("lot size")
    return missing


def dashboard_decision(
    *,
    trend: str,
    market_status: str | None,
    side: str | None,
    result: dict | None,
    enabled: bool,
    threshold: float,
    gaps: list[str],
    missing_notes: list[str],
) -> dict:
    """BUY only after strategy pass + threshold. Trend alone is never a BUY."""
    if market_status == NOT_READY or trend == "NOT READY":
        return {
            "label": "DATA NOT READY",
            "buy": False,
            "side": None,
            "reason": "; ".join(missing_notes) or "Required session data is not usable. Market Ready is not a BUY.",
        }
    if side is None:
        return {
            "label": "NO TRADE",
            "buy": False,
            "side": None,
            "reason": "NO PREFERRED CONTRACT — MARKET NOT CONFIRMED. " + (
                "; ".join(missing_notes) or "EMA, VWAP, and momentum do not confirm a direction."
            ),
        }
    if not enabled:
        return {
            "label": "NO TRADE",
            "buy": False,
            "side": side,
            "reason": f"{side} strategy is off. No new entries.",
        }
    if not result:
        return {
            "label": "DATA NOT READY",
            "buy": False,
            "side": side,
            "reason": f"{side} contract or premium series is unavailable.",
        }
    score = float(result.get("score_percent") or 0)
    passed = bool(result.get("passed"))
    if gaps:
        return {
            "label": f"{side} WATCH",
            "buy": False,
            "side": side,
            "reason": f"Preferred {side} contract is missing {', '.join(gaps)}. Trend is not a BUY.",
        }
    if passed and score >= threshold:
        return {
            "label": "BUY",
            "buy": True,
            "side": side,
            "reason": f"{side} meets every mandatory condition and the {threshold:g}% threshold. Hypothetical / paper only.",
        }
    if score >= threshold:
        return {
            "label": f"{side} WATCH",
            "buy": False,
            "side": side,
            "reason": f"{side} is at {score:g}% but mandatory conditions are incomplete. Trend alone is not a BUY.",
        }
    return {
        "label": f"{side} WATCH" if score > 0 else "NO TRADE",
        "buy": False,
        "side": side,
        "reason": f"{side} is at {score:g}%, below the {threshold:g}% threshold. Trend alone is not a BUY.",
    }


def indicator_summary(index_rows: list[dict], option_rows: list[dict] | None) -> dict:
    by_name: dict[str, dict] = {}
    for row in index_rows or []:
        if row.get("name"):
            by_name[str(row["name"])] = row
    for row in option_rows or []:
        if row.get("name"):
            by_name[str(row["name"])] = row
    items = []
    for label, aliases in INDICATOR_CATALOG:
        src = next((by_name[name] for name in aliases if name in by_name), None)
        items.append(_indicator_item(label, src))
    applicable = [row for row in items if row["state"] in {"PASS", "FAIL"}]
    green = sum(1 for row in applicable if row["state"] == "PASS")
    total = len(applicable)
    percent = score_percent(green, total) if total else None
    return {
        "items": items,
        "green": green,
        "total": total,
        "percent": percent,
        "text": f"Green indicators: {green} / {total}",
        "percent_text": f"Green percentage: {percent:.1f}%" if percent is not None else "Green percentage: —",
    }


def _indicator_item(label: str, src: dict | None) -> dict:
    if src is None:
        return {"name": label, "state": "NA", "mark": "–", "current": None, "required": None, "reason": "Not applicable or not returned for this side."}
    status = str(src.get("status") or "")
    if status in {"OFF", "DATA UNAVAILABLE", "UNAVAILABLE"} or src.get("passed") is None and status not in {"PASS", "FAIL"}:
        return {
            "name": label,
            "state": "NA",
            "mark": "–",
            "current": src.get("current"),
            "required": src.get("required"),
            "reason": src.get("failure_reason") or status or "Unavailable",
        }
    ok = status == "PASS" or src.get("passed") is True
    return {
        "name": label,
        "state": "PASS" if ok else "FAIL",
        "mark": "✓" if ok else "✕",
        "current": src.get("current"),
        "required": src.get("required"),
        "reason": None if ok else (src.get("failure_reason") or "Failed"),
    }


def unconfirmed_notes(market_status: str | None, snapshot: dict | None, trend: str) -> list[str]:
    notes = []
    snap = snapshot or {}
    if market_status == NOT_READY:
        notes.append("Market data is not ready (session closed, stale ticks, or missing feed).")
    if market_status == WAITING:
        notes.append("Waiting for usable futures/spot in the configured window. Market Ready is not a BUY.")
    if snap.get("futures") is None:
        notes.append("Futures LTP is missing.")
    if snap.get("ema20") is None:
        notes.append("EMA is missing.")
    if snap.get("vwap") is None:
        notes.append("VWAP is missing.")
    if trend == "NEUTRAL":
        notes.append("EMA, VWAP, and momentum do not confirm BULLISH or BEARISH.")
    return notes

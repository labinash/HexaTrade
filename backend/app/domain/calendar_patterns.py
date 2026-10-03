"""Weekday and calendar-pattern stats. Historical / paper only. Does not trade."""

from __future__ import annotations

import calendar
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from app.domain.chart_signals import _unix as chart_marker_unix
from app.domain.paper_auto import PNL_FORMULA


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
MIN_SAMPLE = 20
OOS_FRACTION = 0.30
FLAT_PCT = 0.10
GAP_PCT = 0.15
PRICE_BASIS = "INDEX_CLOSE_FUTURES_PROXY"
PROXY_NOTE = "Index close is being used as the futures proxy; actual futures P&L may differ."
DISCLAIMER = (
    "HYPOTHETICAL / PAPER only. A positive historical average is not a prediction. "
    "No broker orders. Patterns are not converted into live strategies."
)


def session_marker_unix(stamp: datetime) -> int:
    """Unix seconds of a stored candle. Matches chart markers (naive timestamps treated as UTC)."""
    return chart_marker_unix(stamp)


def ist_wall_date(stamp: datetime) -> date:
    """Calendar date of a marker in Asia/Kolkata after the same UTC interpretation the chart uses."""
    aware = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)
    return (aware + timedelta(hours=5, minutes=30)).date()


def weekday_name(day: date) -> str:
    return WEEKDAYS[day.weekday()] if day.weekday() < 5 else "Weekend"


def week_of_month(day: date) -> str:
    ordinal = ((day.day - 1) // 7) + 1
    last_day = calendar.monthrange(day.year, day.month)[1]
    if day.day + 7 > last_day:
        return "Last"
    return ("First", "Second", "Third", "Fourth", "Last")[min(ordinal, 5) - 1]


def trading_weekday_ordinal(day: date, sessions: Iterable[date]) -> str:
    """1st/2nd/3rd/4th/Last occurrence of this weekday in the month among stored sessions."""
    same = sorted(item for item in sessions if item.year == day.year and item.month == day.month and item.weekday() == day.weekday())
    if not same:
        return "Unknown"
    index = same.index(day) + 1
    if day == same[-1]:
        return "Last"
    labels = ("First", "Second", "Third", "Fourth", "Last")
    return labels[min(index, 5) - 1]


def iso_week_key(day: date) -> tuple[int, int]:
    iso = day.isocalendar()
    return iso.year, iso.week


def first_last_of_weeks(sessions: list[date]) -> tuple[set[date], set[date]]:
    groups: dict[tuple[int, int], list[date]] = {}
    for day in sessions:
        groups.setdefault(iso_week_key(day), []).append(day)
    firsts, lasts = set(), set()
    for days in groups.values():
        ordered = sorted(days)
        firsts.add(ordered[0])
        lasts.add(ordered[-1])
    return firsts, lasts


def first_last_of_months(sessions: list[date]) -> tuple[set[date], set[date]]:
    groups: dict[tuple[int, int], list[date]] = {}
    for day in sessions:
        groups.setdefault((day.year, day.month), []).append(day)
    firsts, lasts = set(), set()
    for days in groups.values():
        ordered = sorted(days)
        firsts.add(ordered[0])
        lasts.add(ordered[-1])
    return firsts, lasts


def missing_weekdays(sessions: list[date]) -> list[date]:
    """Weekdays inside the stored range that have no session (holidays / gaps)."""
    if not sessions:
        return []
    present = set(sessions)
    missing = []
    cursor = min(sessions)
    end = max(sessions)
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in present:
            missing.append(cursor)
        cursor += timedelta(days=1)
    return missing


def split_in_out(rows: list, fraction: float = OOS_FRACTION) -> tuple[list, list]:
    if len(rows) < 2:
        return list(rows), []
    cut = max(1, min(len(rows) - 1, int(round(len(rows) * (1.0 - fraction)))))
    return rows[:cut], rows[cut:]


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(statistics.pstdev(values), 6)


def mean_ci(values: list[float]) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    mu = sum(values) / len(values)
    se = statistics.pstdev(values) / math.sqrt(len(values))
    return round(mu - 1.96 * se, 6), round(mu + 1.96 * se, 6)


def profit_factor(values: list[float]) -> float | None:
    gains = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses == 0:
        return None if gains == 0 else None
    return round(gains / losses, 4)


def max_drawdown(returns: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return round(dd, 6)


def compounded(returns_pct: list[float]) -> float:
    equity = 1.0
    for value in returns_pct:
        equity *= 1.0 + (value / 100.0)
    return round((equity - 1.0) * 100.0, 6)


def outlier_dominated(values: list[float]) -> bool:
    if len(values) < 5:
        return False
    ordered = sorted(abs(v) for v in values)
    mid = statistics.median(ordered)
    if mid <= 0:
        return False
    return ordered[-1] >= 5 * mid or ordered[-2] >= 5 * mid


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = rank
            i = j + 1
        return out

    return pearson(ranks(xs), ranks(ys))


def classify_move(pct: float | None) -> str:
    if pct is None:
        return "UNKNOWN"
    if pct > FLAT_PCT:
        return "BULLISH"
    if pct < -FLAT_PCT:
        return "BEARISH"
    return "FLAT"


def classify_gap(pct: float | None) -> str:
    if pct is None:
        return "UNKNOWN"
    if pct > GAP_PCT:
        return "GAP_UP"
    if pct < -GAP_PCT:
        return "GAP_DOWN"
    return "FLAT_OPEN"


def reliability(n: int, in_mean: float | None, out_mean: float | None, out_n: int, drawdown: float, cum: float) -> str:
    if n < MIN_SAMPLE:
        return "Insufficient sample"
    if out_n <= 0:
        return "In-sample only"
    if out_n < MIN_SAMPLE:
        return "In-sample only"
    if in_mean is not None and out_mean is not None and in_mean > 0 and out_mean <= 0:
        return "Failed out-of-sample"
    if in_mean is not None and out_mean is not None and in_mean < 0 and out_mean >= 0:
        return "Failed out-of-sample"
    if drawdown > 0 and cum > 0 and drawdown >= abs(cum) * 0.8:
        return "High drawdown"
    if n >= 50 and out_mean is not None and in_mean is not None and (in_mean > 0) == (out_mean > 0):
        return "Stronger evidence"
    return "Weak evidence"


def session_stats(rows: list[dict], *, min_sample: int = MIN_SAMPLE) -> dict:
    oc = [float(row["oc_pct"]) for row in rows if row.get("oc_pct") is not None]
    cc = [float(row["cc_pct"]) for row in rows if row.get("cc_pct") is not None]
    gaps = [float(row["gap_pct"]) for row in rows if row.get("gap_pct") is not None]
    ranges = [float(row["range_pct"]) for row in rows if row.get("range_pct") is not None]
    vols = [float(row["volume"]) for row in rows if row.get("volume") is not None]
    vwap_dev = [float(row["vwap_dev_pct"]) for row in rows if row.get("vwap_dev_pct") is not None]
    mfe = [float(row["mfe_pct"]) for row in rows if row.get("mfe_pct") is not None]
    mae = [float(row["mae_pct"]) for row in rows if row.get("mae_pct") is not None]
    net_pts = [float(row.get("oc_net_pct") if row.get("oc_net_pct") is not None else row["oc_pct"]) for row in rows if row.get("oc_pct") is not None]
    positive = sum(1 for v in oc if v > FLAT_PCT)
    negative = sum(1 for v in oc if v < -FLAT_PCT)
    flat = sum(1 for v in oc if abs(v) <= FLAT_PCT)
    ins, oos = split_in_out(rows)
    in_oc = [float(row["oc_pct"]) for row in ins if row.get("oc_pct") is not None]
    out_oc = [float(row["oc_pct"]) for row in oos if row.get("oc_pct") is not None]
    in_mean, out_mean = mean(in_oc), mean(out_oc)
    low, high = mean_ci(oc)
    n = len(rows)
    status = reliability(n, in_mean, out_mean, len(out_oc), max_drawdown(cc), compounded(cc))
    if n < min_sample:
        status = "Insufficient sample"
    return {
        "sessions": n,
        "valid_sessions": len(oc),
        "positive_sessions": positive,
        "negative_sessions": negative,
        "flat_sessions": flat,
        "positive_session_pct": round(100.0 * positive / len(oc), 2) if oc else None,
        "avg_open_to_close_pct": mean(oc),
        "median_open_to_close_pct": median(oc),
        "avg_close_to_close_pct": mean(cc),
        "median_close_to_close_pct": median(cc),
        "avg_opening_gap_pct": mean(gaps),
        "avg_intraday_range_pct": mean(ranges),
        "median_intraday_range_pct": median(ranges),
        "volatility": stdev(oc),
        "avg_volume": mean(vols),
        "avg_vwap_deviation_pct": mean(vwap_dev),
        "largest_gain_pct": round(max(oc), 6) if oc else None,
        "largest_loss_pct": round(min(oc), 6) if oc else None,
        "profit_factor": profit_factor(oc),
        "cumulative_return_pct": compounded(cc) if cc else None,
        "net_after_costs_pct": mean(net_pts),
        "max_drawdown_pct": max_drawdown(cc) if cc else 0.0,
        "avg_mfe_pct": mean(mfe),
        "avg_mae_pct": mean(mae),
        "mean_ci_low": low,
        "mean_ci_high": high,
        "sample_sufficient": n >= min_sample,
        "outlier_dominated": outlier_dominated(oc),
        "in_sample_sessions": len(ins),
        "out_sample_sessions": len(oos),
        "in_sample_avg_return_pct": in_mean,
        "out_sample_avg_return_pct": out_mean,
        "in_sample_positive_pct": round(100.0 * sum(1 for v in in_oc if v > FLAT_PCT) / len(in_oc), 2) if in_oc else None,
        "out_sample_positive_pct": round(100.0 * sum(1 for v in out_oc if v > FLAT_PCT) / len(out_oc), 2) if out_oc else None,
        "reliability": status,
        "oos_failed": status == "Failed out-of-sample",
        "price_basis": PRICE_BASIS,
        "return_basis": "open_to_close percent unless labelled close-to-close",
        "pnl_formula": PNL_FORMULA,
        "dates": [
            row["trading_date"].isoformat() if hasattr(row.get("trading_date"), "isoformat") else str(row.get("trading_date"))
            for row in rows
            if row.get("trading_date") is not None
        ],
    }


def attach_session_returns(sessions: list[dict], *, slippage_points: float = 0.5) -> list[dict]:
    """Chronological. Previous close only from earlier sessions. No lookahead."""
    ordered = sorted(sessions, key=lambda row: row["trading_date"])
    prev_close = None
    ranges: list[float] = []
    out = []
    for row in ordered:
        open_px = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        volume = float(row.get("volume") or 0)
        vwap = float(row["vwap"]) if row.get("vwap") else None
        oc_pct = ((close - open_px) / open_px) * 100.0 if open_px else None
        cc_pct = ((close - prev_close) / prev_close) * 100.0 if prev_close else None
        gap_pct = ((open_px - prev_close) / prev_close) * 100.0 if prev_close else None
        range_pct = ((high - low) / open_px) * 100.0 if open_px else None
        slip_pct = (abs(slippage_points) * 2.0 / open_px) * 100.0 if open_px else 0.0
        oc_net = (oc_pct - slip_pct) if oc_pct is not None else None
        vwap_dev = ((close - vwap) / vwap) * 100.0 if vwap else None
        mfe = ((high - open_px) / open_px) * 100.0 if open_px else None
        mae = ((open_px - low) / open_px) * 100.0 if open_px else None
        prev_range = ranges[-1] if ranges else None
        med_range = statistics.median(ranges[-20:]) if len(ranges) >= 5 else None
        vol_regime = "UNKNOWN"
        if prev_range is not None and med_range:
            vol_regime = "HIGH_VOL" if prev_range >= med_range else "LOW_VOL"
        item = dict(row)
        item.update(
            {
                "oc_pct": None if oc_pct is None else round(oc_pct, 6),
                "cc_pct": None if cc_pct is None else round(cc_pct, 6),
                "gap_pct": None if gap_pct is None else round(gap_pct, 6),
                "range_pct": None if range_pct is None else round(range_pct, 6),
                "oc_net_pct": None if oc_net is None else round(oc_net, 6),
                "vwap_dev_pct": None if vwap_dev is None else round(vwap_dev, 6),
                "mfe_pct": None if mfe is None else round(mfe, 6),
                "mae_pct": None if mae is None else round(mae, 6),
                "prev_direction": classify_move(out[-1]["oc_pct"] if out else None),
                "gap_class": classify_gap(gap_pct),
                "vol_regime": vol_regime,
                "price_basis": PRICE_BASIS,
            }
        )
        if range_pct is not None:
            ranges.append(range_pct)
        out.append(item)
        prev_close = close
    return out


def annotate_calendar(sessions: list[dict], expiries: set[date] | None) -> list[dict]:
    days = [row["trading_date"] for row in sessions]
    first_w, last_w = first_last_of_weeks(days)
    first_m, last_m = first_last_of_months(days)
    expiry_set = expiries or set()
    trading = sorted(days)
    out = []
    for row in sessions:
        day = row["trading_date"]
        prox = "UNAVAILABLE"
        if expiry_set:
            prox = "NON_EXPIRY"
            if day in expiry_set:
                prox = "EXPIRY_DAY"
            else:
                idx = trading.index(day)
                prev_day = trading[idx - 1] if idx > 0 else None
                next_day = trading[idx + 1] if idx + 1 < len(trading) else None
                if prev_day in expiry_set:
                    prox = "ONE_AFTER_EXPIRY"
                elif next_day in expiry_set:
                    prox = "ONE_BEFORE_EXPIRY"
        item = dict(row)
        item.update(
            {
                "weekday": weekday_name(day),
                "week_of_month": week_of_month(day),
                "weekday_ordinal": trading_weekday_ordinal(day, days),
                "month": MONTHS[day.month - 1],
                "month_num": day.month,
                "year": day.year,
                "quarter": f"Q{((day.month - 1) // 3) + 1}",
                "is_first_of_week": day in first_w,
                "is_last_of_week": day in last_w,
                "is_first_of_month": day in first_m,
                "is_last_of_month": day in last_m,
                "expiry_proximity": prox,
            }
        )
        out.append(item)
    return out


def group_stats(rows: list[dict], key: str, min_sample: int = MIN_SAMPLE) -> list[dict]:
    buckets: dict[str, list] = {}
    for row in rows:
        buckets.setdefault(str(row.get(key) or ""), []).append(row)
    out = []
    for name, items in buckets.items():
        stats = session_stats(items, min_sample=min_sample)
        stats["group"] = name
        stats["key"] = key
        out.append(stats)
    return out


def pattern_card(definition: str, instrument: str, date_range: str, condition: str, stats: dict, groups_tested: int) -> dict:
    return {
        "pattern_definition": definition,
        "instrument": instrument,
        "date_range": date_range,
        "condition": condition,
        "observations": stats.get("valid_sessions") or stats.get("sessions") or 0,
        "average_result": stats.get("avg_open_to_close_pct"),
        "median_result": stats.get("median_open_to_close_pct"),
        "win_rate": stats.get("positive_session_pct"),
        "volatility": stats.get("volatility"),
        "net_result_after_costs": stats.get("net_after_costs_pct"),
        "confidence_interval": [stats.get("mean_ci_low"), stats.get("mean_ci_high")],
        "sample_sufficient": stats.get("sample_sufficient"),
        "statistically_reliable": stats.get("reliability") == "Stronger evidence",
        "works_out_of_sample": bool(stats.get("out_sample_avg_return_pct") is not None and stats.get("in_sample_avg_return_pct") is not None and (stats["out_sample_avg_return_pct"] > 0) == (stats["in_sample_avg_return_pct"] > 0) and stats.get("reliability") not in {"Failed out-of-sample", "Insufficient sample"}),
        "reliability": stats.get("reliability"),
        "oos_warning": (
            "Pattern looks profitable in-sample but fails out-of-sample."
            if stats.get("oos_failed")
            else None
        ),
        "outlier_dominated": stats.get("outlier_dominated"),
        "groups_tested": groups_tested,
        "multiple_testing_note": (
            f"{groups_tested} pattern groups were tested. Some will look strong by chance. "
            "This is not a trading signal."
        ),
        "full_sample": stats,
        "in_sample": {
            "sessions": stats.get("in_sample_sessions"),
            "avg_return_pct": stats.get("in_sample_avg_return_pct"),
            "positive_pct": stats.get("in_sample_positive_pct"),
        },
        "out_of_sample": {
            "sessions": stats.get("out_sample_sessions"),
            "avg_return_pct": stats.get("out_sample_avg_return_pct"),
            "positive_pct": stats.get("out_sample_positive_pct"),
        },
        "dates": stats.get("dates") or [],
        "price_basis": PRICE_BASIS,
        "disclaimer": DISCLAIMER,
        "proxy_note": PROXY_NOTE,
    }


def risk_adjusted(avg: float | None, vol: float | None) -> float | None:
    if avg is None or vol in (None, 0):
        return None
    return round(avg / vol, 4)

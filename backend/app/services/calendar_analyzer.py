"""Load stored INDEX sessions and assemble calendar-pattern reports. Paper only."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import to_ist
from app.db.models import HistoricalPaperRun, HistoricalPaperTrade, Instrument, MarketHistoricalData, PaperTrade
from app.domain.calendar_patterns import (
    DISCLAIMER,
    FLAT_PCT,
    MIN_SAMPLE,
    MONTHS,
    PRICE_BASIS,
    PROXY_NOTE,
    WEEKDAYS,
    annotate_calendar,
    attach_session_returns,
    group_stats,
    missing_weekdays,
    pattern_card,
    pearson,
    risk_adjusted,
    session_stats,
    spearman,
)
from app.domain.index_strategy import index_paper_net, performance_from_trades
from app.domain.paper_auto import OPTION_HISTORY_UNAVAILABLE, PNL_FORMULA
from app.services.historical_repo import stored_coverage


def analyze_calendar(
    db: Session,
    *,
    instrument: str = "BOTH",
    date_from: date | None = None,
    date_to: date | None = None,
    timeframe: str = "1Minute",
    weekday: str | None = None,
    week_of_month: str | None = None,
    weekday_ordinal: str | None = None,
    month: str | None = None,
    quarter: str | None = None,
    expiry_proximity: str | None = None,
    regime: str | None = None,
    min_sample: int = MIN_SAMPLE,
) -> dict:
    symbols = ["NIFTY", "SENSEX"] if instrument.upper() in {"BOTH", ""} else [instrument.upper()]
    for symbol in symbols:
        if symbol not in {"NIFTY", "SENSEX"}:
            raise ValueError("Instrument must be NIFTY, SENSEX or both")
    cover = {row["instrument"]: row for row in stored_coverage(db, "1Minute")}
    start = date_from or min((date.fromisoformat(row["from_date"]) for row in cover.values() if row.get("from_date")), default=None)
    end = date_to or max((date.fromisoformat(row["to_date"]) for row in cover.values() if row.get("to_date")), default=None)
    if start is None or end is None:
        return {
            "error": "No stored INDEX sessions.",
            "disclaimer": DISCLAIMER,
            "coverage": list(cover.values()),
        }
    if end < start:
        raise ValueError("To date is before from date")
    min_n = max(1, int(min_sample or MIN_SAMPLE))
    expiries = _listed_expiries(db)
    expiry_available = bool(expiries)
    reports = {}
    for symbol in symbols:
        sessions = _daily_sessions(db, symbol, start, end, timeframe)
        reports[symbol] = _instrument_report(
            db,
            symbol,
            sessions,
            expiries.get(symbol, set()) if expiry_available else set(),
            expiry_available,
            start,
            end,
            weekday,
            week_of_month,
            weekday_ordinal,
            month,
            quarter,
            expiry_proximity,
            regime,
            min_n,
        )
    comparison = None
    if "NIFTY" in reports and "SENSEX" in reports:
        comparison = _aligned_comparison(reports["NIFTY"]["sessions"], reports["SENSEX"]["sessions"], min_n)
    ranking = _rank_patterns([item for report in reports.values() for item in report.get("patterns") or []], min_n)
    for report in reports.values():
        report.pop("sessions", None)
        report["patterns"] = []
        for table in (report.get("weekday_by_month") or [], report.get("week_of_month") or [], report.get("weekday_by_year") or [], report.get("market_conditions") or [], report.get("expiry") or []):
            for row in table:
                row.pop("dates", None)
    return {
        "timezone": "Asia/Kolkata",
        "price_basis": PRICE_BASIS,
        "proxy_note": PROXY_NOTE,
        "option_note": OPTION_HISTORY_UNAVAILABLE,
        "pnl_formula": PNL_FORMULA,
        "disclaimer": DISCLAIMER,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "timeframe": "1Minute",
        "timeframe_note": "Calendar sessions are daily OHLC built from stored 1-minute INDEX bars.",
        "min_sample": min_n,
        "expiry_analysis": "available" if expiry_available else "unavailable",
        "expiry_note": None if expiry_available else "Expiry analysis is unavailable. Expiry is not inferred from weekday.",
        "coverage": list(cover.values()),
        "instruments": reports,
        "nifty_vs_sensex": comparison,
        "pattern_ranking": ranking,
        "groups_tested": sum(int(report.get("groups_tested") or 0) for report in reports.values()),
        "multiple_testing_warning": (
            "Many weekday/month/regime slices were tested. A few will look strong by chance. "
            "Do not convert a ranked pattern into a strategy."
        ),
        "filters": {
            "instrument": instrument.upper(),
            "weekday": weekday,
            "week_of_month": week_of_month,
            "weekday_ordinal": weekday_ordinal,
            "month": month,
            "quarter": quarter,
            "expiry_proximity": expiry_proximity,
            "regime": regime,
        },
    }


def _daily_sessions(db: Session, instrument: str, start: date, end: date, timeframe: str) -> list[dict]:
    tf = timeframe if timeframe in {"1Minute", "3Minute", "5Minute", "15Minute"} else "1Minute"
    source_tf = "1Minute"
    typical = (
        (MarketHistoricalData.high_price + MarketHistoricalData.low_price + MarketHistoricalData.close_price) / 3.0
    ) * func.coalesce(MarketHistoricalData.volume, 0)
    grouped = db.execute(
        select(
            MarketHistoricalData.trading_date,
            func.min(MarketHistoricalData.timestamp),
            func.max(MarketHistoricalData.timestamp),
            func.max(MarketHistoricalData.high_price),
            func.min(MarketHistoricalData.low_price),
            func.sum(func.coalesce(MarketHistoricalData.volume, 0)),
            func.sum(typical),
            func.count(),
            func.count(func.distinct(MarketHistoricalData.timestamp)),
        )
        .where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == source_tf,
            MarketHistoricalData.contract_kind == "INDEX",
            MarketHistoricalData.trading_date >= start,
            MarketHistoricalData.trading_date <= end,
        )
        .group_by(MarketHistoricalData.trading_date)
        .order_by(MarketHistoricalData.trading_date)
    ).all()
    if not grouped:
        return []
    stamps = []
    for row in grouped:
        stamps.append(row[1])
        stamps.append(row[2])
    edges = db.execute(
        select(
            MarketHistoricalData.timestamp,
            MarketHistoricalData.open_price,
            MarketHistoricalData.close_price,
        ).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == source_tf,
            MarketHistoricalData.contract_kind == "INDEX",
            MarketHistoricalData.timestamp.in_(stamps),
        )
    ).all()
    by_ts = {stamp: (float(open_px), float(close_px)) for stamp, open_px, close_px in edges}
    sessions = []
    duplicates = 0
    for day, first_ts, last_ts, high, low, volume, typical_vol, bars, unique_ts in grouped:
        open_px = by_ts.get(first_ts, (None, None))[0]
        close_px = by_ts.get(last_ts, (None, None))[1]
        if open_px is None or close_px is None:
            continue
        vol = float(volume or 0)
        vwap = (float(typical_vol) / vol) if vol and typical_vol is not None else None
        bar_count = int(bars or 0)
        unique = int(unique_ts or 0)
        duplicates += max(0, bar_count - unique)
        sessions.append(
            {
                "instrument": instrument,
                "trading_date": day,
                "open": open_px,
                "high": float(high),
                "low": float(low),
                "close": close_px,
                "volume": vol,
                "vwap": vwap,
                "bars": bar_count,
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "duplicates": 0,
                "out_of_order": 0,
                "source_timeframe": source_tf,
                "requested_timeframe": tf,
            }
        )
    for row in sessions:
        row["duplicates"] = duplicates
    return sessions


def _listed_expiries(db: Session) -> dict[str, set[date]]:
    rows = db.execute(
        select(Instrument.instrument_name, Instrument.expiry)
        .where(
            Instrument.instrument_type.in_(("CE", "PE")),
            Instrument.expiry.is_not(None),
            Instrument.instrument_name.in_(("NIFTY", "SENSEX")),
        )
        .distinct()
    ).all()
    out: dict[str, set[date]] = {"NIFTY": set(), "SENSEX": set()}
    for name, expiry in rows:
        if expiry and expiry.year > 1990:
            out.setdefault(str(name).upper(), set()).add(expiry)
    if sum(len(v) for v in out.values()) < 3:
        return {}
    return out


def _apply_filters(
    rows: list[dict],
    weekday,
    week_of_month,
    weekday_ordinal,
    month,
    quarter,
    expiry_proximity,
    regime,
) -> list[dict]:
    out = rows
    if weekday and weekday != "ALL":
        out = [row for row in out if row.get("weekday") == weekday]
    if week_of_month and week_of_month != "ALL":
        out = [row for row in out if row.get("week_of_month") == week_of_month]
    if weekday_ordinal and weekday_ordinal != "ALL":
        out = [row for row in out if row.get("weekday_ordinal") == weekday_ordinal]
    if month and month != "ALL":
        out = [row for row in out if row.get("month") == month]
    if quarter and quarter != "ALL":
        out = [row for row in out if row.get("quarter") == quarter]
    if expiry_proximity and expiry_proximity != "ALL":
        out = [row for row in out if row.get("expiry_proximity") == expiry_proximity]
    if regime and regime != "ALL":
        wanted = regime.upper()
        if wanted in {"BULLISH", "BEARISH", "SIDEWAYS", "FLAT"}:
            token = "FLAT" if wanted in {"SIDEWAYS", "FLAT"} else wanted
            out = [row for row in out if row.get("prev_direction") == token]
        elif wanted in {"GAP_UP", "GAP_DOWN"}:
            out = [row for row in out if row.get("gap_class") == wanted]
        elif wanted in {"HIGH_VOL", "LOW_VOL"}:
            out = [row for row in out if row.get("vol_regime") == wanted]
    return out


def _instrument_report(
    db: Session,
    symbol: str,
    raw_sessions: list[dict],
    expiries: set[date],
    expiry_available: bool,
    start: date,
    end: date,
    weekday,
    week_of_month,
    weekday_ordinal,
    month,
    quarter,
    expiry_proximity,
    regime,
    min_n: int,
) -> dict:
    with_returns = attach_session_returns(raw_sessions)
    annotated = annotate_calendar(with_returns, expiries if expiry_available else None)
    filtered = _apply_filters(annotated, weekday, week_of_month, weekday_ordinal, month, quarter, expiry_proximity, regime)
    date_range = f"{start.isoformat()} → {end.isoformat()}"
    by_weekday = []
    patterns = []
    groups = 0
    paper = _paper_by_weekday(db, symbol, start, end)
    for name in WEEKDAYS:
        rows = [row for row in filtered if row.get("weekday") == name]
        stats = session_stats(rows, min_sample=min_n)
        paper_row = paper.get(name) or {}
        paper_row["hold_count"] = max(0, int(stats.get("sessions") or 0) - int(paper_row.get("total_trades") or 0))
        stats["paper"] = paper_row
        stats["net_paper_pnl"] = paper_row.get("net_pnl")
        stats["paper_win_rate"] = paper_row.get("win_rate")
        card = pattern_card(f"{symbol} {name} open-to-close", symbol, date_range, name, stats, 0)
        card["paper"] = paper_row
        by_weekday.append({"weekday": name, **stats})
        patterns.append(card)
        groups += 1
    weekday_month = []
    for name in WEEKDAYS:
        for month_name in MONTHS:
            rows = [row for row in filtered if row.get("weekday") == name and row.get("month") == month_name]
            stats = session_stats(rows, min_sample=min_n)
            weekday_month.append({"weekday": name, "month": month_name, **stats})
            groups += 1
            patterns.append(pattern_card(f"{symbol} {name} in {month_name}", symbol, date_range, f"{name}|{month_name}", stats, 0))
    week_month = []
    for name in WEEKDAYS:
        for label in ("First", "Second", "Third", "Fourth", "Last"):
            rows = [row for row in filtered if row.get("weekday") == name and row.get("week_of_month") == label]
            stats = session_stats(rows, min_sample=min_n)
            week_month.append({"weekday": name, "week_of_month": label, **stats})
            groups += 1
            patterns.append(pattern_card(f"{symbol} {name} {label} week of month", symbol, date_range, f"{name}|WOM|{label}", stats, 0))
    weekday_year = []
    years = sorted({row.get("year") for row in filtered if row.get("year")})
    for name in WEEKDAYS:
        for year in years:
            rows = [row for row in filtered if row.get("weekday") == name and row.get("year") == year]
            stats = session_stats(rows, min_sample=min_n)
            weekday_year.append({"weekday": name, "year": year, **stats})
            groups += 1
    conditions = []
    for label, pred in (
        ("After bullish previous close", lambda r: r.get("prev_direction") == "BULLISH"),
        ("After bearish previous close", lambda r: r.get("prev_direction") == "BEARISH"),
        ("After flat previous close", lambda r: r.get("prev_direction") == "FLAT"),
        ("After gap-up open", lambda r: r.get("gap_class") == "GAP_UP"),
        ("After gap-down open", lambda r: r.get("gap_class") == "GAP_DOWN"),
        ("After high-volatility session", lambda r: r.get("vol_regime") == "HIGH_VOL"),
        ("After low-volatility session", lambda r: r.get("vol_regime") == "LOW_VOL"),
        ("First trading day of week", lambda r: r.get("is_first_of_week")),
        ("Last trading day of week", lambda r: r.get("is_last_of_week")),
        ("First trading day of month", lambda r: r.get("is_first_of_month")),
        ("Last trading day of month", lambda r: r.get("is_last_of_month")),
    ):
        for name in WEEKDAYS:
            rows = [row for row in filtered if row.get("weekday") == name and pred(row)]
            stats = session_stats(rows, min_sample=min_n)
            conditions.append({"weekday": name, "condition": label, **stats})
            groups += 1
            patterns.append(pattern_card(f"{symbol} {name} {label}", symbol, date_range, f"{name}|{label}", stats, 0))
    expiry_rows = []
    if expiry_available:
        for prox in ("EXPIRY_DAY", "ONE_BEFORE_EXPIRY", "ONE_AFTER_EXPIRY", "NON_EXPIRY"):
            rows = [row for row in filtered if row.get("expiry_proximity") == prox]
            stats = session_stats(rows, min_sample=min_n)
            expiry_rows.append({"expiry_proximity": prox, **stats})
            groups += 1
    for card in patterns:
        card["groups_tested"] = groups
        card["multiple_testing_note"] = (
            f"{groups} pattern groups were tested. Some will look strong by chance. This is not a trading signal."
        )
    holidays = missing_weekdays([row["trading_date"] for row in annotated])
    missing_bars = sum(max(0, 375 - int(row.get("bars") or 0)) for row in annotated)
    return {
        "instrument": symbol,
        "sessions": filtered,
        "session_count": len(filtered),
        "available_sessions": len(annotated),
        "weekday_table": by_weekday,
        "weekday_by_month": weekday_month,
        "week_of_month": week_month,
        "weekday_by_year": weekday_year,
        "market_conditions": conditions,
        "expiry": expiry_rows,
        "paper_by_weekday": paper,
        "patterns": patterns,
        "groups_tested": groups,
        "holidays_or_missing_weekdays": [day.isoformat() for day in holidays[:80]],
        "holiday_count": len(holidays),
        "missing_minute_bars_estimate": missing_bars,
        "duplicate_timestamps": int(annotated[0].get("duplicates") or 0) if annotated else 0,
        "out_of_order": int(annotated[0].get("out_of_order") or 0) if annotated else 0,
        "quality": {
            "timezone": "Asia/Kolkata",
            "data_source": "market_historical_data INDEX 1Minute",
            "price_basis": PRICE_BASIS,
            "proxy_note": PROXY_NOTE,
            "option_history_available": False,
        },
        "overall": session_stats(filtered, min_sample=min_n),
    }


def _paper_by_weekday(db: Session, symbol: str, start: date, end: date) -> dict:
    trades = []
    for row in db.scalars(select(PaperTrade).where(PaperTrade.underlying == symbol)).all():
        day = to_ist(row.entry_time.replace(tzinfo=timezone.utc) if row.entry_time.tzinfo is None else row.entry_time).date()
        if day < start or day > end:
            continue
        item = _trade_item(row.option_type, row.status, row.entry_price, row.exit_price, row.quantity, row.net_pnl, row.gross_pnl, row.exit_reason, row.entry_time, row.exit_time, day)
        trades.append(item)
    latest = db.scalar(
        select(HistoricalPaperRun)
        .where(HistoricalPaperRun.status == "COMPLETED", HistoricalPaperRun.instrument.in_((symbol, "BOTH")), HistoricalPaperRun.total_trades > 0)
        .order_by(HistoricalPaperRun.id.desc())
    )
    if latest is not None:
        for row in db.scalars(select(HistoricalPaperTrade).where(HistoricalPaperTrade.run_id == latest.id, HistoricalPaperTrade.instrument == symbol)).all():
            if row.trade_date < start or row.trade_date > end:
                continue
            item = _trade_item(row.option_type, "CLOSED", row.entry_price, row.exit_price, row.quantity, row.net_pnl, row.gross_pnl, row.exit_reason, row.entry_time, row.exit_time, row.trade_date)
            trades.append(item)
    buckets = {name: [] for name in WEEKDAYS}
    for row in trades:
        name = WEEKDAYS[row["date"].weekday()] if row["date"].weekday() < 5 else None
        if name:
            buckets[name].append(row)
    out = {}
    for name, rows in buckets.items():
        buy = [row for row in rows if row["side"] in {"BUY", "CE"}]
        sell = [row for row in rows if row["side"] == "SELL"]
        summary = performance_from_trades(rows)
        buy_sum = performance_from_trades(buy)
        sell_sum = performance_from_trades(sell)
        reasons = defaultdict(int)
        for row in rows:
            reasons[str(row.get("exit_reason") or "UNKNOWN")] += 1
        out[name] = {
            "buy_signal_count": len(buy),
            "sell_signal_count": len(sell),
            "hold_count": 0,
            "buy_win_rate": buy_sum.get("win_rate"),
            "sell_win_rate": sell_sum.get("win_rate"),
            "buy_net_pnl": buy_sum.get("net_pnl"),
            "sell_net_pnl": sell_sum.get("net_pnl"),
            "combined_net_pnl": summary.get("net_pnl"),
            "average_trade_result": summary.get("average_trade_result"),
            "average_holding_minutes": summary.get("average_holding_minutes"),
            "max_drawdown": summary.get("max_drawdown"),
            "stop_loss_exits": reasons.get("STOP_LOSS_HIT", 0),
            "target_exits": reasons.get("TARGET_HIT", 0),
            "time_exits": reasons.get("TIME_EXIT", 0),
            "force_square_off_exits": reasons.get("FORCE_SQUARE_OFF", 0),
            "net_pnl": summary.get("net_pnl"),
            "win_rate": summary.get("win_rate"),
            "total_trades": summary.get("total_trades"),
        }
    return out


def _trade_item(side, status, entry, exit_px, qty, net, gross, reason, entry_time, exit_time, day) -> dict:
    side_u = str(side or "").upper()
    item = {
        "status": status or "CLOSED",
        "option_type": side_u,
        "side": side_u,
        "entry_price": float(entry or 0),
        "exit_price": float(exit_px) if exit_px is not None else None,
        "quantity": int(qty or 0),
        "net_pnl": float(net or 0),
        "gross_pnl": float(gross or 0),
        "exit_reason": reason,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "date": day,
        "instrument": "",
        "holding_minutes": None,
    }
    if side_u in {"BUY", "SELL"} and exit_px is not None:
        g, slip, n = index_paper_net(side_u, entry, exit_px, int(qty or 0), slippage_points=0.5)
        item["gross_pnl"] = float(g)
        item["net_pnl"] = float(n)
        item["slippage"] = float(slip)
        item["total_charges"] = float(slip)
    if entry_time and exit_time:
        a = entry_time if getattr(entry_time, "tzinfo", None) else entry_time.replace(tzinfo=timezone.utc)
        b = exit_time if getattr(exit_time, "tzinfo", None) else exit_time.replace(tzinfo=timezone.utc)
        item["holding_minutes"] = (b - a).total_seconds() / 60.0
    return item


def _aligned_comparison(nifty_rows: list[dict], sensex_rows: list[dict], min_n: int) -> dict:
    n_map = {row["trading_date"]: row for row in nifty_rows}
    s_map = {row["trading_date"]: row for row in sensex_rows}
    common = sorted(set(n_map) & set(s_map))
    by_day = {name: {"same": 0, "opp": 0, "n": [], "s": []} for name in WEEKDAYS}
    for day in common:
        n, s = n_map[day], s_map[day]
        if n.get("oc_pct") is None or s.get("oc_pct") is None:
            continue
        name = n.get("weekday")
        if name not in by_day:
            continue
        n_dir = 1 if n["oc_pct"] > 0 else (-1 if n["oc_pct"] < 0 else 0)
        s_dir = 1 if s["oc_pct"] > 0 else (-1 if s["oc_pct"] < 0 else 0)
        if n_dir and s_dir:
            if n_dir == s_dir:
                by_day[name]["same"] += 1
            else:
                by_day[name]["opp"] += 1
        by_day[name]["n"].append(n["oc_pct"])
        by_day[name]["s"].append(s["oc_pct"])
    rows = []
    for name in WEEKDAYS:
        bucket = by_day[name]
        total = bucket["same"] + bucket["opp"]
        n_win = round(100.0 * sum(1 for v in bucket["n"] if v > FLAT_PCT) / len(bucket["n"]), 2) if bucket["n"] else None
        s_win = round(100.0 * sum(1 for v in bucket["s"] if v > FLAT_PCT) / len(bucket["s"]), 2) if bucket["s"] else None
        rows.append(
            {
                "weekday": name,
                "aligned_sessions": total,
                "same_direction_pct": round(100.0 * bucket["same"] / total, 2) if total else None,
                "opposite_direction_pct": round(100.0 * bucket["opp"] / total, 2) if total else None,
                "pearson": pearson(bucket["n"], bucket["s"]),
                "spearman": spearman(bucket["n"], bucket["s"]),
                "avg_nifty_return": round(sum(bucket["n"]) / len(bucket["n"]), 6) if bucket["n"] else None,
                "avg_sensex_return": round(sum(bucket["s"]) / len(bucket["s"]), 6) if bucket["s"] else None,
                "nifty_volatility": session_stats([{"oc_pct": v, "cc_pct": v} for v in bucket["n"]], min_sample=min_n).get("volatility") if bucket["n"] else None,
                "sensex_volatility": session_stats([{"oc_pct": v, "cc_pct": v} for v in bucket["s"]], min_sample=min_n).get("volatility") if bucket["s"] else None,
                "nifty_win_rate": n_win,
                "sensex_win_rate": s_win,
                "win_rate_diff": round(n_win - s_win, 2) if n_win is not None and s_win is not None else None,
                "sample_sufficient": total >= min_n,
            }
        )
        rows[-1]["volatility_diff"] = (
            round(rows[-1]["nifty_volatility"] - rows[-1]["sensex_volatility"], 6)
            if rows[-1]["nifty_volatility"] is not None and rows[-1]["sensex_volatility"] is not None
            else None
        )
    return {
        "aligned_session_count": len(common),
        "note": "NIFTY and SENSEX are compared only on shared trading_date values. Returns are open-to-close percent on INDEX close.",
        "weekdays": rows,
    }


def _rank_patterns(cards: list[dict], min_n: int) -> list[dict]:
    scored = []
    for card in cards:
        n = int(card.get("observations") or 0)
        oos = card.get("out_of_sample") or {}
        full = card.get("full_sample") or {}
        oos_net = oos.get("avg_return_pct")
        oos_n = int(oos.get("sessions") or 0)
        pos = card.get("win_rate")
        ra = risk_adjusted(card.get("average_result"), card.get("volatility"))
        dd = full.get("max_drawdown_pct") or 0
        if n < min_n or oos_n < min_n:
            continue
        scored.append(
            {
                **card,
                "rank_oos_net": oos_net,
                "rank_positive_pct": pos,
                "rank_risk_adjusted": ra,
                "rank_drawdown": dd,
                "rank_n": n,
            }
        )
    scored.sort(
        key=lambda row: (
            -(row["rank_oos_net"] or -999),
            -(row["rank_risk_adjusted"] or -999),
            -(row["rank_positive_pct"] or 0),
            row["rank_drawdown"] or 0,
            -row["rank_n"],
        )
    )
    slim = []
    for row in scored[:40]:
        full = dict(row.get("full_sample") or {})
        full.pop("dates", None)
        slim.append(
            {
                "pattern_definition": row.get("pattern_definition"),
                "instrument": row.get("instrument"),
                "date_range": row.get("date_range"),
                "condition": row.get("condition"),
                "observations": row.get("observations"),
                "average_result": row.get("average_result"),
                "median_result": row.get("median_result"),
                "win_rate": row.get("win_rate"),
                "volatility": row.get("volatility"),
                "net_result_after_costs": row.get("net_result_after_costs"),
                "confidence_interval": row.get("confidence_interval"),
                "sample_sufficient": row.get("sample_sufficient"),
                "statistically_reliable": row.get("statistically_reliable"),
                "works_out_of_sample": row.get("works_out_of_sample"),
                "reliability": row.get("reliability"),
                "oos_warning": row.get("oos_warning"),
                "outlier_dominated": row.get("outlier_dominated"),
                "groups_tested": row.get("groups_tested"),
                "multiple_testing_note": row.get("multiple_testing_note"),
                "price_basis": row.get("price_basis"),
                "disclaimer": row.get("disclaimer"),
                "proxy_note": row.get("proxy_note"),
                "in_sample": row.get("in_sample"),
                "out_of_sample": row.get("out_of_sample"),
                "dates": (row.get("dates") or [])[-31:],
                "full_sample": full,
                "rank_oos_net": row.get("rank_oos_net"),
                "rank_positive_pct": row.get("rank_positive_pct"),
                "rank_risk_adjusted": row.get("rank_risk_adjusted"),
                "rank_drawdown": row.get("rank_drawdown"),
                "rank_n": row.get("rank_n"),
            }
        )
    return slim

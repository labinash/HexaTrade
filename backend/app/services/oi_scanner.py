"""Service layer for the OI Scanner.

Current values come from the shared quote cache that the existing WebSocket
feed fills, and history comes from the minute candles the existing aggregator
already writes. No extra broker connection and no extra snapshot table.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.kite.instruments import expiries as list_expiries
from app.adapters.kite.instruments import instruments_for
from app.core.clock import as_utc_naive, now_ist, to_ist, to_utc
from app.db.models import Candle, OiScannerConfig, UnderlyingProfile
from app.domain.oi_scanner import (
    LIVE,
    STATE_LABELS,
    Contract,
    ScanRow,
    ScannerConfig,
    Snapshot,
    build_row,
    market_state,
    passes,
    summarise,
    top_movers,
    window_strikes,
)
from app.services.mapping import f, jsonable

CONFIG_FIELDS = {
    "interval_minutes", "reference_time", "strikes_above", "strikes_below", "option_type",
    "expiry_mode", "min_oi_change_interval", "min_oi_change_reference", "direction",
    "refresh_seconds", "snapshot_tolerance_minutes",
}

SEEDS = {
    "SENSEX": {"min_oi_change_interval": Decimal("2000"), "min_oi_change_reference": Decimal("2000")},
}

SNAPSHOT_INTERVAL = "minute"
SNAPSHOT_SECONDS = 60


def config_row(db: Session, symbol: str) -> OiScannerConfig | None:
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    if profile is None:
        return None
    row = db.scalar(select(OiScannerConfig).where(OiScannerConfig.underlying_id == profile.id))
    if row is None:
        row = OiScannerConfig(underlying_id=profile.id, **SEEDS.get(symbol, {}))
        db.add(row)
        db.flush()
    return row


def config_payload(db: Session, symbol: str) -> dict:
    row = config_row(db, symbol)
    if row is None:
        return {}
    data = {key: jsonable(getattr(row, key)) for key in sorted(CONFIG_FIELDS)}
    data["underlying"] = symbol
    data["snapshot_interval_seconds"] = SNAPSHOT_SECONDS
    return data


def config_of(row: OiScannerConfig, **overrides) -> ScannerConfig:
    base = dict(
        interval_minutes=row.interval_minutes,
        reference_time=row.reference_time,
        strikes_above=row.strikes_above,
        strikes_below=row.strikes_below,
        option_type=row.option_type,
        expiry_mode=row.expiry_mode,
        min_oi_change_interval=f(row.min_oi_change_interval),
        min_oi_change_reference=f(row.min_oi_change_reference),
        direction=row.direction,
        refresh_seconds=row.refresh_seconds,
        snapshot_tolerance_minutes=row.snapshot_tolerance_minutes,
    )
    base.update({key: value for key, value in overrides.items() if value is not None})
    return ScannerConfig(**base)


def resolve_expiries(db: Session, symbol: str, mode: str, specific: date | None) -> list[date]:
    found = list_expiries(db, symbol)
    if not found:
        return []
    if specific is not None:
        return [specific] if specific in found else []
    if mode == "all":
        return found
    if mode == "next":
        return found[1:2] or found[:1]
    return found[:1]


def snapshots_at(db: Session, tokens: list[int], target: datetime, tolerance_minutes: int) -> dict[int, Snapshot]:
    """Newest stored snapshot at or before target, per token.

    Rows older than the tolerance window are ignored rather than presented as if
    they were current. Lookups are keyed on instrument_token, so open interest
    is never compared across strikes, expiries or option types.
    """
    if not tokens:
        return {}
    floor = target - timedelta(minutes=max(tolerance_minutes, 1))
    pairs = db.execute(
        select(Candle.instrument_token, func.max(Candle.start_time))
        .where(
            Candle.instrument_token.in_(tokens),
            Candle.interval == SNAPSHOT_INTERVAL,
            Candle.start_time <= target,
            Candle.start_time >= floor,
        )
        .group_by(Candle.instrument_token)
    ).all()
    if not pairs:
        return {}
    wanted = {int(token): moment for token, moment in pairs}
    rows = db.scalars(
        select(Candle).where(
            Candle.instrument_token.in_(list(wanted)),
            Candle.interval == SNAPSHOT_INTERVAL,
            Candle.start_time.in_(list(wanted.values())),
        )
    ).all()
    out: dict[int, Snapshot] = {}
    for row in rows:
        token = int(row.instrument_token)
        if wanted.get(token) != row.start_time:
            continue
        out[token] = Snapshot(
            oi=int(row.oi) if row.oi is not None else None,
            ltp=f(row.close),
            volume=int(row.volume) if row.volume is not None else None,
            at=row.start_time,
        )
    return out


def _live_snapshot(quote: dict, fallback: Snapshot) -> Snapshot:
    """Live cache when it has this token, otherwise the newest stored minute."""
    if quote.get("oi") is None and quote.get("ltp") is None:
        return fallback
    stamp = quote.get("timestamp")
    # Kite sends exchange timestamps as naive IST, which to_utc already assumes.
    at = as_utc_naive(to_utc(stamp)) if isinstance(stamp, datetime) else None
    return Snapshot(
        oi=int(quote["oi"]) if quote.get("oi") is not None else fallback.oi,
        ltp=quote.get("ltp") if quote.get("ltp") is not None else fallback.ltp,
        volume=int(quote["volume"]) if quote.get("volume") is not None else fallback.volume,
        at=at or fallback.at,
    )


def scan(db: Session, runtime, symbol: str, overrides: dict, as_of: time | None = None) -> dict:
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    if profile is None:
        raise ValueError(f"{symbol} is not configured")
    row = config_row(db, symbol)
    config = config_of(row, **overrides)
    show_all = bool(overrides.get("show_all"))

    ist_now = now_ist()
    state = market_state(ist_now.time(), profile.session_start, profile.session_end)
    historical = as_of is not None
    clock = ist_now.replace(hour=as_of.hour, minute=as_of.minute, second=0, microsecond=0) if historical else ist_now
    if historical:
        state = LIVE if profile.session_start <= as_of <= profile.session_end else state

    expiry_list = resolve_expiries(db, symbol, config.expiry_mode, overrides.get("specific_expiry"))
    contracts: list[Contract] = []
    for expiry in expiry_list:
        for kind in ("CE", "PE"):
            if config.option_type in {"CE", "PE"} and kind != config.option_type:
                continue
            for item in instruments_for(db, symbol, kind, expiry):
                if item.strike is None:
                    continue
                contracts.append(
                    Contract(
                        instrument_token=int(item.instrument_token),
                        tradingsymbol=item.tradingsymbol,
                        underlying=symbol,
                        expiry=item.expiry,
                        strike=float(item.strike),
                        option_type=item.instrument_type,
                        lot_size=int(item.lot_size or 0),
                    )
                )

    from app.services.engine import market_view

    view = market_view(db, runtime, symbol)
    price = view.get("futures_ltp") or view.get("ltp")
    strikes = sorted({item.strike for item in contracts})
    atm = min(strikes, key=lambda strike: (abs(strike - price), strike)) if strikes and price else None
    interval = _strike_interval(strikes)
    if atm is not None and not show_all:
        keep = set(window_strikes(strikes, atm, config.strikes_above, config.strikes_below))
        contracts = [item for item in contracts if item.strike in keep]

    tokens = [item.instrument_token for item in contracts]
    now_utc_naive = as_utc_naive(to_utc(clock))
    latest = snapshots_at(db, tokens, now_utc_naive, config.snapshot_tolerance_minutes)
    previous = snapshots_at(
        db, tokens, now_utc_naive - timedelta(minutes=config.interval_minutes), config.snapshot_tolerance_minutes
    )
    reference_clock = ist_now.replace(
        hour=config.reference_time.hour, minute=config.reference_time.minute, second=0, microsecond=0
    )
    reference = {}
    if reference_clock <= clock:
        reference = snapshots_at(db, tokens, as_utc_naive(to_utc(reference_clock)), config.snapshot_tolerance_minutes)

    cached = {} if historical else runtime.cache.many(tokens)
    rows: list[ScanRow] = []
    for contract in contracts:
        token = contract.instrument_token
        stored = latest.get(token, Snapshot())
        live = stored if historical else _live_snapshot(cached.get(token) or {}, stored)
        built = build_row(
            contract,
            live,
            previous.get(token, Snapshot()),
            reference.get(token, Snapshot()),
            config,
            state,
            atm,
            interval,
            historical,
        )
        quote = cached.get(token) or {}
        built.bid = quote.get("bid")
        built.ask = quote.get("ask")
        built.day_change = quote.get("change")
        rows.append(built)

    visible = [item for item in rows if passes(item, config)]
    reference_available = any(item.oi_change_reference is not None for item in rows)
    return {
        "underlying": symbol,
        "state": state,
        "state_label": STATE_LABELS.get(state, state),
        "historical": historical,
        "as_of": clock.strftime("%H:%M:%S"),
        "server_time": ist_now.strftime("%H:%M:%S"),
        "interval_minutes": config.interval_minutes,
        "reference_time": config.reference_time.strftime("%H:%M"),
        "reference_available": reference_available,
        "reference_pending": reference_clock > clock,
        "snapshot_interval_seconds": SNAPSHOT_SECONDS,
        "expiries": [item.isoformat() for item in list_expiries(db, symbol)],
        "expiries_shown": [item.isoformat() for item in expiry_list],
        "atm": atm,
        "strike_interval": interval,
        "context": {
            "spot": view.get("ltp"),
            "futures_ltp": view.get("futures_ltp"),
            "futures_oi": view.get("futures_oi"),
            "vwap": view.get("vwap"),
            "ema": view.get("ema"),
            "rsi": view.get("rsi"),
            "atr": view.get("atr"),
            "volume": view.get("volume"),
            "oi_change_from_reference_time": view.get("oi_change"),
            "stale": view.get("stale"),
            "session_start": profile.session_start.strftime("%H:%M"),
            "session_end": profile.session_end.strftime("%H:%M"),
        },
        "summary": summarise(rows),
        "top_movers": top_movers(rows),
        "rows": [row_payload(item) for item in visible],
        "total_contracts": len(rows),
        "shown_contracts": len(visible),
        "filters": {
            "option_type": config.option_type,
            "direction": config.direction,
            "min_oi_change_interval": config.min_oi_change_interval,
            "min_oi_change_reference": config.min_oi_change_reference,
            "strikes_above": config.strikes_above,
            "strikes_below": config.strikes_below,
            "show_all": show_all,
            "expiry_mode": config.expiry_mode,
        },
        "note": "Open interest analysis only. Interpretations are possibilities, not signals, and no order is placed from this screen.",
    }


def row_payload(row: ScanRow) -> dict:
    return {
        "instrument_token": row.instrument_token,
        "tradingsymbol": row.tradingsymbol,
        "underlying": row.underlying,
        "expiry": row.expiry.isoformat() if row.expiry else None,
        "strike": row.strike,
        "option_type": row.option_type,
        "moneyness": row.moneyness,
        "strikes_from_atm": row.strikes_from_atm,
        "ltp": row.ltp,
        "bid": row.bid,
        "ask": row.ask,
        "volume": row.volume,
        "oi": row.oi,
        "day_change": row.day_change,
        "timestamp": _ist_label(row.timestamp),
        "oi_change_5m": row.oi_change_interval,
        "oi_change_5m_percent": row.oi_change_interval_percent,
        "oi_change_interval": row.oi_change_interval,
        "oi_change_interval_percent": row.oi_change_interval_percent,
        "interval_from": _ist_label(row.interval_from),
        "interval_note": row.interval_note,
        "oi_change_reference": row.oi_change_reference,
        "oi_change_reference_percent": row.oi_change_reference_percent,
        "reference_at": _ist_label(row.reference_at),
        "reference_note": row.reference_note,
        "price_change": row.price_change,
        "price_change_percent": row.price_change_percent,
        "interpretation": row.interpretation,
        "interpretation_code": row.interpretation_code,
        "significant_increase": row.significant_increase,
        "significant_decrease": row.significant_decrease,
        "notes": row.notes,
    }


def _ist_label(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    aware = moment if moment.tzinfo else moment.replace(tzinfo=__import__("datetime").timezone.utc)
    return to_ist(aware).strftime("%H:%M:%S")


def _strike_interval(strikes: list[float]) -> float | None:
    from app.domain.chain import strike_interval

    return strike_interval(strikes)

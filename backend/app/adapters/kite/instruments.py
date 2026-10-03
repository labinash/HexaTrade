from datetime import date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.adapters.kite.client import call_with_backoff
from app.db.models import Instrument, UnderlyingProfile

KEEP = {
    ("NSE", "NIFTY 50", "EQ"),
    ("BSE", "SENSEX", "EQ"),
}


def refresh_instruments(db: Session, kite) -> int:
    profiles = {row.symbol: row for row in db.scalars(select(UnderlyingProfile))}
    dump_date = date.today()
    rows = []
    for exchange in ("NFO", "BFO", "NSE", "BSE"):
        payload = call_with_backoff(kite.instruments, exchange)
        for item in payload:
            mapped = _map_row(item, profiles, dump_date)
            if mapped is not None:
                rows.append(mapped)
    db.execute(delete(Instrument).where(Instrument.dump_date == dump_date))
    db.add_all(rows)
    db.commit()
    return len(rows)


def _map_row(item: dict, profiles: dict, dump_date: date) -> Instrument | None:
    exchange = item.get("exchange")
    tradingsymbol = item.get("tradingsymbol")
    name = item.get("name") or ""
    instrument_type = item.get("instrument_type") or ""
    profile = None
    if exchange == "NFO" and name == "NIFTY" and instrument_type in {"CE", "PE", "FUT"}:
        profile = profiles.get("NIFTY")
    elif exchange == "BFO" and name == "SENSEX" and instrument_type in {"CE", "PE", "FUT"}:
        profile = profiles.get("SENSEX")
    elif exchange == "NSE" and tradingsymbol == "NIFTY 50":
        profile = profiles.get("NIFTY")
    elif exchange == "BSE" and tradingsymbol == "SENSEX":
        profile = profiles.get("SENSEX")
    else:
        return None
    expiry = item.get("expiry")
    if isinstance(expiry, datetime):
        expiry = expiry.date()
    elif isinstance(expiry, str) and expiry:
        expiry = date.fromisoformat(expiry[:10])
    else:
        expiry = expiry if isinstance(expiry, date) else None
    return Instrument(
        underlying_id=profile.id if profile else None,
        instrument_token=int(item["instrument_token"]),
        exchange=exchange,
        segment=item.get("segment") or "",
        tradingsymbol=tradingsymbol,
        instrument_name=name or tradingsymbol,
        expiry=expiry,
        strike=item.get("strike") or None,
        instrument_type=instrument_type,
        lot_size=int(item.get("lot_size") or 1),
        tick_size=item.get("tick_size") or 0.05,
        dump_date=dump_date,
    )


def latest_dump(db: Session) -> date | None:
    return db.scalar(select(func.max(Instrument.dump_date)))


def instruments_for(db: Session, symbol: str, instrument_type: str | None = None, expiry: date | None = None) -> list[Instrument]:
    dump = latest_dump(db)
    if dump is None:
        return []
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    if profile is None:
        return []
    query = select(Instrument).where(Instrument.dump_date == dump, Instrument.underlying_id == profile.id)
    if instrument_type:
        query = query.where(Instrument.instrument_type == instrument_type)
    if expiry:
        query = query.where(Instrument.expiry == expiry)
    return list(db.scalars(query))


def front_future(db: Session, symbol: str, on_date: date | None = None) -> Instrument | None:
    on_date = on_date or date.today()
    rows = [
        row
        for row in instruments_for(db, symbol, "FUT")
        if row.expiry is not None and row.expiry >= on_date
    ]
    if not rows:
        return None
    return sorted(rows, key=lambda row: row.expiry)[0]


def spot_instrument(db: Session, symbol: str) -> Instrument | None:
    dump = latest_dump(db)
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    if dump is None or profile is None:
        return None
    return db.scalar(
        select(Instrument).where(
            Instrument.dump_date == dump,
            Instrument.exchange == profile.spot_exchange,
            Instrument.tradingsymbol == profile.spot_symbol,
        )
    )


def expiries(db: Session, symbol: str) -> list[date]:
    rows = instruments_for(db, symbol, "CE")
    found = sorted({row.expiry for row in rows if row.expiry is not None and row.expiry >= date.today()})
    return found

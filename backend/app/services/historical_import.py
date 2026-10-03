"""Background historical import. Writes only to market_historical_data, never to candle."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.kite.auth import kite_client, load_access_token
from app.adapters.kite.historical import KiteHistoricalMarketDataProvider, chunk_windows
from app.adapters.kite.instruments import spot_instrument
from app.core.clock import as_utc_naive, combine_ist, now_utc
from app.db.models import HistoricalImportEvent, HistoricalImportJob, UnderlyingProfile
from app.db.session import session_factory
from app.domain.historical import (
    in_session,
    is_weekend,
    missing_ranges,
    normalize_timeframe,
    parse_instruments,
    three_year_window,
    validate_ohlc,
)
from app.domain.historical_provider import ProviderError
from app.services.historical_repo import (
    existing_timestamps,
    get_latest_historical_timestamp,
    insert_historical_data,
    quality_for,
    row_from_bar,
)

log = logging.getLogger("hextrade.historical.import")
_lock = threading.Lock()

OPTION_NOTE = (
    "Kite does not provide expired option-contract history. "
    "Backdated analysis and paper trading use NIFTY and SENSEX index 1-minute closes instead."
)
INDEX_FUTURES_NOTE = (
    "INDEX FUTURES PROXY. Option history unavailable. Paper BUY = long NIFTY/SENSEX, "
    "paper SELL = short NIFTY/SENSEX, filled at the index close. NET P&L is index paper, not CE/PE premium. "
    "HYPOTHETICAL / PAPER only. No broker orders."
)


def default_window() -> dict:
    start, end = three_year_window()
    return {"from_date": start.isoformat(), "to_date": end.isoformat(), "timeframe": "1Minute"}


def queue_import(
    db: Session,
    *,
    instruments,
    timeframe: str = "1Minute",
    mode: str = "INITIAL",
    date_from: date | None = None,
    date_to: date | None = None,
) -> HistoricalImportJob:
    symbols = parse_instruments(instruments)
    tf = normalize_timeframe(timeframe)
    start, end = three_year_window()
    if date_from:
        start = date_from
    if date_to:
        end = date_to
    if end < start:
        raise ValueError("To date is before from date")
    mode = (mode or "INITIAL").upper()
    if mode not in {"INITIAL", "INCREMENTAL", "RESUME"}:
        raise ValueError("mode must be INITIAL, INCREMENTAL or RESUME")
    active = db.scalar(
        select(HistoricalImportJob).where(HistoricalImportJob.status.in_(("QUEUED", "RUNNING"))).order_by(HistoricalImportJob.id.desc())
    )
    if active is not None:
        raise ValueError(f"Import job {active.id} is already {active.status}. Resume or wait for it to finish.")
    if mode == "INCREMENTAL":
        starts = []
        for symbol in symbols:
            latest = get_latest_historical_timestamp(db, symbol, tf)
            miss_from, miss_to = missing_ranges(latest, start, end)
            starts.append(miss_from)
            end = min(end, miss_to)
        if starts:
            start = min(starts)
    now = as_utc_naive(now_utc())
    job = HistoricalImportJob(
        created_at=now,
        updated_at=now,
        mode=mode,
        status="QUEUED",
        instruments=",".join(symbols),
        timeframe=tf,
        date_from=start,
        date_to=end,
        option_data_note=OPTION_NOTE,
        log_message="Import queued",
    )
    db.add(job)
    db.flush()
    _event(db, job, f"Import started · {job.instruments} · {tf} · {start} → {end} · {mode}")
    return job


def resume_import(db: Session, job_id: int) -> HistoricalImportJob:
    job = db.get(HistoricalImportJob, job_id)
    if job is None:
        raise ValueError("Import job not found")
    if job.status not in {"FAILED", "PAUSED"}:
        raise ValueError(f"Job {job.id} is {job.status} and cannot be resumed")
    active = db.scalar(
        select(HistoricalImportJob).where(HistoricalImportJob.status.in_(("QUEUED", "RUNNING"))).order_by(HistoricalImportJob.id.desc())
    )
    if active is not None and active.id != job.id:
        raise ValueError(f"Import job {active.id} is already {active.status}")
    job.status = "QUEUED"
    job.mode = "RESUME"
    job.last_error = ""
    job.log_message = "Resume queued"
    job.updated_at = as_utc_naive(now_utc())
    _event(db, job, "Resume import from last successful point")
    return job


def job_payload(job: HistoricalImportJob) -> dict:
    symbols = [part for part in (job.instruments or "").split(",") if part]
    current = job.current_instrument
    sides = []
    for symbol in symbols:
        if job.status == "COMPLETED":
            state = "completed"
        elif current == symbol and job.status == "RUNNING":
            state = "importing"
        elif current and symbols.index(symbol) < symbols.index(current) if current in symbols else False:
            state = "completed"
        else:
            state = "pending"
        sides.append({"instrument": symbol, "state": state})
    return {
        "id": job.id,
        "status": job.status,
        "mode": job.mode,
        "instruments": symbols,
        "timeframe": job.timeframe,
        "from_date": job.date_from.isoformat(),
        "to_date": job.date_to.isoformat(),
        "current_instrument": job.current_instrument,
        "progress_percent": float(job.progress_percent or 0),
        "records_received": job.records_received,
        "records_inserted": job.records_inserted,
        "records_skipped": job.records_skipped,
        "records_updated": job.records_updated,
        "records_failed": job.records_failed,
        "records_invalid": job.records_invalid,
        "last_error": job.last_error,
        "log_message": job.log_message,
        "option_data_note": job.option_data_note,
        "sides": sides,
        "resume_from": job.resume_from.isoformat() if job.resume_from else None,
    }


def workspace(db: Session) -> dict:
    start, end = three_year_window()
    jobs = list(db.scalars(select(HistoricalImportJob).order_by(HistoricalImportJob.id.desc()).limit(8)).all())
    profiles = {row.symbol: row for row in db.scalars(select(UnderlyingProfile)).all()}
    quality = [quality_for(db, symbol, "1Minute", profiles.get(symbol)) for symbol in ("NIFTY", "SENSEX")]
    latest = jobs[0] if jobs else None
    return {
        "default_from": start.isoformat(),
        "default_to": end.isoformat(),
        "timeframe": "1Minute",
        "instruments": ["NIFTY", "SENSEX"],
        "option_data_note": OPTION_NOTE,
        "quality": quality,
        "job": job_payload(latest) if latest else None,
        "jobs": [job_payload(row) for row in jobs],
        "note": "Historical import does not write to the live candle table and never sends an order.",
    }


def run_queued_import(runtime) -> None:
    if not _lock.acquire(blocking=False):
        return
    db = session_factory()()
    try:
        job = db.scalar(
            select(HistoricalImportJob).where(HistoricalImportJob.status == "QUEUED").order_by(HistoricalImportJob.id.asc())
        )
        if job is None:
            return
        _run_job(runtime, db, job)
    except Exception:
        log.exception("historical import worker failed")
    finally:
        db.close()
        _lock.release()


def _run_job(runtime, db: Session, job: HistoricalImportJob) -> None:
    job.status = "RUNNING"
    job.updated_at = as_utc_naive(now_utc())
    job.log_message = "Import running"
    db.commit()
    access = load_access_token(db, runtime.settings)
    if not access:
        _fail(db, job, "Broker session is not connected")
        return
    kite = kite_client(runtime.settings, access)
    provider = KiteHistoricalMarketDataProvider(kite)
    symbols = parse_instruments(job.instruments)
    windows = chunk_windows(job.date_from, job.date_to, job.timeframe)
    total = max(1, len(symbols) * len(windows))
    done = 0
    try:
        for symbol in symbols:
            job.current_instrument = symbol
            profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
            spot = spot_instrument(db, symbol)
            if profile is None or spot is None:
                _fail(db, job, f"{symbol} spot instrument is missing from the instrument master")
                return
            token = int(spot.instrument_token)
            exchange = profile.spot_exchange
            tradingsymbol = profile.spot_symbol
            session_start = profile.session_start
            session_end = profile.session_end
            _event(db, job, f"{symbol} {job.date_from} → {job.date_to}")
            for chunk_start, chunk_end in windows:
                if job.mode == "RESUME" and job.resume_from is not None:
                    if combine_ist(chunk_end, time(23, 59)).replace(tzinfo=None) < job.resume_from:
                        done += 1
                        continue
                if job.mode == "INCREMENTAL":
                    latest = get_latest_historical_timestamp(db, symbol, job.timeframe)
                    miss_from, miss_to = missing_ranges(latest, chunk_start, chunk_end)
                    if miss_from > miss_to or (latest and miss_from > chunk_end):
                        done += 1
                        continue
                    chunk_start, chunk_end = miss_from, min(miss_to, chunk_end)
                _import_chunk(
                    db,
                    job,
                    provider,
                    symbol=symbol,
                    exchange=exchange,
                    tradingsymbol=tradingsymbol,
                    token=token,
                    chunk_start=chunk_start,
                    chunk_end=chunk_end,
                    session_start=session_start,
                    session_end=session_end,
                )
                done += 1
                job.progress_percent = Decimal(str(round(100.0 * done / total, 2)))
                job.resume_from = datetime.combine(chunk_end, time(15, 30))
                job.updated_at = as_utc_naive(now_utc())
                db.commit()
        job.status = "COMPLETED"
        job.progress_percent = Decimal("100")
        job.log_message = (
            f"Import completed · received {job.records_received} · inserted {job.records_inserted} · "
            f"skipped {job.records_skipped} · invalid {job.records_invalid} · failed {job.records_failed}"
        )
        _event(db, job, job.log_message)
        db.commit()
    except ProviderError as exc:
        _fail(db, job, str(exc)[:400])
    except Exception as exc:
        log.exception("historical import job %s failed", job.id)
        _fail(db, job, f"{type(exc).__name__}: {exc}"[:400])


def _import_chunk(
    db,
    job: HistoricalImportJob,
    provider,
    *,
    symbol: str,
    exchange: str,
    tradingsymbol: str,
    token: int,
    chunk_start: date,
    chunk_end: date,
    session_start,
    session_end,
) -> None:
    bars = provider.get_historical_data(
        instrument=symbol,
        exchange=exchange,
        timeframe=job.timeframe,
        from_date=chunk_start,
        to_date=chunk_end,
        token=token,
    )
    job.records_received += len(bars)
    if not bars:
        _event(db, job, f"{symbol} {chunk_start}–{chunk_end}: no candles from provider (weekend/holiday skipped)")
        return
    start_ts = bars[0].timestamp
    end_ts = bars[-1].timestamp
    known = existing_timestamps(db, symbol, job.timeframe, start_ts, end_ts)
    fresh = []
    for bar in bars:
        if is_weekend(bar.timestamp):
            continue
        aware = bar.timestamp.replace(tzinfo=bar.timestamp.tzinfo)
        if not in_session(aware if aware.tzinfo else bar.timestamp, session_start, session_end):
            continue
        reason = validate_ohlc(bar.open, bar.high, bar.low, bar.close)
        if reason:
            job.records_invalid += 1
            _event(db, job, f"Invalid {symbol} {bar.timestamp}: {reason}")
            continue
        if bar.timestamp in known:
            job.records_skipped += 1
            continue
        fresh.append(
            row_from_bar(
                instrument=symbol,
                exchange=exchange,
                timeframe=job.timeframe,
                bar=bar,
                source=getattr(provider, "source", "HistoricalAPI"),
                tradingsymbol=tradingsymbol,
            )
        )
        known.add(bar.timestamp)
    inserted = insert_historical_data(db, fresh)
    job.records_inserted += inserted
    job.log_message = f"{symbol} {chunk_start}–{chunk_end}: inserted {inserted}"
    _event(db, job, job.log_message)


def _fail(db: Session, job: HistoricalImportJob, message: str) -> None:
    job.status = "FAILED"
    job.last_error = message
    job.log_message = message
    job.updated_at = as_utc_naive(now_utc())
    _event(db, job, f"Import failed: {message}")
    db.commit()
    log.warning("historical import job %s failed: %s", job.id, message)


def _event(db: Session, job: HistoricalImportJob, message: str) -> None:
    db.add(
        HistoricalImportEvent(
            job_id=job.id,
            created_at=as_utc_naive(now_utc()),
            message=message[:400],
        )
    )

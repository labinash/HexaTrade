"""Seeds throwaway instruments and minute snapshots, runs the scanner, removes them.

Lets the OI maths be checked against known numbers while the market is closed and
Zerodha is not connected. Uses token ids in a private range and deletes them in a
finally block.
"""

import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.core.clock import now_ist, to_utc  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.models import Candle, Instrument, UnderlyingProfile  # noqa: E402
from app.db.session import init_engine, session_factory  # noqa: E402
from app.services.engine import AppRuntime  # noqa: E402
from app.services.oi_scanner import scan  # noqa: E402

TOKENS = {"FUT": 990000, "25000CE": 990001, "25050CE": 990002, "25000PE": 990003}
EXPIRY = date.today() + timedelta(days=9)
SNAPSHOTS = {
    # token: [(ist clock, oi, ltp)]
    TOKENS["25000CE"]: [(time(10, 0), 100000, 120.0), (time(10, 25), 125000, 140.0), (time(10, 30), 138000, 145.0)],
    TOKENS["25050CE"]: [(time(10, 0), 90000, 100.0), (time(10, 25), 118000, 120.0), (time(10, 30), 112000, 118.0)],
    TOKENS["25000PE"]: [(time(10, 0), 200000, 150.0), (time(10, 25), 210000, 140.0), (time(10, 30), 218000, 132.0)],
}


def seed(db, profile_id):
    today = date.today()
    rows = [
        Instrument(
            underlying_id=profile_id, instrument_token=TOKENS["FUT"], exchange="NFO", segment="NFO-FUT",
            tradingsymbol="VERIFYFUT", instrument_name="NIFTY", expiry=EXPIRY, strike=None,
            instrument_type="FUT", lot_size=75, tick_size=0.05, dump_date=today,
        )
    ]
    for label, token in TOKENS.items():
        if label == "FUT":
            continue
        strike = float(label[:5])
        rows.append(
            Instrument(
                underlying_id=profile_id, instrument_token=token, exchange="NFO", segment="NFO-OPT",
                tradingsymbol=f"VERIFY{label}", instrument_name="NIFTY", expiry=EXPIRY, strike=strike,
                instrument_type=label[5:], lot_size=75, tick_size=0.05, dump_date=today,
            )
        )
    db.add_all(rows)
    for token, points in SNAPSHOTS.items():
        for clock, oi, ltp in points:
            start = to_utc(datetime.combine(today, clock).replace(tzinfo=now_ist().tzinfo)).replace(tzinfo=None)
            db.add(
                Candle(
                    instrument_token=token, interval="minute", start_time=start,
                    open=Decimal(str(ltp)), high=Decimal(str(ltp)), low=Decimal(str(ltp)),
                    close=Decimal(str(ltp)), volume=5000, oi=oi,
                )
            )
    db.commit()


def cleanup(db):
    tokens = list(TOKENS.values())
    db.execute(delete(Candle).where(Candle.instrument_token.in_(tokens)))
    db.execute(delete(Instrument).where(Instrument.instrument_token.in_(tokens)))
    db.commit()


def main():
    settings = get_settings()
    init_engine(settings)
    db = session_factory()()
    runtime = AppRuntime(settings)
    runtime.db_ok = True
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == "NIFTY"))
    try:
        cleanup(db)
        seed(db, profile.id)
        runtime.cache.put({"token": TOKENS["FUT"], "ltp": 25010.0, "oi": 1000000, "volume": 900000, "timestamp": now_ist()})
        result = scan(db, runtime, "NIFTY", {"min_oi_change_interval": 0, "min_oi_change_reference": 0}, as_of=time(10, 30))
        print(f"state={result['state']} as_of={result['as_of']} atm={result['atm']} interval={result['interval_minutes']}m reference={result['reference_time']}")
        print(f"contracts total={result['total_contracts']} shown={result['shown_contracts']}")
        print(f"{'contract':<16}{'oi':>9}{'5m chg':>9}{'5m %':>8}{'since 10:00':>13}{'ref %':>8}  interpretation")
        for row in sorted(result["rows"], key=lambda item: (item["strike"], item["option_type"])):
            print(
                f"{row['tradingsymbol']:<16}{row['oi']:>9}{row['oi_change_interval']:>9}"
                f"{row['oi_change_interval_percent']:>8.2f}{row['oi_change_reference']:>13}"
                f"{row['oi_change_reference_percent']:>8.2f}  {row['interpretation']}"
            )
        summary = result["summary"]
        for key in ("largest_ce_increase", "largest_pe_increase", "largest_ce_decrease", "largest_pe_decrease"):
            item = summary[key]
            print(f"{key}: {item and f'{item['strike']:.0f} {item['option_type']} {item['oi_change']:+}'}")
        movers = result["top_movers"]
        print("additions:", [(m["strike"], m["option_type"], m["oi_change"]) for m in movers["additions"]])
        print("reductions:", [(m["strike"], m["option_type"], m["oi_change"]) for m in movers["reductions"]])

        filtered = scan(db, runtime, "NIFTY", {"min_oi_change_interval": 10000, "direction": "SIGNIFICANT_INCREASE"}, as_of=time(10, 30))
        print("significant increase only:", [r["tradingsymbol"] for r in filtered["rows"]])
        closed = scan(db, runtime, "NIFTY", {"min_oi_change_interval": 0, "min_oi_change_reference": 0})
        print(f"without as_of -> state={closed['state']} label={closed['state_label']} rows={closed['shown_contracts']}")
    finally:
        cleanup(db)
        db.close()


if __name__ == "__main__":
    main()

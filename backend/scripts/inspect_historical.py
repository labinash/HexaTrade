from sqlalchemy import text

from app.db.session import init_engine, session_factory


def main() -> None:
    init_engine()
    db = session_factory()()
    try:
        tables = db.execute(
            text(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_NAME IN ('market_historical_data','historical_import_job','candle')"
            )
        ).fetchall()
        print("tables", [row[0] for row in tables])
        try:
            print("mhd_count", db.execute(text("SELECT COUNT(*) FROM market_historical_data")).scalar())
            print(
                "mhd_groups",
                db.execute(
                    text(
                        "SELECT instrument, timeframe, contract_kind, COUNT(*) n, "
                        "MIN(trading_date), MAX(trading_date) "
                        "FROM market_historical_data "
                        "GROUP BY instrument, timeframe, contract_kind"
                    )
                ).fetchall(),
            )
        except Exception as exc:
            print("mhd", type(exc).__name__, exc)
        try:
            print(
                "jobs",
                db.execute(
                    text(
                        "SELECT TOP 8 id, status, instruments, timeframe, date_from, date_to, "
                        "records_inserted, records_failed, last_error, log_message "
                        "FROM historical_import_job ORDER BY id DESC"
                    )
                ).fetchall(),
            )
        except Exception as exc:
            print("jobs", type(exc).__name__, exc)
        try:
            print(
                "paper_runs",
                db.execute(
                    text(
                        "SELECT TOP 8 id, status, instrument, timeframe, date_from, date_to, "
                        "total_trades, net_pnl, option_data_available, last_error, option_data_note "
                        "FROM historical_paper_run ORDER BY id DESC"
                    )
                ).fetchall(),
            )
        except Exception as exc:
            print("paper_runs", type(exc).__name__, exc)
    finally:
        db.close()


if __name__ == "__main__":
    main()

from sqlalchemy import text
from app.db.session import init_engine, session_factory

init_engine()
db = session_factory()()


def q(sql):
    return db.execute(text(sql)).fetchall()


print(
    "sessions",
    q(
        """
SELECT instrument, COUNT(DISTINCT trading_date) sessions, COUNT(*) candles,
 MIN(trading_date) first_d, MAX(trading_date) last_d
 FROM market_historical_data WHERE contract_kind='INDEX' AND timeframe='1Minute'
 GROUP BY instrument
"""
    ),
)
print(
    "dups",
    q(
        """
SELECT instrument, COUNT(*) extra FROM (
  SELECT instrument, timestamp, COUNT(*) n
  FROM market_historical_data
  WHERE contract_kind='INDEX' AND timeframe='1Minute'
  GROUP BY instrument, timestamp HAVING COUNT(*)>1
) x GROUP BY instrument
"""
    ),
)
print(
    "one day nifty",
    q(
        """
SELECT trading_date, COUNT(*) n, MIN(timestamp), MAX(timestamp)
 FROM market_historical_data
 WHERE instrument='NIFTY' AND timeframe='1Minute' AND contract_kind='INDEX'
   AND trading_date='2026-01-15'
 GROUP BY trading_date
"""
    ),
)
print("cross mix", q("SELECT COUNT(*) FROM market_historical_data WHERE instrument NOT IN ('NIFTY','SENSEX')"))
print("option rows", q("SELECT COUNT(*) FROM market_historical_data WHERE contract_kind='OPTION'"))
try:
    print(
        "paper_trade",
        q("SELECT underlying, option_type, status, COUNT(*), SUM(net_pnl) FROM paper_trade GROUP BY underlying, option_type, status"),
    )
except Exception as exc:
    print("paper_trade", type(exc).__name__, str(exc)[:240])
try:
    print(
        "positions",
        q("SELECT execution_mode, status, COUNT(*), SUM(realized_pnl) FROM position GROUP BY execution_mode, status"),
    )
except Exception as exc:
    print("positions", type(exc).__name__, str(exc)[:240])
try:
    print(
        "hist trades",
        q(
            "SELECT TOP 5 id, instrument, option_type, quantity, entry_price, exit_price, gross_pnl, net_pnl, total_charges, slippage FROM historical_paper_trade ORDER BY id DESC"
        ),
    )
except Exception as exc:
    print("hist trades", type(exc).__name__, str(exc)[:240])
try:
    print("alerts", q("SELECT status, signal, COUNT(*) FROM alert_signal GROUP BY status, signal"))
except Exception as exc:
    print("alerts", type(exc).__name__, str(exc)[:240])
db.close()

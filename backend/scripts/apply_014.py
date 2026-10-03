from sqlalchemy import text

from app.db.session import get_engine

STMTS = [
    """
    IF COL_LENGTH('dbo.paper_auto_setting', 'paper_mode') IS NULL
    ALTER TABLE dbo.paper_auto_setting ADD paper_mode VARCHAR(24) NOT NULL CONSTRAINT DF_pas_mode DEFAULT ('MANUAL')
    """,
    """
    IF COL_LENGTH('dbo.paper_auto_setting', 'auto_paused') IS NULL
    ALTER TABLE dbo.paper_auto_setting ADD auto_paused BIT NOT NULL CONSTRAINT DF_pas_pause DEFAULT (0)
    """,
    """
    IF COL_LENGTH('dbo.paper_auto_setting', 'index_strategy_json') IS NULL
    ALTER TABLE dbo.paper_auto_setting ADD index_strategy_json NVARCHAR(MAX) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_run', 'performance_json') IS NULL
    ALTER TABLE dbo.historical_paper_run ADD performance_json NVARCHAR(MAX) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_run', 'warnings_json') IS NULL
    ALTER TABLE dbo.historical_paper_run ADD warnings_json NVARCHAR(MAX) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_run', 'same_candle_rule') IS NULL
    ALTER TABLE dbo.historical_paper_run ADD same_candle_rule VARCHAR(400) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_trade', 'buy_score') IS NULL
    ALTER TABLE dbo.historical_paper_trade ADD buy_score DECIMAL(18,4) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_trade', 'sell_score') IS NULL
    ALTER TABLE dbo.historical_paper_trade ADD sell_score DECIMAL(18,4) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_trade', 'explanation') IS NULL
    ALTER TABLE dbo.historical_paper_trade ADD explanation VARCHAR(400) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_trade', 'indicators_json') IS NULL
    ALTER TABLE dbo.historical_paper_trade ADD indicators_json NVARCHAR(MAX) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_trade', 'lot_size') IS NULL
    ALTER TABLE dbo.historical_paper_trade ADD lot_size INT NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_trade', 'slippage') IS NULL
    ALTER TABLE dbo.historical_paper_trade ADD slippage DECIMAL(18,4) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_trade', 'brokerage') IS NULL
    ALTER TABLE dbo.historical_paper_trade ADD brokerage DECIMAL(18,2) NULL
    """,
    """
    IF COL_LENGTH('dbo.historical_paper_trade', 'strategy_name') IS NULL
    ALTER TABLE dbo.historical_paper_trade ADD strategy_name VARCHAR(80) NULL
    """,
]


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))
    print("index strategy columns ready")


if __name__ == "__main__":
    main()

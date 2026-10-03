from sqlalchemy import text

from app.db.session import get_engine

STMTS = [
    """
    IF COL_LENGTH('dbo.app_setting', 'chart_live_enabled') IS NULL
    ALTER TABLE dbo.app_setting ADD chart_live_enabled BIT NOT NULL CONSTRAINT DF_app_chart_live DEFAULT (0)
    """,
    """
    IF COL_LENGTH('dbo.app_setting', 'max_lots_per_trade') IS NULL
    ALTER TABLE dbo.app_setting ADD max_lots_per_trade INT NOT NULL CONSTRAINT DF_app_max_lots DEFAULT (2)
    """,
    """
    UPDATE dbo.app_setting
    SET market_protection_percent = 2
    WHERE market_protection_percent IS NULL OR market_protection_percent <= 0
    """,
]


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))
    print("chart live trading columns ready")


if __name__ == "__main__":
    main()

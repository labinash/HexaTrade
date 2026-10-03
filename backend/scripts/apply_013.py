from sqlalchemy import text

from app.db.session import get_engine

STMTS = [
    """
    IF COL_LENGTH('dbo.paper_auto_setting', 'paper_instrument') IS NULL
    ALTER TABLE dbo.paper_auto_setting ADD paper_instrument VARCHAR(16) NOT NULL CONSTRAINT DF_pas_inst DEFAULT ('OPTIONS')
    """,
    """
    IF COL_LENGTH('dbo.paper_trade', 'instrument_kind') IS NULL
    ALTER TABLE dbo.paper_trade ADD instrument_kind VARCHAR(16) NOT NULL CONSTRAINT DF_pt_kind DEFAULT ('OPTION')
    """,
]


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))
    print("index paper columns ready")


if __name__ == "__main__":
    main()

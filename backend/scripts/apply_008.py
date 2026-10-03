from sqlalchemy import text

from app.db.session import get_engine

STMTS = [
    """
    IF COL_LENGTH('dbo.strategy_parameters', 'candidate_strike_range') IS NULL
        ALTER TABLE dbo.strategy_parameters
            ADD candidate_strike_range INT NOT NULL CONSTRAINT DF_sp_cand_range DEFAULT (5)
    """,
    """
    IF COL_LENGTH('dbo.strategy_parameters', 'min_candidate_score_percent') IS NULL
        ALTER TABLE dbo.strategy_parameters
            ADD min_candidate_score_percent DECIMAL(18,4) NOT NULL CONSTRAINT DF_sp_cand_min DEFAULT (60)
    """,
]


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))
        rows = conn.execute(
            text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME='strategy_parameters' AND COLUMN_NAME IN "
                "('candidate_strike_range','min_candidate_score_percent')"
            )
        ).fetchall()
    print("columns", [row[0] for row in rows])


if __name__ == "__main__":
    main()

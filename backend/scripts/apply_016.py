from sqlalchemy import text

from app.db.session import get_engine

STMTS = [
    """
    IF OBJECT_ID('dbo.trading_order_audit', 'U') IS NULL
    CREATE TABLE dbo.trading_order_audit (
        id                BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        created_at        DATETIME NOT NULL,
        updated_at        DATETIME NOT NULL,
        user_id           VARCHAR(32) NOT NULL CONSTRAINT DF_toa_user DEFAULT ('local'),
        tenant_id         VARCHAR(32) NOT NULL CONSTRAINT DF_toa_tenant DEFAULT ('local'),
        broker            VARCHAR(16) NOT NULL CONSTRAINT DF_toa_broker DEFAULT ('Zerodha'),
        execution_mode    VARCHAR(8) NOT NULL CONSTRAINT DF_toa_mode DEFAULT ('PAPER'),
        instrument        VARCHAR(16) NOT NULL CONSTRAINT DF_toa_inst DEFAULT (''),
        exchange          VARCHAR(8) NOT NULL CONSTRAINT DF_toa_ex DEFAULT (''),
        tradingsymbol     VARCHAR(64) NOT NULL CONSTRAINT DF_toa_ts DEFAULT (''),
        option_type       VARCHAR(4) NOT NULL CONSTRAINT DF_toa_ot DEFAULT (''),
        strike            DECIMAL(18,4) NULL,
        transaction_type  VARCHAR(8) NOT NULL CONSTRAINT DF_toa_side DEFAULT ('BUY'),
        quantity          INT NOT NULL CONSTRAINT DF_toa_qty DEFAULT (0),
        lots              INT NOT NULL CONSTRAINT DF_toa_lots DEFAULT (1),
        order_type        VARCHAR(8) NOT NULL CONSTRAINT DF_toa_otype DEFAULT ('MARKET'),
        product           VARCHAR(8) NOT NULL CONSTRAINT DF_toa_prod DEFAULT ('MIS'),
        requested_price   DECIMAL(18,4) NULL,
        signal_direction  VARCHAR(16) NOT NULL CONSTRAINT DF_toa_dir DEFAULT (''),
        signal_score      DECIMAL(18,4) NULL,
        signal_timestamp  DATETIME NULL,
        client_order_id   VARCHAR(64) NOT NULL CONSTRAINT DF_toa_cid DEFAULT (''),
        broker_order_id   VARCHAR(32) NULL,
        broker_status     VARCHAR(32) NOT NULL CONSTRAINT DF_toa_st DEFAULT (''),
        reject_reason     VARCHAR(400) NOT NULL CONSTRAINT DF_toa_rej DEFAULT (''),
        local_order_id    BIGINT NULL
    )
    """,
]


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))
    print("trading_order_audit ready")


if __name__ == "__main__":
    main()

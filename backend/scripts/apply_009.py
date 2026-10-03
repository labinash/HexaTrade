from sqlalchemy import text

from app.db.session import get_engine

STMTS = [
    """
    IF OBJECT_ID('dbo.paper_auto_setting', 'U') IS NULL
    CREATE TABLE dbo.paper_auto_setting (
        id INT NOT NULL PRIMARY KEY,
        enabled BIT NOT NULL CONSTRAINT DF_pas_enabled DEFAULT (0),
        min_signal_score DECIMAL(18,4) NOT NULL CONSTRAINT DF_pas_score DEFAULT (75),
        candidate_strike_range INT NOT NULL CONSTRAINT DF_pas_range DEFAULT (5),
        max_open_positions INT NOT NULL CONSTRAINT DF_pas_open DEFAULT (1),
        max_trades_per_day INT NOT NULL CONSTRAINT DF_pas_day DEFAULT (3),
        max_daily_loss DECIMAL(18,2) NOT NULL CONSTRAINT DF_pas_dloss DEFAULT (5000),
        max_loss_per_trade DECIMAL(18,2) NOT NULL CONSTRAINT DF_pas_tloss DEFAULT (2000),
        square_off TIME NOT NULL CONSTRAINT DF_pas_sq DEFAULT ('15:20'),
        allow_reentry BIT NOT NULL CONSTRAINT DF_pas_re DEFAULT (0),
        cooldown_minutes INT NOT NULL CONSTRAINT DF_pas_cd DEFAULT (15),
        entry_price_source VARCHAR(8) NOT NULL CONSTRAINT DF_pas_src DEFAULT ('LTP'),
        lots INT NOT NULL CONSTRAINT DF_pas_lots DEFAULT (1)
    )
    """,
    "IF NOT EXISTS (SELECT 1 FROM dbo.paper_auto_setting WHERE id = 1) INSERT INTO dbo.paper_auto_setting (id) VALUES (1)",
    """
    IF OBJECT_ID('dbo.charge_rate', 'U') IS NULL
    CREATE TABLE dbo.charge_rate (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        broker VARCHAR(32) NOT NULL CONSTRAINT DF_cr_broker DEFAULT ('Zerodha'),
        segment VARCHAR(16) NOT NULL CONSTRAINT DF_cr_seg DEFAULT ('NFO'),
        charge_type VARCHAR(16) NOT NULL,
        side VARCHAR(8) NOT NULL CONSTRAINT DF_cr_side DEFAULT ('BOTH'),
        rate_percent DECIMAL(18,4) NOT NULL CONSTRAINT DF_cr_pct DEFAULT (0),
        flat_amount DECIMAL(18,2) NOT NULL CONSTRAINT DF_cr_flat DEFAULT (0),
        cap_amount DECIMAL(18,2) NULL,
        taxable BIT NOT NULL CONSTRAINT DF_cr_tax DEFAULT (0),
        effective_from DATE NOT NULL,
        effective_to DATE NULL,
        is_active BIT NOT NULL CONSTRAINT DF_cr_act DEFAULT (1)
    )
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM dbo.charge_rate)
    INSERT INTO dbo.charge_rate (broker, segment, charge_type, side, rate_percent, flat_amount, cap_amount, taxable, effective_from, is_active)
    VALUES
        ('Zerodha', 'NFO', 'BROKERAGE', 'BOTH', 2.5, 0, 20, 1, '2026-04-01', 1),
        ('Zerodha', 'NFO', 'STT', 'SELL', 0.15, 0, NULL, 0, '2026-04-01', 1),
        ('Zerodha', 'NFO', 'EXCHANGE', 'BOTH', 0.05065, 0, NULL, 1, '2026-04-01', 1),
        ('Zerodha', 'NFO', 'SEBI', 'BOTH', 0.0001, 0, NULL, 1, '2026-04-01', 1),
        ('Zerodha', 'NFO', 'STAMP', 'BUY', 0.003, 0, NULL, 0, '2026-04-01', 1),
        ('Zerodha', 'NFO', 'OTHER', 'BOTH', 0.0001, 0, NULL, 1, '2026-04-01', 1)
    """,
    """
    IF OBJECT_ID('dbo.paper_trade', 'U') IS NULL
    CREATE TABLE dbo.paper_trade (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        idempotency_key VARCHAR(160) NOT NULL,
        signal_id VARCHAR(160) NOT NULL,
        underlying VARCHAR(16) NOT NULL,
        option_type VARCHAR(4) NOT NULL,
        tradingsymbol VARCHAR(64) NOT NULL,
        exchange VARCHAR(8) NOT NULL,
        expiry DATE NULL,
        strike DECIMAL(18,4) NOT NULL,
        instrument_token BIGINT NOT NULL,
        quantity INT NOT NULL,
        lot_size INT NOT NULL,
        lots INT NOT NULL,
        entry_time DATETIME NOT NULL,
        entry_price DECIMAL(18,4) NOT NULL,
        entry_underlying_price DECIMAL(18,4) NULL,
        entry_option_ltp DECIMAL(18,4) NULL,
        entry_signal_score DECIMAL(18,4) NOT NULL,
        entry_passed INT NOT NULL,
        entry_total INT NOT NULL,
        stop_loss DECIMAL(18,4) NULL,
        target DECIMAL(18,4) NULL,
        trail_price DECIMAL(18,4) NULL,
        mfe_price DECIMAL(18,4) NULL,
        exit_time DATETIME NULL,
        exit_price DECIMAL(18,4) NULL,
        exit_underlying_price DECIMAL(18,4) NULL,
        exit_reason VARCHAR(40) NULL,
        gross_pnl DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_gross DEFAULT (0),
        brokerage DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_brok DEFAULT (0),
        stt DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_stt DEFAULT (0),
        transaction_charges DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_txn DEFAULT (0),
        sebi_charges DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_sebi DEFAULT (0),
        gst DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_gst DEFAULT (0),
        stamp_duty DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_stamp DEFAULT (0),
        other_charges DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_oth DEFAULT (0),
        total_charges DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_tot DEFAULT (0),
        net_pnl DECIMAL(18,2) NOT NULL CONSTRAINT DF_pt_net DEFAULT (0),
        charges_estimated BIT NOT NULL CONSTRAINT DF_pt_est DEFAULT (1),
        status VARCHAR(16) NOT NULL CONSTRAINT DF_pt_st DEFAULT ('OPEN'),
        CONSTRAINT uq_paper_trade_idemp UNIQUE (idempotency_key)
    )
    """,
    """
    IF OBJECT_ID('dbo.paper_trade_condition', 'U') IS NULL
    CREATE TABLE dbo.paper_trade_condition (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        trade_id BIGINT NOT NULL,
        name VARCHAR(64) NOT NULL,
        passed BIT NOT NULL,
        current_value VARCHAR(80) NOT NULL CONSTRAINT DF_ptc_cur DEFAULT (''),
        required_value VARCHAR(80) NOT NULL CONSTRAINT DF_ptc_req DEFAULT (''),
        CONSTRAINT fk_ptc_trade FOREIGN KEY (trade_id) REFERENCES dbo.paper_trade(id)
    )
    """,
    """
    IF OBJECT_ID('dbo.paper_trade_event', 'U') IS NULL
    CREATE TABLE dbo.paper_trade_event (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        trade_id BIGINT NULL,
        created_at DATETIME NOT NULL,
        message VARCHAR(400) NOT NULL,
        underlying VARCHAR(16) NOT NULL CONSTRAINT DF_pte_und DEFAULT (''),
        CONSTRAINT fk_pte_trade FOREIGN KEY (trade_id) REFERENCES dbo.paper_trade(id)
    )
    """,
]


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))
    print("paper auto tables ready")


if __name__ == "__main__":
    main()

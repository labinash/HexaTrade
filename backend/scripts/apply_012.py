from sqlalchemy import text

from app.db.session import get_engine

STMTS = [
    """
    IF OBJECT_ID('dbo.alert_setting', 'U') IS NULL
    CREATE TABLE dbo.alert_setting (
        id INT NOT NULL PRIMARY KEY,
        notifications_enabled BIT NOT NULL CONSTRAINT DF_as_on DEFAULT (1),
        sound_enabled BIT NOT NULL CONSTRAINT DF_as_snd DEFAULT (0),
        browser_notify BIT NOT NULL CONSTRAINT DF_as_br DEFAULT (0),
        nifty_enabled BIT NOT NULL CONSTRAINT DF_as_nf DEFAULT (1),
        sensex_enabled BIT NOT NULL CONSTRAINT DF_as_sx DEFAULT (1),
        ce_alerts BIT NOT NULL CONSTRAINT DF_as_ce DEFAULT (1),
        pe_alerts BIT NOT NULL CONSTRAINT DF_as_pe DEFAULT (1),
        min_confidence DECIMAL(18,4) NOT NULL CONSTRAINT DF_as_th DEFAULT (70),
        min_score_separation DECIMAL(18,4) NOT NULL CONSTRAINT DF_as_sep DEFAULT (8),
        cooldown_seconds INT NOT NULL CONSTRAINT DF_as_cd DEFAULT (300),
        max_spread_percent DECIMAL(18,4) NOT NULL CONSTRAINT DF_as_sp DEFAULT (2),
        min_option_volume INT NOT NULL CONSTRAINT DF_as_vol DEFAULT (100),
        min_option_oi INT NOT NULL CONSTRAINT DF_as_oi DEFAULT (500),
        min_rr DECIMAL(18,4) NOT NULL CONSTRAINT DF_as_rr DEFAULT (1.5),
        sl_percent DECIMAL(18,4) NOT NULL CONSTRAINT DF_as_sl DEFAULT (20),
        target1_rr DECIMAL(18,4) NOT NULL CONSTRAINT DF_as_t1 DEFAULT (1.5),
        target2_rr DECIMAL(18,4) NOT NULL CONSTRAINT DF_as_t2 DEFAULT (2.5),
        supertrend_atr_mult DECIMAL(18,4) NOT NULL CONSTRAINT DF_as_st DEFAULT (3),
        time_start TIME NOT NULL CONSTRAINT DF_as_ts DEFAULT ('09:20'),
        time_end TIME NOT NULL CONSTRAINT DF_as_te DEFAULT ('15:10'),
        alert_only BIT NOT NULL CONSTRAINT DF_as_ao DEFAULT (1)
    )
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM dbo.alert_setting WHERE id = 1)
    INSERT INTO dbo.alert_setting (id) VALUES (1)
    """,
    """
    IF OBJECT_ID('dbo.alert_signal', 'U') IS NULL
    CREATE TABLE dbo.alert_signal (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        instrument VARCHAR(16) NOT NULL,
        option_type VARCHAR(4) NOT NULL,
        strike DECIMAL(18,4) NULL,
        expiry DATE NULL,
        tradingsymbol VARCHAR(64) NOT NULL CONSTRAINT DF_asi_sym DEFAULT (''),
        signal VARCHAR(8) NOT NULL CONSTRAINT DF_asi_sig DEFAULT ('WAIT'),
        status VARCHAR(24) NOT NULL CONSTRAINT DF_asi_st DEFAULT ('NEW'),
        confidence DECIMAL(18,4) NOT NULL CONSTRAINT DF_asi_cf DEFAULT (0),
        ce_score DECIMAL(18,4) NOT NULL CONSTRAINT DF_asi_ce DEFAULT (0),
        pe_score DECIMAL(18,4) NOT NULL CONSTRAINT DF_asi_pe DEFAULT (0),
        entry_price DECIMAL(18,4) NULL,
        entry_low DECIMAL(18,4) NULL,
        entry_high DECIMAL(18,4) NULL,
        stop_loss DECIMAL(18,4) NULL,
        target1 DECIMAL(18,4) NULL,
        target2 DECIMAL(18,4) NULL,
        risk_reward DECIMAL(18,4) NULL,
        last_premium DECIMAL(18,4) NULL,
        reasons_json NVARCHAR(MAX) NOT NULL CONSTRAINT DF_asi_rj DEFAULT ('[]'),
        blocked_json NVARCHAR(MAX) NOT NULL CONSTRAINT DF_asi_bj DEFAULT ('[]'),
        warning VARCHAR(200) NOT NULL CONSTRAINT DF_asi_w DEFAULT (''),
        data_time DATETIME NULL,
        acknowledged BIT NOT NULL CONSTRAINT DF_asi_ack DEFAULT (0),
        alert_only BIT NOT NULL CONSTRAINT DF_asi_ao DEFAULT (1),
        instrument_token BIGINT NULL
    )
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_alert_signal_created' AND object_id = OBJECT_ID('dbo.alert_signal'))
    CREATE INDEX ix_alert_signal_created ON dbo.alert_signal (instrument, created_at)
    """,
    """
    IF OBJECT_ID('dbo.alert_log', 'U') IS NULL
    CREATE TABLE dbo.alert_log (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        created_at DATETIME NOT NULL,
        instrument VARCHAR(16) NOT NULL CONSTRAINT DF_al_ins DEFAULT (''),
        option_type VARCHAR(4) NOT NULL CONSTRAINT DF_al_ot DEFAULT (''),
        event_type VARCHAR(32) NOT NULL,
        result VARCHAR(32) NOT NULL CONSTRAINT DF_al_res DEFAULT (''),
        reason VARCHAR(500) NOT NULL CONSTRAINT DF_al_r DEFAULT (''),
        close_price DECIMAL(18,4) NULL,
        score DECIMAL(18,4) NULL,
        signal_id BIGINT NULL
    )
    """,
]


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))
    print("alert engine tables ready")


if __name__ == "__main__":
    main()

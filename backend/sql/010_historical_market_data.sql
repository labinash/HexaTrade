/* HexTrade 010 - dedicated historical market data.

   Does not write into dbo.candle (live feed).
   Idempotent. */

USE HexTrade;
GO

IF OBJECT_ID('dbo.market_historical_data', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.market_historical_data (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        instrument VARCHAR(20) NOT NULL,
        exchange VARCHAR(10) NULL,
        timeframe VARCHAR(20) NOT NULL,
        trading_date DATE NOT NULL,
        timestamp DATETIME2 NOT NULL,
        open_price DECIMAL(18,4) NOT NULL,
        high_price DECIMAL(18,4) NOT NULL,
        low_price DECIMAL(18,4) NOT NULL,
        close_price DECIMAL(18,4) NOT NULL,
        volume BIGINT NULL,
        open_interest BIGINT NULL,
        data_source VARCHAR(100) NULL,
        contract_kind VARCHAR(16) NOT NULL CONSTRAINT DF_mhd_kind DEFAULT ('INDEX'),
        option_type VARCHAR(4) NOT NULL CONSTRAINT DF_mhd_opt DEFAULT (''),
        strike DECIMAL(18,4) NOT NULL CONSTRAINT DF_mhd_strike DEFAULT (0),
        expiry DATE NOT NULL CONSTRAINT DF_mhd_exp DEFAULT ('1900-01-01'),
        tradingsymbol VARCHAR(64) NOT NULL CONSTRAINT DF_mhd_sym DEFAULT (''),
        created_date DATETIME2 NOT NULL
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ux_mhd_instrument_ts_tf' AND object_id = OBJECT_ID('dbo.market_historical_data'))
    CREATE UNIQUE INDEX ux_mhd_instrument_ts_tf
    ON dbo.market_historical_data (instrument, timeframe, timestamp, contract_kind, option_type, strike, expiry);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_mhd_lookup' AND object_id = OBJECT_ID('dbo.market_historical_data'))
    CREATE INDEX ix_mhd_lookup ON dbo.market_historical_data (instrument, timeframe, timestamp);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_mhd_date' AND object_id = OBJECT_ID('dbo.market_historical_data'))
    CREATE INDEX ix_mhd_date ON dbo.market_historical_data (instrument, timeframe, trading_date);
GO

IF OBJECT_ID('dbo.historical_import_job', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.historical_import_job (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        created_at DATETIME2 NOT NULL,
        updated_at DATETIME2 NOT NULL,
        mode VARCHAR(16) NOT NULL,
        status VARCHAR(16) NOT NULL CONSTRAINT DF_hij_status DEFAULT ('QUEUED'),
        instruments VARCHAR(40) NOT NULL,
        timeframe VARCHAR(20) NOT NULL CONSTRAINT DF_hij_tf DEFAULT ('1Minute'),
        date_from DATE NOT NULL,
        date_to DATE NOT NULL,
        current_instrument VARCHAR(20) NOT NULL CONSTRAINT DF_hij_cur DEFAULT (''),
        resume_from DATETIME2 NULL,
        records_received INT NOT NULL CONSTRAINT DF_hij_recv DEFAULT (0),
        records_inserted INT NOT NULL CONSTRAINT DF_hij_ins DEFAULT (0),
        records_skipped INT NOT NULL CONSTRAINT DF_hij_skip DEFAULT (0),
        records_updated INT NOT NULL CONSTRAINT DF_hij_upd DEFAULT (0),
        records_failed INT NOT NULL CONSTRAINT DF_hij_fail DEFAULT (0),
        records_invalid INT NOT NULL CONSTRAINT DF_hij_inv DEFAULT (0),
        progress_percent DECIMAL(18,4) NOT NULL CONSTRAINT DF_hij_pct DEFAULT (0),
        last_error VARCHAR(400) NOT NULL CONSTRAINT DF_hij_err DEFAULT (''),
        log_message VARCHAR(400) NOT NULL CONSTRAINT DF_hij_log DEFAULT (''),
        option_data_note VARCHAR(400) NOT NULL CONSTRAINT DF_hij_opt DEFAULT ('')
    );
END
GO

IF OBJECT_ID('dbo.historical_import_event', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.historical_import_event (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        job_id BIGINT NOT NULL,
        created_at DATETIME2 NOT NULL,
        message VARCHAR(400) NOT NULL,
        CONSTRAINT fk_hie_job FOREIGN KEY (job_id) REFERENCES dbo.historical_import_job(id)
    );
    CREATE INDEX ix_hie_job ON dbo.historical_import_event (job_id);
END
GO

IF OBJECT_ID('dbo.historical_paper_run', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.historical_paper_run (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        created_at DATETIME2 NOT NULL,
        updated_at DATETIME2 NOT NULL,
        status VARCHAR(16) NOT NULL CONSTRAINT DF_hpr_status DEFAULT ('QUEUED'),
        instrument VARCHAR(20) NOT NULL,
        timeframe VARCHAR(20) NOT NULL CONSTRAINT DF_hpr_tf DEFAULT ('1Minute'),
        date_from DATE NOT NULL,
        date_to DATE NOT NULL,
        strategy_version_id INT NULL,
        strategy_label VARCHAR(80) NOT NULL CONSTRAINT DF_hpr_label DEFAULT (''),
        strategy_snapshot NVARCHAR(MAX) NOT NULL CONSTRAINT DF_hpr_snap DEFAULT (''),
        threshold DECIMAL(18,4) NOT NULL CONSTRAINT DF_hpr_th DEFAULT (75),
        initial_capital DECIMAL(18,2) NOT NULL CONSTRAINT DF_hpr_cap DEFAULT (100000),
        strike_range INT NOT NULL CONSTRAINT DF_hpr_rng DEFAULT (5),
        charge_note VARCHAR(200) NOT NULL CONSTRAINT DF_hpr_ch DEFAULT (''),
        option_data_available BIT NOT NULL CONSTRAINT DF_hpr_oa DEFAULT (0),
        option_data_note VARCHAR(400) NOT NULL CONSTRAINT DF_hpr_on DEFAULT (''),
        data_incomplete BIT NOT NULL CONSTRAINT DF_hpr_inc DEFAULT (0),
        last_error VARCHAR(400) NOT NULL CONSTRAINT DF_hpr_err DEFAULT (''),
        total_trades INT NOT NULL CONSTRAINT DF_hpr_tr DEFAULT (0),
        wins INT NOT NULL CONSTRAINT DF_hpr_w DEFAULT (0),
        losses INT NOT NULL CONSTRAINT DF_hpr_l DEFAULT (0),
        win_rate DECIMAL(18,4) NULL,
        gross_pnl DECIMAL(18,2) NOT NULL CONSTRAINT DF_hpr_g DEFAULT (0),
        total_charges DECIMAL(18,2) NOT NULL CONSTRAINT DF_hpr_tc DEFAULT (0),
        net_pnl DECIMAL(18,2) NOT NULL CONSTRAINT DF_hpr_n DEFAULT (0),
        max_drawdown DECIMAL(18,2) NOT NULL CONSTRAINT DF_hpr_dd DEFAULT (0),
        profit_factor DECIMAL(18,4) NULL,
        threshold_analysis NVARCHAR(MAX) NOT NULL CONSTRAINT DF_hpr_ta DEFAULT ('')
    );
END
GO

IF OBJECT_ID('dbo.historical_paper_trade', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.historical_paper_trade (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        run_id BIGINT NOT NULL,
        trade_date DATE NOT NULL,
        entry_time DATETIME2 NOT NULL,
        exit_time DATETIME2 NULL,
        instrument VARCHAR(20) NOT NULL,
        option_type VARCHAR(4) NOT NULL,
        strike DECIMAL(18,4) NULL,
        expiry DATE NULL,
        quantity INT NOT NULL,
        entry_price DECIMAL(18,4) NOT NULL,
        exit_price DECIMAL(18,4) NULL,
        signal_percent DECIMAL(18,4) NOT NULL,
        passed INT NOT NULL CONSTRAINT DF_hpt_p DEFAULT (0),
        total INT NOT NULL CONSTRAINT DF_hpt_t DEFAULT (0),
        gross_pnl DECIMAL(18,2) NOT NULL CONSTRAINT DF_hpt_g DEFAULT (0),
        total_charges DECIMAL(18,2) NOT NULL CONSTRAINT DF_hpt_c DEFAULT (0),
        net_pnl DECIMAL(18,2) NOT NULL CONSTRAINT DF_hpt_n DEFAULT (0),
        exit_reason VARCHAR(40) NOT NULL CONSTRAINT DF_hpt_x DEFAULT (''),
        CONSTRAINT fk_hpt_run FOREIGN KEY (run_id) REFERENCES dbo.historical_paper_run(id)
    );
    CREATE INDEX ix_hpt_run ON dbo.historical_paper_trade (run_id);
END
GO

IF OBJECT_ID('dbo.historical_condition_stat', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.historical_condition_stat (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        run_id BIGINT NOT NULL,
        side VARCHAR(8) NOT NULL,
        name VARCHAR(64) NOT NULL,
        evaluated INT NOT NULL CONSTRAINT DF_hcs_e DEFAULT (0),
        passed INT NOT NULL CONSTRAINT DF_hcs_p DEFAULT (0),
        failed INT NOT NULL CONSTRAINT DF_hcs_f DEFAULT (0),
        unavailable INT NOT NULL CONSTRAINT DF_hcs_u DEFAULT (0),
        CONSTRAINT fk_hcs_run FOREIGN KEY (run_id) REFERENCES dbo.historical_paper_run(id)
    );
    CREATE INDEX ix_hcs_run ON dbo.historical_condition_stat (run_id);
END
GO

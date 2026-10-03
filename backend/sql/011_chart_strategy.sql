/* HexTrade 011 - independent CE/PE strategy switches and chart signal events.

   OFF means no new entries for that side. Open paper positions stay open.
   Chart signals are hypothetical / paper only. No broker orders. */

USE HexTrade;
GO

IF COL_LENGTH('dbo.paper_auto_setting', 'ce_strategy_enabled') IS NULL
    ALTER TABLE dbo.paper_auto_setting ADD ce_strategy_enabled BIT NOT NULL CONSTRAINT DF_pas_ce DEFAULT (1);
GO

IF COL_LENGTH('dbo.paper_auto_setting', 'pe_strategy_enabled') IS NULL
    ALTER TABLE dbo.paper_auto_setting ADD pe_strategy_enabled BIT NOT NULL CONSTRAINT DF_pas_pe DEFAULT (1);
GO

IF OBJECT_ID('dbo.chart_signal', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.chart_signal (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        instrument VARCHAR(16) NOT NULL,
        timestamp DATETIME NOT NULL,
        signal_type VARCHAR(8) NOT NULL,
        option_type VARCHAR(4) NOT NULL,
        strike DECIMAL(18,4) NULL,
        expiry DATE NULL,
        option_ltp DECIMAL(18,4) NULL,
        underlying_ltp DECIMAL(18,4) NULL,
        strategy_score DECIMAL(18,4) NULL,
        passed_conditions INT NULL,
        total_conditions INT NULL,
        entry_price DECIMAL(18,4) NULL,
        target_price DECIMAL(18,4) NULL,
        stop_loss_price DECIMAL(18,4) NULL,
        exit_price DECIMAL(18,4) NULL,
        exit_reason VARCHAR(40) NULL,
        is_hypothetical BIT NOT NULL CONSTRAINT DF_cs_hyp DEFAULT (1),
        tradingsymbol VARCHAR(64) NOT NULL CONSTRAINT DF_cs_sym DEFAULT (''),
        quantity INT NULL,
        gross_pnl DECIMAL(18,2) NULL,
        total_charges DECIMAL(18,2) NULL,
        net_pnl DECIMAL(18,2) NULL,
        charges_estimated BIT NULL,
        source VARCHAR(16) NOT NULL CONSTRAINT DF_cs_src DEFAULT ('LIVE_PAPER'),
        paper_trade_id BIGINT NULL
    );
    CREATE UNIQUE INDEX ux_chart_signal ON dbo.chart_signal (instrument, timestamp, signal_type, option_type, source);
    CREATE INDEX ix_chart_signal_lookup ON dbo.chart_signal (instrument, timestamp);
END
GO

PRINT 'HexTrade 011 chart strategy ready.';
GO

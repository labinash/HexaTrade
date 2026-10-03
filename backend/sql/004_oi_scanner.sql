/* HexTrade 004 - OI Scanner.
   Idempotent. Run against the HexTrade database after 003_momentum_unit.sql.

   No new snapshot table is created. Minute OI history already lands in
   dbo.candle, written by the existing candle aggregator from the shared quote
   cache, and UQ_candle (instrument_token, interval, start_time) is the index
   the scanner seeks on. This script only adds the per-underlying settings and
   one covering index for the descending "latest snapshot before T" lookup. */

USE HexTrade;
GO

IF OBJECT_ID('dbo.oi_scanner_config', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.oi_scanner_config (
        id                          BIGINT IDENTITY(1,1) PRIMARY KEY,
        underlying_id               BIGINT NOT NULL UNIQUE REFERENCES dbo.underlying_profile(id),
        interval_minutes            INT           NOT NULL CONSTRAINT DF_osc_interval DEFAULT 5,
        reference_time              TIME(0)       NOT NULL CONSTRAINT DF_osc_reftime  DEFAULT '10:00',
        strikes_above               INT           NOT NULL CONSTRAINT DF_osc_above    DEFAULT 10,
        strikes_below               INT           NOT NULL CONSTRAINT DF_osc_below    DEFAULT 10,
        option_type                 VARCHAR(4)    NOT NULL CONSTRAINT DF_osc_type     DEFAULT 'ALL',
        expiry_mode                 VARCHAR(16)   NOT NULL CONSTRAINT DF_osc_expiry   DEFAULT 'nearest',
        min_oi_change_interval      DECIMAL(18,2) NOT NULL CONSTRAINT DF_osc_minint   DEFAULT 10000,
        min_oi_change_reference     DECIMAL(18,2) NOT NULL CONSTRAINT DF_osc_minref   DEFAULT 10000,
        direction                   VARCHAR(24)   NOT NULL CONSTRAINT DF_osc_dir      DEFAULT 'ALL',
        refresh_seconds             INT           NOT NULL CONSTRAINT DF_osc_refresh  DEFAULT 5,
        snapshot_tolerance_minutes  INT           NOT NULL CONSTRAINT DF_osc_tol      DEFAULT 3
    );
END
GO

/* Seeded per underlying. SENSEX gets its own smaller thresholds because its
   option open interest runs at a different scale to NIFTY. */
INSERT INTO dbo.oi_scanner_config (underlying_id, min_oi_change_interval, min_oi_change_reference, strikes_above, strikes_below)
SELECT u.id,
       CASE u.symbol WHEN 'SENSEX' THEN 2000 ELSE 10000 END,
       CASE u.symbol WHEN 'SENSEX' THEN 2000 ELSE 10000 END,
       10,
       10
FROM dbo.underlying_profile u
WHERE NOT EXISTS (SELECT 1 FROM dbo.oi_scanner_config c WHERE c.underlying_id = u.id);
GO

/* Supports "newest snapshot at or before T" without a sort, and carries oi,
   close and volume so the seek does not touch the base table. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_candle_token_interval_time' AND object_id = OBJECT_ID('dbo.candle'))
    CREATE INDEX IX_candle_token_interval_time
        ON dbo.candle (instrument_token, interval, start_time DESC)
        INCLUDE (oi, [close], volume);
GO

PRINT 'HexTrade 004 OI scanner ready.';
GO

/* HexTrade 005 - configurable strategy direction per underlying.

   strategy_parameters held one row per strategy version, shared by NIFTY and
   SENSEX. It now holds one row per version per underlying so the two indices
   can carry different directions and thresholds. Existing rows are claimed by
   NIFTY and a SENSEX copy is inserted alongside each of them, after which the
   SENSEX row is free to be tuned independently.

   Direction only changes the directional conditions. Futures volume, OI change,
   ATR and the trading window stay direction neutral, so no column for them is
   touched here.

   Idempotent. Safe to run more than once. */

USE HexTrade;
GO

IF COL_LENGTH('dbo.strategy_parameters', 'underlying') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD underlying NVARCHAR(16) NOT NULL CONSTRAINT DF_sp_underlying DEFAULT ('NIFTY');
GO

IF COL_LENGTH('dbo.strategy_parameters', 'direction') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD direction NVARCHAR(8) NOT NULL CONSTRAINT DF_sp_direction DEFAULT ('BULLISH');
GO

/* The bearish band is stored apart from the bullish one. Reusing 40-70 for a
   downtrend would demand strength the market is not showing. */
IF COL_LENGTH('dbo.strategy_parameters', 'rsi_min_bearish') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD rsi_min_bearish DECIMAL(18,4) NOT NULL CONSTRAINT DF_sp_rsi_min_bear DEFAULT (30);
GO

IF COL_LENGTH('dbo.strategy_parameters', 'rsi_max_bearish') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD rsi_max_bearish DECIMAL(18,4) NOT NULL CONSTRAINT DF_sp_rsi_max_bear DEFAULT (60);
GO

IF EXISTS (SELECT 1 FROM sys.key_constraints WHERE name = 'UQ_params_version' AND parent_object_id = OBJECT_ID('dbo.strategy_parameters'))
    ALTER TABLE dbo.strategy_parameters DROP CONSTRAINT UQ_params_version;
GO

IF NOT EXISTS (SELECT 1 FROM sys.key_constraints WHERE name = 'uq_params_version_underlying' AND parent_object_id = OBJECT_ID('dbo.strategy_parameters'))
    ALTER TABLE dbo.strategy_parameters
        ADD CONSTRAINT uq_params_version_underlying UNIQUE (strategy_version_id, underlying);
GO

IF NOT EXISTS (SELECT 1 FROM dbo.underlying_profile WHERE symbol = 'SENSEX')
BEGIN
    PRINT 'SENSEX profile missing, skipping the SENSEX parameter backfill.';
END
ELSE
BEGIN
    /* Seeded from the matching NIFTY row so nothing starts blank, then edited on
       the Strategy screen. SENSEX is not assumed to keep NIFTY thresholds. */
    INSERT INTO dbo.strategy_parameters (
        strategy_version_id, underlying, direction,
        ema_period, ema_enabled,
        momentum_time, momentum_min_points, momentum_min_percent, momentum_unit,
        oi_time, oi_min_change, oi_unit,
        volume_ema_period, volume_enabled, vwap_enabled,
        rsi_period, rsi_min, rsi_max, rsi_min_bearish, rsi_max_bearish, rsi_enabled,
        atr_period, atr_enabled, candle_bullish_required,
        time_start, time_end, time_enabled,
        timeframe_minutes, evaluate_on, confirmation_mode, min_conditions,
        price_source, oi_source, bullish_option_type, bearish_option_type
    )
    SELECT
        p.strategy_version_id, 'SENSEX', p.direction,
        p.ema_period, p.ema_enabled,
        p.momentum_time, p.momentum_min_points, p.momentum_min_percent, p.momentum_unit,
        p.oi_time, p.oi_min_change, p.oi_unit,
        p.volume_ema_period, p.volume_enabled, p.vwap_enabled,
        p.rsi_period, p.rsi_min, p.rsi_max, p.rsi_min_bearish, p.rsi_max_bearish, p.rsi_enabled,
        p.atr_period, p.atr_enabled, p.candle_bullish_required,
        p.time_start, p.time_end, p.time_enabled,
        p.timeframe_minutes, p.evaluate_on, p.confirmation_mode, p.min_conditions,
        p.price_source, p.oi_source, p.bullish_option_type, p.bearish_option_type
    FROM dbo.strategy_parameters AS p
    WHERE p.underlying = 'NIFTY'
      AND NOT EXISTS (
          SELECT 1 FROM dbo.strategy_parameters AS s
          WHERE s.strategy_version_id = p.strategy_version_id AND s.underlying = 'SENSEX'
      );
END
GO

PRINT 'HexTrade 005 strategy direction ready.';
GO

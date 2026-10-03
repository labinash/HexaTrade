/* HexTrade 007 - independent CE / PE option-buy thresholds and exit arming.

   CE uses nine mandatory premium+futures checks. PE uses eleven, including the
   11:00 premium and OI references. Thresholds stay per underlying so SENSEX
   never inherits a NIFTY ATR or OI figure from source defaults alone.

   trail_arm_fraction delays a trailing stop until that share of the target
   distance has been reached. premium_trend_exit watches option premium versus
   its own EMA20. Neither setting places a live order.

   Idempotent. Safe to run more than once. */

USE HexTrade;
GO

IF COL_LENGTH('dbo.strategy_parameters', 'option_momentum_percent') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD option_momentum_percent DECIMAL(18,4) NOT NULL CONSTRAINT DF_sp_opt_mom DEFAULT (3);
GO

IF COL_LENGTH('dbo.strategy_parameters', 'pe_midday_time') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD pe_midday_time TIME NOT NULL CONSTRAINT DF_sp_pe_mid DEFAULT ('11:00');
GO

IF COL_LENGTH('dbo.strategy_parameters', 'pe_oi_since_midday') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD pe_oi_since_midday DECIMAL(18,2) NOT NULL CONSTRAINT DF_sp_pe_oi DEFAULT (10000);
GO

IF COL_LENGTH('dbo.exit_policy', 'trail_arm_fraction') IS NULL
    ALTER TABLE dbo.exit_policy
        ADD trail_arm_fraction DECIMAL(18,4) NOT NULL CONSTRAINT DF_ep_trail_arm DEFAULT (0.5);
GO

IF COL_LENGTH('dbo.exit_policy', 'premium_trend_exit') IS NULL
    ALTER TABLE dbo.exit_policy
        ADD premium_trend_exit BIT NOT NULL CONSTRAINT DF_ep_prem_trend DEFAULT (1);
GO

PRINT 'HexTrade 007 CE/PE option-buy thresholds ready.';
GO

/* HexTrade 003 - momentum threshold in points or percent.
   Idempotent. Existing rows keep the points behaviour they already had. */

USE HexTrade;
GO

IF COL_LENGTH('dbo.strategy_parameters', 'momentum_min_percent') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD momentum_min_percent DECIMAL(18,4) NOT NULL CONSTRAINT DF_sp_mom_pct DEFAULT (0.05);
GO

IF COL_LENGTH('dbo.strategy_parameters', 'momentum_unit') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD momentum_unit NVARCHAR(8) NOT NULL CONSTRAINT DF_sp_mom_unit DEFAULT ('points');
GO

PRINT 'HexTrade 003 momentum unit ready.';
GO

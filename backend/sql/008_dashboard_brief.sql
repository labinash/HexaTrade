/* HexTrade 008 - dashboard candidate ranking thresholds.

   candidate_strike_range is ATM ± N for scoring, not a hardcoded strike.
   min_candidate_score_percent stops a weak score being shown as a strong pick.

   Idempotent. */

USE HexTrade;
GO

IF COL_LENGTH('dbo.strategy_parameters', 'candidate_strike_range') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD candidate_strike_range INT NOT NULL CONSTRAINT DF_sp_cand_range DEFAULT (5);
GO

IF COL_LENGTH('dbo.strategy_parameters', 'min_candidate_score_percent') IS NULL
    ALTER TABLE dbo.strategy_parameters
        ADD min_candidate_score_percent DECIMAL(18,4) NOT NULL CONSTRAINT DF_sp_cand_min DEFAULT (60);
GO

PRINT 'HexTrade 008 dashboard brief thresholds ready.';
GO

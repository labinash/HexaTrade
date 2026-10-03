/* HexTrade 006 - dashboard option trading panel.

   The panel reuses the existing option chain, strategy, selection and paper
   order services, so this migration only adds the fields needed to record a
   manually sized paper order and to detect an accidental double submit.

   No live-order capability is added anywhere by this change.

   Idempotent. Safe to run more than once. */

USE HexTrade;
GO

/* How long a repeat manual paper buy on the same contract and side counts as an
   accidental duplicate. Configurable rather than fixed in code. */
IF COL_LENGTH('dbo.app_setting', 'duplicate_window_seconds') IS NULL
    ALTER TABLE dbo.app_setting
        ADD duplicate_window_seconds INT NOT NULL CONSTRAINT DF_as_dup_window DEFAULT (60);
GO

/* Which screen recorded the proposal. Existing rows came from the selection
   screen, which is what the default preserves. */
IF COL_LENGTH('dbo.paper_trade_proposal', 'source') IS NULL
    ALTER TABLE dbo.paper_trade_proposal
        ADD source NVARCHAR(32) NOT NULL CONSTRAINT DF_ptp_source DEFAULT ('selection');
GO

/* What the strategy said when the order was recorded. Stored so a trade taken
   while the strategy had not confirmed stays identifiable later. */
IF COL_LENGTH('dbo.paper_trade_proposal', 'direction_state') IS NULL
    ALTER TABLE dbo.paper_trade_proposal
        ADD direction_state NVARCHAR(24) NOT NULL CONSTRAINT DF_ptp_dir_state DEFAULT ('');
GO

IF COL_LENGTH('dbo.paper_trade_proposal', 'direction_score') IS NULL
    ALTER TABLE dbo.paper_trade_proposal ADD direction_score INT NULL;
GO

IF COL_LENGTH('dbo.paper_trade_proposal', 'signal_confirmed') IS NULL
    ALTER TABLE dbo.paper_trade_proposal
        ADD signal_confirmed BIT NOT NULL CONSTRAINT DF_ptp_confirmed DEFAULT (0);
GO

IF COL_LENGTH('dbo.paper_trade_proposal', 'order_type') IS NULL
    ALTER TABLE dbo.paper_trade_proposal
        ADD order_type NVARCHAR(8) NOT NULL CONSTRAINT DF_ptp_order_type DEFAULT ('MARKET');
GO

/* The quote the user was looking at when they confirmed, kept alongside the
   simulated fill price so slippage against the shown price stays auditable. */
IF COL_LENGTH('dbo.paper_trade_proposal', 'requested_price') IS NULL
    ALTER TABLE dbo.paper_trade_proposal ADD requested_price DECIMAL(18,4) NULL;
GO

PRINT 'HexTrade 006 option panel ready.';
GO

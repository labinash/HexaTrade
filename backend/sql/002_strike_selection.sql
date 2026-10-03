/* HexTrade 002 - option strike selection engine.
   Idempotent. Run against the HexTrade database after 001_hextrade.sql.
   Each underlying gets its own configuration row; NIFTY values never apply to SENSEX. */

USE HexTrade;
GO

IF OBJECT_ID('dbo.strike_selection_config', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.strike_selection_config (
        id                      BIGINT IDENTITY(1,1) PRIMARY KEY,
        underlying_id           BIGINT NOT NULL UNIQUE REFERENCES dbo.underlying_profile(id),
        strikes_each_side       INT            NOT NULL CONSTRAINT DF_ssc_side      DEFAULT 2,
        min_volume              BIGINT         NOT NULL CONSTRAINT DF_ssc_vol       DEFAULT 1000,
        min_oi                  BIGINT         NOT NULL CONSTRAINT DF_ssc_oi        DEFAULT 5000,
        max_spread_percent      DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_spread    DEFAULT 2,
        max_quote_age_seconds   INT            NOT NULL CONSTRAINT DF_ssc_age       DEFAULT 10,
        min_premium             DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_pmin      DEFAULT 20,
        max_premium             DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_pmax      DEFAULT 400,
        min_reward_risk         DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_rr        DEFAULT 1.5,
        min_score               DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_score     DEFAULT 50,
        preferred_delta_min     DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_pdmin     DEFAULT 0.35,
        preferred_delta_max     DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_pdmax     DEFAULT 0.60,
        acceptable_delta_min    DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_admin     DEFAULT 0.25,
        acceptable_delta_max    DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_admax     DEFAULT 0.70,
        delta_missing_action    VARCHAR(8)     NOT NULL CONSTRAINT DF_ssc_dma       DEFAULT 'warn',
        iv_high_ratio           DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_ivr       DEFAULT 1.15,
        iv_high_action          VARCHAR(8)     NOT NULL CONSTRAINT DF_ssc_iva       DEFAULT 'warn',
        atr_multiplier          DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_atrm      DEFAULT 1,
        stop_method             VARCHAR(24)    NOT NULL CONSTRAINT DF_ssc_sm        DEFAULT 'option_percent',
        stop_option_percent     DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_sop       DEFAULT 20,
        stop_atr_multiplier     DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_satr      DEFAULT 1,
        target_reward_risk      DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_trr       DEFAULT 2,
        max_holding_minutes     INT            NOT NULL CONSTRAINT DF_ssc_hold      DEFAULT 60,
        near_expiry_days        INT            NOT NULL CONSTRAINT DF_ssc_near      DEFAULT 2,
        far_expiry_days         INT            NOT NULL CONSTRAINT DF_ssc_far       DEFAULT 21,
        account_capital         DECIMAL(18,2)  NOT NULL CONSTRAINT DF_ssc_cap       DEFAULT 100000,
        max_risk_percent        DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_risk      DEFAULT 1,
        max_lots                INT            NOT NULL CONSTRAINT DF_ssc_lots      DEFAULT 5,
        max_capital_allocation  DECIMAL(18,2)  NOT NULL CONSTRAINT DF_ssc_alloc     DEFAULT 50000,
        max_daily_loss          DECIMAL(18,2)  NOT NULL CONSTRAINT DF_ssc_dloss     DEFAULT 5000,
        weight_liquidity        DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_wliq      DEFAULT 20,
        weight_spread           DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_wspr      DEFAULT 15,
        weight_delta            DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_wdel      DEFAULT 15,
        weight_reward_risk      DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_wrr       DEFAULT 20,
        weight_volume_oi        DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_wvoi      DEFAULT 10,
        weight_iv               DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_wiv       DEFAULT 5,
        weight_premium          DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_wprem     DEFAULT 5,
        weight_expiry           DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_wexp      DEFAULT 5,
        weight_atm_distance     DECIMAL(18,4)  NOT NULL CONSTRAINT DF_ssc_watm      DEFAULT 5
    );
END
GO

IF OBJECT_ID('dbo.strike_selection_run', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.strike_selection_run (
        id                    BIGINT IDENTITY(1,1) PRIMARY KEY,
        created_at            DATETIME2(3)  NOT NULL,
        underlying            VARCHAR(16)   NOT NULL,
        direction             VARCHAR(16)   NOT NULL,
        option_type           VARCHAR(4)    NULL,
        signal_id             BIGINT        NULL REFERENCES dbo.signal(id),
        strategy_version_id   BIGINT        NULL REFERENCES dbo.strategy_version(id),
        spot                  DECIMAL(18,4) NULL,
        futures_ltp           DECIMAL(18,4) NULL,
        atm                   DECIMAL(18,4) NULL,
        strike_interval       DECIMAL(18,4) NULL,
        atr                   DECIMAL(18,4) NULL,
        expected_move         DECIMAL(18,4) NULL,
        conditions_passed     INT           NOT NULL CONSTRAINT DF_ssr_cp DEFAULT 0,
        conditions_failed     INT           NOT NULL CONSTRAINT DF_ssr_cf DEFAULT 0,
        available             BIT           NOT NULL CONSTRAINT DF_ssr_av DEFAULT 0,
        selected_symbol       VARCHAR(64)   NULL,
        selected_score        DECIMAL(18,4) NULL,
        candidate_count       INT           NOT NULL CONSTRAINT DF_ssr_cc DEFAULT 0,
        passed_count          INT           NOT NULL CONSTRAINT DF_ssr_pc DEFAULT 0,
        no_trade_reasons      VARCHAR(1000) NOT NULL CONSTRAINT DF_ssr_nr DEFAULT ''
    );
    CREATE INDEX IX_ssr_created ON dbo.strike_selection_run(created_at DESC);
    CREATE INDEX IX_ssr_underlying ON dbo.strike_selection_run(underlying, created_at DESC);
END
GO

IF OBJECT_ID('dbo.strike_selection_candidate', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.strike_selection_candidate (
        id                 BIGINT IDENTITY(1,1) PRIMARY KEY,
        run_id             BIGINT        NOT NULL REFERENCES dbo.strike_selection_run(id),
        rank               INT           NOT NULL CONSTRAINT DF_ssk_rank DEFAULT 0,
        tradingsymbol      VARCHAR(64)   NOT NULL,
        instrument_token   BIGINT        NOT NULL,
        strike             DECIMAL(18,4) NOT NULL,
        option_type        VARCHAR(4)    NOT NULL,
        expiry             DATE          NULL,
        moneyness          VARCHAR(8)    NOT NULL CONSTRAINT DF_ssk_mny DEFAULT '',
        strikes_from_atm   INT           NOT NULL CONSTRAINT DF_ssk_sfa DEFAULT 0,
        ltp                DECIMAL(18,4) NULL,
        bid                DECIMAL(18,4) NULL,
        ask                DECIMAL(18,4) NULL,
        spread_percent     DECIMAL(18,4) NULL,
        volume             BIGINT        NULL,
        oi                 BIGINT        NULL,
        oi_change          BIGINT        NULL,
        iv                 DECIMAL(18,4) NULL,
        iv_band            VARCHAR(8)    NOT NULL CONSTRAINT DF_ssk_ivb DEFAULT '',
        delta              DECIMAL(18,4) NULL,
        gamma              DECIMAL(18,8) NULL,
        theta_per_day      DECIMAL(18,4) NULL,
        vega_per_point     DECIMAL(18,4) NULL,
        days_to_expiry     INT           NULL,
        entry              DECIMAL(18,4) NULL,
        stop_loss          DECIMAL(18,4) NULL,
        target             DECIMAL(18,4) NULL,
        reward_risk        DECIMAL(18,4) NULL,
        allowed_lots       INT           NOT NULL CONSTRAINT DF_ssk_lots DEFAULT 0,
        quantity           INT           NOT NULL CONSTRAINT DF_ssk_qty  DEFAULT 0,
        risk_amount        DECIMAL(18,2) NULL,
        score              DECIMAL(18,4) NOT NULL CONSTRAINT DF_ssk_score DEFAULT 0,
        status             VARCHAR(8)    NOT NULL CONSTRAINT DF_ssk_st   DEFAULT 'PASS',
        reject_reasons     VARCHAR(1000) NOT NULL CONSTRAINT DF_ssk_rej  DEFAULT '',
        oi_interpretation  VARCHAR(120)  NOT NULL CONSTRAINT DF_ssk_oii  DEFAULT ''
    );
    CREATE INDEX IX_ssk_run ON dbo.strike_selection_candidate(run_id, rank);
END
GO

IF OBJECT_ID('dbo.paper_trade_proposal', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.paper_trade_proposal (
        id                 BIGINT IDENTITY(1,1) PRIMARY KEY,
        created_at         DATETIME2(3)  NOT NULL,
        run_id             BIGINT        NULL REFERENCES dbo.strike_selection_run(id),
        underlying         VARCHAR(16)   NOT NULL,
        direction          VARCHAR(16)   NOT NULL,
        tradingsymbol      VARCHAR(64)   NOT NULL,
        instrument_token   BIGINT        NOT NULL,
        option_type        VARCHAR(4)    NOT NULL,
        strike             DECIMAL(18,4) NOT NULL,
        expiry             DATE          NULL,
        entry              DECIMAL(18,4) NOT NULL,
        stop_loss          DECIMAL(18,4) NOT NULL,
        target             DECIMAL(18,4) NOT NULL,
        quantity           INT           NOT NULL,
        lots               INT           NOT NULL,
        risk_amount        DECIMAL(18,2) NULL,
        reward_risk        DECIMAL(18,4) NULL,
        score              DECIMAL(18,4) NOT NULL CONSTRAINT DF_ptp_score DEFAULT 0,
        selection_reasons  VARCHAR(1000) NOT NULL CONSTRAINT DF_ptp_rs DEFAULT '',
        warnings           VARCHAR(1000) NOT NULL CONSTRAINT DF_ptp_wr DEFAULT '',
        position_id        BIGINT        NULL REFERENCES dbo.position(id)
    );
    CREATE INDEX IX_ptp_created ON dbo.paper_trade_proposal(created_at DESC);
END
GO

/* Seed one configuration row per underlying. SENSEX is seeded with its own
   independent values rather than a copy of NIFTY. */
INSERT INTO dbo.strike_selection_config (underlying_id, min_volume, min_oi, min_premium, max_premium)
SELECT u.id,
       CASE u.symbol WHEN 'SENSEX' THEN 300 ELSE 1000 END,
       CASE u.symbol WHEN 'SENSEX' THEN 1500 ELSE 5000 END,
       CASE u.symbol WHEN 'SENSEX' THEN 30 ELSE 20 END,
       CASE u.symbol WHEN 'SENSEX' THEN 600 ELSE 400 END
FROM dbo.underlying_profile u
WHERE NOT EXISTS (SELECT 1 FROM dbo.strike_selection_config c WHERE c.underlying_id = u.id);
GO

PRINT 'HexTrade 002 strike selection objects ready.';
GO

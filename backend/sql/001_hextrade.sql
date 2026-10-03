/*
    HexTrade — SQL Server schema
    Run in SSMS, or:

    sqlcmd -S localhost\SQLEXPRESS -E -i "C:\D\Code snippets\HexTrade\backend\sql\001_hextrade.sql"

    Safe to run again. Creates database HexTrade if it is missing,
    then creates tables and the starter NIFTY / SENSEX rows.
*/

IF DB_ID(N'HexTrade') IS NULL
BEGIN
    CREATE DATABASE HexTrade;
END
GO

USE HexTrade;
GO

IF OBJECT_ID(N'dbo.underlying_profile', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.underlying_profile (
        id                    BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        symbol                NVARCHAR(16)  NOT NULL,
        spot_exchange         NVARCHAR(8)   NOT NULL,
        spot_symbol           NVARCHAR(64)  NOT NULL,
        derivatives_exchange  NVARCHAR(8)   NOT NULL,
        instrument_name       NVARCHAR(32)  NOT NULL,
        atr_threshold         DECIMAL(18,4) NULL,
        session_start         TIME          NOT NULL CONSTRAINT DF_underlying_session_start DEFAULT ('09:15'),
        session_end           TIME          NOT NULL CONSTRAINT DF_underlying_session_end DEFAULT ('15:30'),
        CONSTRAINT UQ_underlying_symbol UNIQUE (symbol)
    );
END
GO

IF OBJECT_ID(N'dbo.instrument', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.instrument (
        id                BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        underlying_id     BIGINT NULL,
        instrument_token  BIGINT NOT NULL,
        exchange          NVARCHAR(8)  NOT NULL,
        segment           NVARCHAR(16) NOT NULL,
        tradingsymbol     NVARCHAR(64) NOT NULL,
        instrument_name   NVARCHAR(64) NOT NULL,
        expiry            DATE NULL,
        strike            DECIMAL(18,4) NULL,
        instrument_type   NVARCHAR(8) NOT NULL,
        lot_size          INT NOT NULL,
        tick_size         DECIMAL(18,4) NOT NULL,
        dump_date         DATE NOT NULL,
        CONSTRAINT FK_instrument_underlying FOREIGN KEY (underlying_id) REFERENCES dbo.underlying_profile(id),
        CONSTRAINT UQ_instrument_dump UNIQUE (exchange, tradingsymbol, dump_date)
    );
    CREATE INDEX IX_instrument_lookup ON dbo.instrument (instrument_name, instrument_type, expiry);
END
GO

IF OBJECT_ID(N'dbo.candle', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.candle (
        id                BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        instrument_token  BIGINT NOT NULL,
        interval          NVARCHAR(16) NOT NULL,
        start_time        DATETIME2 NOT NULL,
        [open]            DECIMAL(18,4) NOT NULL,
        high              DECIMAL(18,4) NOT NULL,
        low               DECIMAL(18,4) NOT NULL,
        [close]           DECIMAL(18,4) NOT NULL,
        volume            BIGINT NOT NULL CONSTRAINT DF_candle_volume DEFAULT (0),
        oi                BIGINT NOT NULL CONSTRAINT DF_candle_oi DEFAULT (0),
        CONSTRAINT UQ_candle UNIQUE (instrument_token, interval, start_time)
    );
    CREATE INDEX IX_candle_token ON dbo.candle (instrument_token);
END
GO

IF OBJECT_ID(N'dbo.oi_reference', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.oi_reference (
        id                BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        instrument_token  BIGINT NOT NULL,
        session_date      DATE NOT NULL,
        reference_time    TIME NOT NULL,
        oi                BIGINT NOT NULL,
        [close]           DECIMAL(18,4) NOT NULL,
        CONSTRAINT UQ_oi_reference UNIQUE (instrument_token, session_date, reference_time)
    );
END
GO

IF OBJECT_ID(N'dbo.broker_session', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.broker_session (
        id                       BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        user_id                  NVARCHAR(32) NOT NULL,
        access_token_encrypted   VARBINARY(MAX) NOT NULL,
        expires_at               DATETIME2 NOT NULL,
        created_at               DATETIME2 NOT NULL
    );
END
GO

IF OBJECT_ID(N'dbo.app_setting', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.app_setting (
        id                          INT NOT NULL PRIMARY KEY,
        trading_mode                NVARCHAR(16) NOT NULL CONSTRAINT DF_app_mode DEFAULT ('ANALYSIS'),
        kill_switch                 BIT NOT NULL CONSTRAINT DF_app_kill DEFAULT (0),
        auto_entry_armed            BIT NOT NULL CONSTRAINT DF_app_entry DEFAULT (0),
        auto_exit_armed             BIT NOT NULL CONSTRAINT DF_app_exit DEFAULT (0),
        live_armed                  BIT NOT NULL CONSTRAINT DF_app_live DEFAULT (0),
        product                     NVARCHAR(8) NOT NULL CONSTRAINT DF_app_product DEFAULT ('MIS'),
        order_type                  NVARCHAR(8) NOT NULL CONSTRAINT DF_app_ordertype DEFAULT ('LIMIT'),
        lots                        INT NOT NULL CONSTRAINT DF_app_lots DEFAULT (1),
        market_protection_percent   DECIMAL(18,4) NULL,
        slippage_points             DECIMAL(18,4) NOT NULL CONSTRAINT DF_app_slip DEFAULT (0.5),
        brokerage_per_order         DECIMAL(18,2) NOT NULL CONSTRAINT DF_app_brokerage DEFAULT (20),
        statutory_charge_bps        DECIMAL(18,4) NOT NULL CONSTRAINT DF_app_bps DEFAULT (0),
        strikes_above               INT NOT NULL CONSTRAINT DF_app_above DEFAULT (10),
        strikes_below               INT NOT NULL CONSTRAINT DF_app_below DEFAULT (10),
        chain_refresh_seconds       INT NOT NULL CONSTRAINT DF_app_refresh DEFAULT (1),
        mis_cutoff                  TIME NOT NULL CONSTRAINT DF_app_mis DEFAULT ('15:20'),
        cooldown_seconds            INT NOT NULL CONSTRAINT DF_app_cool DEFAULT (300),
        risk_free_rate              DECIMAL(18,4) NOT NULL CONSTRAINT DF_app_rate DEFAULT (0.07)
    );
END
GO

IF OBJECT_ID(N'dbo.strategy', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.strategy (
        id    BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        name  NVARCHAR(64) NOT NULL,
        CONSTRAINT UQ_strategy_name UNIQUE (name)
    );
END
GO

IF OBJECT_ID(N'dbo.strategy_version', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.strategy_version (
        id               BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        strategy_id      BIGINT NOT NULL,
        version_number   INT NOT NULL,
        created_at       DATETIME2 NOT NULL,
        note             NVARCHAR(200) NOT NULL CONSTRAINT DF_version_note DEFAULT (''),
        CONSTRAINT FK_version_strategy FOREIGN KEY (strategy_id) REFERENCES dbo.strategy(id),
        CONSTRAINT UQ_strategy_version UNIQUE (strategy_id, version_number)
    );
END
GO

IF OBJECT_ID(N'dbo.strategy_parameters', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.strategy_parameters (
        id                       BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        strategy_version_id      BIGINT NOT NULL,
        ema_period               INT NOT NULL CONSTRAINT DF_sp_ema DEFAULT (20),
        ema_enabled              BIT NOT NULL CONSTRAINT DF_sp_ema_on DEFAULT (1),
        momentum_time            TIME NOT NULL CONSTRAINT DF_sp_mom_time DEFAULT ('09:20'),
        momentum_min_points      DECIMAL(18,4) NOT NULL CONSTRAINT DF_sp_mom DEFAULT (5),
        oi_time                  TIME NOT NULL CONSTRAINT DF_sp_oi_time DEFAULT ('09:20'),
        oi_min_change            DECIMAL(18,2) NOT NULL CONSTRAINT DF_sp_oi DEFAULT (10000),
        oi_unit                  NVARCHAR(16) NOT NULL CONSTRAINT DF_sp_oi_unit DEFAULT ('contracts'),
        volume_ema_period        INT NOT NULL CONSTRAINT DF_sp_vol_ema DEFAULT (20),
        volume_enabled           BIT NOT NULL CONSTRAINT DF_sp_vol_on DEFAULT (1),
        vwap_enabled             BIT NOT NULL CONSTRAINT DF_sp_vwap DEFAULT (1),
        rsi_period               INT NOT NULL CONSTRAINT DF_sp_rsi_p DEFAULT (14),
        rsi_min                  DECIMAL(18,4) NOT NULL CONSTRAINT DF_sp_rsi_min DEFAULT (40),
        rsi_max                  DECIMAL(18,4) NOT NULL CONSTRAINT DF_sp_rsi_max DEFAULT (70),
        rsi_enabled              BIT NOT NULL CONSTRAINT DF_sp_rsi_on DEFAULT (1),
        atr_period               INT NOT NULL CONSTRAINT DF_sp_atr_p DEFAULT (14),
        atr_enabled              BIT NOT NULL CONSTRAINT DF_sp_atr_on DEFAULT (1),
        candle_bullish_required  BIT NOT NULL CONSTRAINT DF_sp_candle DEFAULT (1),
        time_start               TIME NOT NULL CONSTRAINT DF_sp_t0 DEFAULT ('09:30'),
        time_end                 TIME NOT NULL CONSTRAINT DF_sp_t1 DEFAULT ('15:00'),
        time_enabled             BIT NOT NULL CONSTRAINT DF_sp_time_on DEFAULT (1),
        timeframe_minutes        INT NOT NULL CONSTRAINT DF_sp_tf DEFAULT (1),
        evaluate_on              NVARCHAR(16) NOT NULL CONSTRAINT DF_sp_eval DEFAULT ('candle_close'),
        confirmation_mode        NVARCHAR(16) NOT NULL CONSTRAINT DF_sp_conf DEFAULT ('all'),
        min_conditions           INT NOT NULL CONSTRAINT DF_sp_min DEFAULT (9),
        price_source             NVARCHAR(32) NOT NULL CONSTRAINT DF_sp_price DEFAULT ('front_futures'),
        oi_source                NVARCHAR(32) NOT NULL CONSTRAINT DF_sp_oisrc DEFAULT ('front_futures'),
        bullish_option_type      NVARCHAR(4) NOT NULL CONSTRAINT DF_sp_ce DEFAULT ('CE'),
        bearish_option_type      NVARCHAR(4) NOT NULL CONSTRAINT DF_sp_pe DEFAULT ('PE'),
        CONSTRAINT FK_params_version FOREIGN KEY (strategy_version_id) REFERENCES dbo.strategy_version(id),
        CONSTRAINT UQ_params_version UNIQUE (strategy_version_id)
    );
END
GO

IF OBJECT_ID(N'dbo.option_selection_rule', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.option_selection_rule (
        id                    BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        strategy_version_id   BIGINT NOT NULL,
        method                NVARCHAR(32) NOT NULL CONSTRAINT DF_os_method DEFAULT ('atm'),
        strike_offset         INT NOT NULL CONSTRAINT DF_os_offset DEFAULT (0),
        premium_min           DECIMAL(18,4) NULL,
        premium_max           DECIMAL(18,4) NULL,
        min_volume            BIGINT NOT NULL CONSTRAINT DF_os_vol DEFAULT (0),
        min_oi                BIGINT NOT NULL CONSTRAINT DF_os_oi DEFAULT (0),
        max_spread            DECIMAL(18,4) NULL,
        min_liquidity         DECIMAL(18,4) NOT NULL CONSTRAINT DF_os_liq DEFAULT (0),
        expiry_rule           NVARCHAR(32) NOT NULL CONSTRAINT DF_os_exp DEFAULT ('nearest'),
        CONSTRAINT FK_option_version FOREIGN KEY (strategy_version_id) REFERENCES dbo.strategy_version(id),
        CONSTRAINT UQ_option_version UNIQUE (strategy_version_id)
    );
END
GO

IF OBJECT_ID(N'dbo.exit_policy', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.exit_policy (
        id                    BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        strategy_version_id   BIGINT NOT NULL,
        stop_enabled          BIT NOT NULL CONSTRAINT DF_ex_stop_on DEFAULT (1),
        stop_style            NVARCHAR(16) NOT NULL CONSTRAINT DF_ex_stop_style DEFAULT ('points'),
        stop_points           DECIMAL(18,4) NOT NULL CONSTRAINT DF_ex_stop_pts DEFAULT (10),
        stop_percent          DECIMAL(18,4) NOT NULL CONSTRAINT DF_ex_stop_pct DEFAULT (20),
        stop_atr_mult         DECIMAL(18,4) NOT NULL CONSTRAINT DF_ex_stop_atr DEFAULT (1),
        target_enabled        BIT NOT NULL CONSTRAINT DF_ex_tgt_on DEFAULT (1),
        target_style          NVARCHAR(16) NOT NULL CONSTRAINT DF_ex_tgt_style DEFAULT ('rr'),
        target_points         DECIMAL(18,4) NOT NULL CONSTRAINT DF_ex_tgt_pts DEFAULT (20),
        target_percent        DECIMAL(18,4) NOT NULL CONSTRAINT DF_ex_tgt_pct DEFAULT (40),
        target_rr             DECIMAL(18,4) NOT NULL CONSTRAINT DF_ex_tgt_rr DEFAULT (2),
        trail_enabled         BIT NOT NULL CONSTRAINT DF_ex_tr_on DEFAULT (0),
        trail_style           NVARCHAR(16) NOT NULL CONSTRAINT DF_ex_tr_style DEFAULT ('points'),
        trail_points          DECIMAL(18,4) NOT NULL CONSTRAINT DF_ex_tr_pts DEFAULT (8),
        trail_percent         DECIMAL(18,4) NOT NULL CONSTRAINT DF_ex_tr_pct DEFAULT (10),
        trail_atr_mult        DECIMAL(18,4) NOT NULL CONSTRAINT DF_ex_tr_atr DEFAULT (1),
        trail_uses_live_atr   BIT NOT NULL CONSTRAINT DF_ex_tr_live DEFAULT (0),
        reversal_ema          BIT NOT NULL CONSTRAINT DF_ex_rev_ema DEFAULT (1),
        reversal_vwap         BIT NOT NULL CONSTRAINT DF_ex_rev_vwap DEFAULT (1),
        reversal_rsi          BIT NOT NULL CONSTRAINT DF_ex_rev_rsi DEFAULT (0),
        time_exit_enabled     BIT NOT NULL CONSTRAINT DF_ex_time_on DEFAULT (1),
        time_exit             TIME NOT NULL CONSTRAINT DF_ex_time DEFAULT ('15:15'),
        max_hold_enabled      BIT NOT NULL CONSTRAINT DF_ex_hold_on DEFAULT (0),
        max_hold_minutes      INT NOT NULL CONSTRAINT DF_ex_hold DEFAULT (60),
        priority              NVARCHAR(200) NOT NULL CONSTRAINT DF_ex_pri DEFAULT ('EMERGENCY,STOP_LOSS,STRATEGY_INVALIDATED,TARGET,TRAILING_STOP,MAX_HOLD,TIME_EXIT'),
        CONSTRAINT FK_exit_version FOREIGN KEY (strategy_version_id) REFERENCES dbo.strategy_version(id),
        CONSTRAINT UQ_exit_version UNIQUE (strategy_version_id)
    );
END
GO

IF OBJECT_ID(N'dbo.risk_policy', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.risk_policy (
        id                          BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        strategy_version_id         BIGINT NOT NULL,
        max_capital                 DECIMAL(18,2) NOT NULL CONSTRAINT DF_rk_cap DEFAULT (100000),
        max_quantity                INT NOT NULL CONSTRAINT DF_rk_qty DEFAULT (5000),
        max_daily_loss              DECIMAL(18,2) NOT NULL CONSTRAINT DF_rk_loss DEFAULT (5000),
        max_trades_per_day          INT NOT NULL CONSTRAINT DF_rk_trades DEFAULT (3),
        max_open_positions          INT NOT NULL CONSTRAINT DF_rk_open DEFAULT (2),
        max_loss_per_position       DECIMAL(18,2) NOT NULL CONSTRAINT DF_rk_pos DEFAULT (2000),
        max_exposure                DECIMAL(18,2) NOT NULL CONSTRAINT DF_rk_exp DEFAULT (200000),
        max_spread                  DECIMAL(18,4) NOT NULL CONSTRAINT DF_rk_spread DEFAULT (2),
        min_option_volume           BIGINT NOT NULL CONSTRAINT DF_rk_vol DEFAULT (0),
        min_option_oi               BIGINT NOT NULL CONSTRAINT DF_rk_oi DEFAULT (0),
        entry_start                 TIME NOT NULL CONSTRAINT DF_rk_start DEFAULT ('09:30'),
        entry_stop                  TIME NOT NULL CONSTRAINT DF_rk_stop DEFAULT ('15:00'),
        forced_square_off           TIME NOT NULL CONSTRAINT DF_rk_sq DEFAULT ('15:15'),
        max_trades_per_underlying   INT NOT NULL CONSTRAINT DF_rk_und DEFAULT (2),
        cooldown_seconds            INT NOT NULL CONSTRAINT DF_rk_cool DEFAULT (300),
        CONSTRAINT FK_risk_version FOREIGN KEY (strategy_version_id) REFERENCES dbo.strategy_version(id),
        CONSTRAINT UQ_risk_version UNIQUE (strategy_version_id)
    );
END
GO

IF OBJECT_ID(N'dbo.signal', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.signal (
        id                    BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        strategy_version_id   BIGINT NOT NULL,
        underlying            NVARCHAR(16) NOT NULL,
        created_at            DATETIME2 NOT NULL,
        direction             NVARCHAR(16) NOT NULL,
        passed                BIT NOT NULL CONSTRAINT DF_sig_pass DEFAULT (0),
        actionable            BIT NOT NULL CONSTRAINT DF_sig_act DEFAULT (0),
        close_price           DECIMAL(18,4) NULL,
        ema_value             DECIMAL(18,4) NULL,
        momentum_value        DECIMAL(18,4) NULL,
        oi_change             DECIMAL(18,2) NULL,
        volume_value          DECIMAL(18,2) NULL,
        volume_ema            DECIMAL(18,2) NULL,
        vwap_value            DECIMAL(18,4) NULL,
        rsi_value             DECIMAL(18,4) NULL,
        atr_value             DECIMAL(18,4) NULL,
        failed_reasons        NVARCHAR(500) NOT NULL CONSTRAINT DF_sig_fail DEFAULT (''),
        CONSTRAINT FK_signal_version FOREIGN KEY (strategy_version_id) REFERENCES dbo.strategy_version(id)
    );
    CREATE INDEX IX_signal_underlying ON dbo.signal (underlying);
    CREATE INDEX IX_signal_created ON dbo.signal (created_at);
END
GO

IF OBJECT_ID(N'dbo.signal_condition', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.signal_condition (
        id              BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        signal_id       BIGINT NOT NULL,
        sort_order      INT NOT NULL,
        name            NVARCHAR(64) NOT NULL,
        current_value   NVARCHAR(64) NOT NULL,
        required_value  NVARCHAR(64) NOT NULL,
        status          NVARCHAR(8) NOT NULL,
        CONSTRAINT FK_condition_signal FOREIGN KEY (signal_id) REFERENCES dbo.signal(id)
    );
END
GO

IF OBJECT_ID(N'dbo.order_record', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.order_record (
        id                 BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        idempotency_key    NVARCHAR(64) NOT NULL,
        signal_id          BIGINT NULL,
        position_id        BIGINT NULL,
        execution_mode     NVARCHAR(8) NOT NULL,
        side               NVARCHAR(8) NOT NULL,
        exchange           NVARCHAR(8) NOT NULL,
        tradingsymbol      NVARCHAR(64) NOT NULL,
        instrument_token   BIGINT NOT NULL,
        quantity           INT NOT NULL,
        order_type         NVARCHAR(8) NOT NULL,
        price              DECIMAL(18,4) NULL,
        status             NVARCHAR(24) NOT NULL,
        broker_order_id    NVARCHAR(32) NULL,
        tag                NVARCHAR(20) NOT NULL,
        rejection_reason   NVARCHAR(300) NOT NULL CONSTRAINT DF_ord_reason DEFAULT (''),
        created_at         DATETIME2 NOT NULL,
        CONSTRAINT UQ_order_idempotency UNIQUE (idempotency_key),
        CONSTRAINT FK_order_signal FOREIGN KEY (signal_id) REFERENCES dbo.signal(id)
    );
END
GO

IF OBJECT_ID(N'dbo.fill', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.fill (
        id          BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        order_id    BIGINT NOT NULL,
        quantity    INT NOT NULL,
        price       DECIMAL(18,4) NOT NULL,
        filled_at   DATETIME2 NOT NULL,
        CONSTRAINT FK_fill_order FOREIGN KEY (order_id) REFERENCES dbo.order_record(id)
    );
END
GO

IF OBJECT_ID(N'dbo.position', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.position (
        id                    BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        user_label            NVARCHAR(32) NOT NULL CONSTRAINT DF_pos_user DEFAULT ('local'),
        underlying            NVARCHAR(16) NOT NULL,
        exchange              NVARCHAR(8) NOT NULL,
        tradingsymbol         NVARCHAR(64) NOT NULL,
        instrument_token      BIGINT NOT NULL,
        option_type           NVARCHAR(4) NOT NULL,
        strike                DECIMAL(18,4) NOT NULL,
        expiry                DATE NULL,
        entry_time            DATETIME2 NOT NULL,
        entry_price           DECIMAL(18,4) NOT NULL,
        quantity              INT NOT NULL,
        stop_loss             DECIMAL(18,4) NULL,
        target                DECIMAL(18,4) NULL,
        trail_price           DECIMAL(18,4) NULL,
        atr_at_entry          DECIMAL(18,4) NULL,
        exit_time             DATETIME2 NULL,
        exit_price            DECIMAL(18,4) NULL,
        realized_pnl          DECIMAL(18,2) NULL,
        charges               DECIMAL(18,2) NOT NULL CONSTRAINT DF_pos_charges DEFAULT (0),
        exit_reason           NVARCHAR(32) NULL,
        strategy_version_id   BIGINT NOT NULL,
        signal_id             BIGINT NULL,
        execution_mode        NVARCHAR(8) NOT NULL,
        status                NVARCHAR(16) NOT NULL CONSTRAINT DF_pos_status DEFAULT ('OPEN'),
        mfe_price             DECIMAL(18,4) NULL,
        CONSTRAINT FK_position_version FOREIGN KEY (strategy_version_id) REFERENCES dbo.strategy_version(id),
        CONSTRAINT FK_position_signal FOREIGN KEY (signal_id) REFERENCES dbo.signal(id)
    );
END
GO

IF OBJECT_ID(N'dbo.position_event', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.position_event (
        id            BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        position_id   BIGINT NOT NULL,
        created_at    DATETIME2 NOT NULL,
        event_type    NVARCHAR(32) NOT NULL,
        reason        NVARCHAR(64) NOT NULL,
        price         DECIMAL(18,4) NULL,
        CONSTRAINT FK_event_position FOREIGN KEY (position_id) REFERENCES dbo.position(id)
    );
END
GO

IF OBJECT_ID(N'dbo.audit_event', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.audit_event (
        id                    BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        created_at            DATETIME2 NOT NULL,
        event_type            NVARCHAR(40) NOT NULL,
        strategy_version_id   BIGINT NULL,
        underlying            NVARCHAR(16) NOT NULL CONSTRAINT DF_aud_und DEFAULT (''),
        instrument            NVARCHAR(64) NOT NULL CONSTRAINT DF_aud_ins DEFAULT (''),
        user_label            NVARCHAR(32) NOT NULL CONSTRAINT DF_aud_user DEFAULT ('local'),
        input_close           DECIMAL(18,4) NULL,
        input_rsi             DECIMAL(18,4) NULL,
        input_atr             DECIMAL(18,4) NULL,
        input_oi_change       DECIMAL(18,2) NULL,
        result                NVARCHAR(32) NOT NULL CONSTRAINT DF_aud_result DEFAULT (''),
        reason                NVARCHAR(500) NOT NULL CONSTRAINT DF_aud_reason DEFAULT (''),
        CONSTRAINT FK_audit_version FOREIGN KEY (strategy_version_id) REFERENCES dbo.strategy_version(id)
    );
    CREATE INDEX IX_audit_created ON dbo.audit_event (created_at);
    CREATE INDEX IX_audit_type ON dbo.audit_event (event_type);
END
GO

IF OBJECT_ID(N'dbo.backtest_run', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.backtest_run (
        id                        BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        created_at                DATETIME2 NOT NULL,
        underlying                NVARCHAR(16) NOT NULL,
        strategy_version_id       BIGINT NOT NULL,
        date_from                 DATETIME2 NOT NULL,
        date_to                   DATETIME2 NOT NULL,
        timeframe                 NVARCHAR(16) NOT NULL,
        slippage_points           DECIMAL(18,4) NOT NULL,
        brokerage_per_order       DECIMAL(18,2) NOT NULL,
        statutory_charge_bps      DECIMAL(18,4) NOT NULL,
        trade_count               INT NOT NULL CONSTRAINT DF_bt_count DEFAULT (0),
        wins                      INT NOT NULL CONSTRAINT DF_bt_wins DEFAULT (0),
        losses                    INT NOT NULL CONSTRAINT DF_bt_losses DEFAULT (0),
        gross_pnl                 DECIMAL(18,2) NOT NULL CONSTRAINT DF_bt_gross DEFAULT (0),
        net_pnl                   DECIMAL(18,2) NOT NULL CONSTRAINT DF_bt_net DEFAULT (0),
        max_drawdown              DECIMAL(18,2) NOT NULL CONSTRAINT DF_bt_dd DEFAULT (0),
        average_win               DECIMAL(18,2) NOT NULL CONSTRAINT DF_bt_aw DEFAULT (0),
        average_loss              DECIMAL(18,2) NOT NULL CONSTRAINT DF_bt_al DEFAULT (0),
        win_rate                  DECIMAL(18,4) NOT NULL CONSTRAINT DF_bt_wr DEFAULT (0),
        profit_factor             DECIMAL(18,4) NULL,
        max_consecutive_losses    INT NOT NULL CONSTRAINT DF_bt_streak DEFAULT (0),
        average_holding_minutes   DECIMAL(18,4) NOT NULL CONSTRAINT DF_bt_hold DEFAULT (0),
        option_pnl_available      BIT NOT NULL CONSTRAINT DF_bt_opt DEFAULT (0),
        note                      NVARCHAR(400) NOT NULL CONSTRAINT DF_bt_note DEFAULT (''),
        CONSTRAINT FK_backtest_version FOREIGN KEY (strategy_version_id) REFERENCES dbo.strategy_version(id)
    );
END
GO

IF OBJECT_ID(N'dbo.backtest_trade', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.backtest_trade (
        id                BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        backtest_run_id   BIGINT NOT NULL,
        entry_time        DATETIME2 NOT NULL,
        exit_time         DATETIME2 NOT NULL,
        entry_price       DECIMAL(18,4) NOT NULL,
        exit_price        DECIMAL(18,4) NOT NULL,
        quantity          INT NOT NULL,
        gross_pnl         DECIMAL(18,2) NOT NULL,
        net_pnl           DECIMAL(18,2) NOT NULL,
        exit_reason       NVARCHAR(32) NOT NULL,
        holding_minutes   DECIMAL(18,4) NOT NULL,
        CONSTRAINT FK_bt_trade_run FOREIGN KEY (backtest_run_id) REFERENCES dbo.backtest_run(id)
    );
END
GO

/* Starter rows. SENSEX ATR threshold is left empty on purpose. */
IF NOT EXISTS (SELECT 1 FROM dbo.underlying_profile WHERE symbol = N'NIFTY')
BEGIN
    INSERT INTO dbo.underlying_profile
        (symbol, spot_exchange, spot_symbol, derivatives_exchange, instrument_name, atr_threshold, session_start, session_end)
    VALUES
        (N'NIFTY', N'NSE', N'NIFTY 50', N'NFO', N'NIFTY', 15, '09:15', '15:30');
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.underlying_profile WHERE symbol = N'SENSEX')
BEGIN
    INSERT INTO dbo.underlying_profile
        (symbol, spot_exchange, spot_symbol, derivatives_exchange, instrument_name, atr_threshold, session_start, session_end)
    VALUES
        (N'SENSEX', N'BSE', N'SENSEX', N'BFO', N'SENSEX', NULL, '09:15', '15:30');
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.app_setting WHERE id = 1)
BEGIN
    INSERT INTO dbo.app_setting (id, trading_mode, kill_switch, auto_entry_armed, auto_exit_armed, live_armed)
    VALUES (1, N'ANALYSIS', 0, 0, 0, 0);
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.strategy WHERE name = N'Index futures bullish')
BEGIN
    INSERT INTO dbo.strategy (name) VALUES (N'Index futures bullish');

    DECLARE @strategy_id BIGINT = SCOPE_IDENTITY();

    INSERT INTO dbo.strategy_version (strategy_id, version_number, created_at, note)
    VALUES (@strategy_id, 1, SYSUTCDATETIME(), N'Initial user-specified parameters');

    DECLARE @version_id BIGINT = SCOPE_IDENTITY();

    INSERT INTO dbo.strategy_parameters (strategy_version_id) VALUES (@version_id);
    INSERT INTO dbo.exit_policy (strategy_version_id) VALUES (@version_id);
    INSERT INTO dbo.risk_policy (strategy_version_id) VALUES (@version_id);
    INSERT INTO dbo.option_selection_rule (strategy_version_id) VALUES (@version_id);
END
GO

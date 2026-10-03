from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _pk():
    """SQL Server uses BIGINT IDENTITY. SQLite only autoincrements INTEGER, which
    the in-memory test database needs."""
    return BigInteger().with_variant(Integer, "sqlite")


def _money():
    return Numeric(18, 2)


def _px():
    return Numeric(18, 4)


class UnderlyingProfile(Base):
    __tablename__ = "underlying_profile"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True)
    spot_exchange: Mapped[str] = mapped_column(String(8))
    spot_symbol: Mapped[str] = mapped_column(String(64))
    derivatives_exchange: Mapped[str] = mapped_column(String(8))
    instrument_name: Mapped[str] = mapped_column(String(32))
    atr_threshold: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    session_start: Mapped[time] = mapped_column(Time, default=time(9, 15))
    session_end: Mapped[time] = mapped_column(Time, default=time(15, 30))


class Instrument(Base):
    __tablename__ = "instrument"
    __table_args__ = (
        UniqueConstraint("exchange", "tradingsymbol", "dump_date", name="uq_instrument_dump"),
        Index("ix_instrument_lookup", "instrument_name", "instrument_type", "expiry"),
    )

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    underlying_id: Mapped[int | None] = mapped_column(ForeignKey("underlying_profile.id"), nullable=True)
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    exchange: Mapped[str] = mapped_column(String(8))
    segment: Mapped[str] = mapped_column(String(16))
    tradingsymbol: Mapped[str] = mapped_column(String(64))
    instrument_name: Mapped[str] = mapped_column(String(64))
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    instrument_type: Mapped[str] = mapped_column(String(8))
    lot_size: Mapped[int] = mapped_column(Integer)
    tick_size: Mapped[Decimal] = mapped_column(_px())
    dump_date: Mapped[date] = mapped_column(Date)


class Candle(Base):
    __tablename__ = "candle"
    __table_args__ = (
        UniqueConstraint("instrument_token", "interval", "start_time", name="uq_candle"),
    )

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    instrument_token: Mapped[int] = mapped_column(BigInteger, index=True)
    interval: Mapped[str] = mapped_column(String(16))
    start_time: Mapped[datetime] = mapped_column(DateTime)
    open: Mapped[Decimal] = mapped_column(_px())
    high: Mapped[Decimal] = mapped_column(_px())
    low: Mapped[Decimal] = mapped_column(_px())
    close: Mapped[Decimal] = mapped_column(_px())
    volume: Mapped[int] = mapped_column(BigInteger, default=0)
    oi: Mapped[int] = mapped_column(BigInteger, default=0)


class OiReference(Base):
    __tablename__ = "oi_reference"
    __table_args__ = (
        UniqueConstraint("instrument_token", "session_date", "reference_time", name="uq_oi_reference"),
    )

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    session_date: Mapped[date] = mapped_column(Date)
    reference_time: Mapped[time] = mapped_column(Time)
    oi: Mapped[int] = mapped_column(BigInteger)
    close: Mapped[Decimal] = mapped_column(_px())


class BrokerSession(Base):
    __tablename__ = "broker_session"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32))
    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AppSetting(Base):
    __tablename__ = "app_setting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    trading_mode: Mapped[str] = mapped_column(String(16), default="ANALYSIS")
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_entry_armed: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_exit_armed: Mapped[bool] = mapped_column(Boolean, default=False)
    live_armed: Mapped[bool] = mapped_column(Boolean, default=False)
    chart_live_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_lots_per_trade: Mapped[int] = mapped_column(Integer, default=2)
    product: Mapped[str] = mapped_column(String(8), default="MIS")
    order_type: Mapped[str] = mapped_column(String(8), default="LIMIT")
    lots: Mapped[int] = mapped_column(Integer, default=1)
    market_protection_percent: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    slippage_points: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0.5"))
    brokerage_per_order: Mapped[Decimal] = mapped_column(_money(), default=Decimal("20"))
    statutory_charge_bps: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    strikes_above: Mapped[int] = mapped_column(Integer, default=10)
    strikes_below: Mapped[int] = mapped_column(Integer, default=10)
    chain_refresh_seconds: Mapped[int] = mapped_column(Integer, default=1)
    mis_cutoff: Mapped[time] = mapped_column(Time, default=time(15, 20))
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    risk_free_rate: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0.07"))
    # Window in which a repeat manual paper buy on the same contract and side is
    # treated as an accidental double submit.
    duplicate_window_seconds: Mapped[int] = mapped_column(Integer, default=60)


class Strategy(Base):
    __tablename__ = "strategy"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    versions: Mapped[list["StrategyVersion"]] = relationship(back_populates="strategy")


class StrategyVersion(Base):
    __tablename__ = "strategy_version"
    __table_args__ = (UniqueConstraint("strategy_id", "version_number", name="uq_strategy_version"),)

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategy.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(String(200), default="")
    strategy: Mapped[Strategy] = relationship(back_populates="versions")
    # One row per underlying. NIFTY and SENSEX are configured independently.
    parameters: Mapped[list["StrategyParameters"]] = relationship(
        back_populates="version", order_by="StrategyParameters.underlying"
    )
    exit_policy: Mapped["ExitPolicy"] = relationship(back_populates="version", uselist=False)
    risk_policy: Mapped["RiskPolicy"] = relationship(back_populates="version", uselist=False)
    option_rule: Mapped["OptionSelectionRule"] = relationship(back_populates="version", uselist=False)


class StrategyParameters(Base):
    __tablename__ = "strategy_parameters"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", "underlying", name="uq_params_version_underlying"),
    )

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_version.id"))
    underlying: Mapped[str] = mapped_column(String(16), default="NIFTY")
    direction: Mapped[str] = mapped_column(String(8), default="BULLISH")
    ema_period: Mapped[int] = mapped_column(Integer, default=20)
    ema_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    momentum_time: Mapped[time] = mapped_column(Time, default=time(9, 20))
    momentum_min_points: Mapped[Decimal] = mapped_column(_px(), default=Decimal("5"))
    momentum_min_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0.05"))
    momentum_unit: Mapped[str] = mapped_column(String(8), default="points")
    oi_time: Mapped[time] = mapped_column(Time, default=time(9, 20))
    oi_min_change: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("10000"))
    oi_unit: Mapped[str] = mapped_column(String(16), default="contracts")
    volume_ema_period: Mapped[int] = mapped_column(Integer, default=20)
    volume_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    vwap_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rsi_period: Mapped[int] = mapped_column(Integer, default=14)
    rsi_min: Mapped[Decimal] = mapped_column(_px(), default=Decimal("40"))
    rsi_max: Mapped[Decimal] = mapped_column(_px(), default=Decimal("70"))
    rsi_min_bearish: Mapped[Decimal] = mapped_column(_px(), default=Decimal("30"))
    rsi_max_bearish: Mapped[Decimal] = mapped_column(_px(), default=Decimal("60"))
    rsi_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    atr_period: Mapped[int] = mapped_column(Integer, default=14)
    atr_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    candle_bullish_required: Mapped[bool] = mapped_column(Boolean, default=True)
    time_start: Mapped[time] = mapped_column(Time, default=time(9, 30))
    time_end: Mapped[time] = mapped_column(Time, default=time(15, 0))
    time_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeframe_minutes: Mapped[int] = mapped_column(Integer, default=1)
    evaluate_on: Mapped[str] = mapped_column(String(16), default="candle_close")
    confirmation_mode: Mapped[str] = mapped_column(String(16), default="all")
    min_conditions: Mapped[int] = mapped_column(Integer, default=9)
    price_source: Mapped[str] = mapped_column(String(32), default="front_futures")
    oi_source: Mapped[str] = mapped_column(String(32), default="front_futures")
    bullish_option_type: Mapped[str] = mapped_column(String(4), default="CE")
    bearish_option_type: Mapped[str] = mapped_column(String(4), default="PE")
    option_momentum_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("3"))
    pe_midday_time: Mapped[time] = mapped_column(Time, default=time(11, 0))
    pe_oi_since_midday: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("10000"))
    candidate_strike_range: Mapped[int] = mapped_column(Integer, default=5)
    min_candidate_score_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("60"))
    version: Mapped[StrategyVersion] = relationship(back_populates="parameters")


class OptionSelectionRule(Base):
    __tablename__ = "option_selection_rule"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_version.id"), unique=True)
    method: Mapped[str] = mapped_column(String(32), default="atm")
    strike_offset: Mapped[int] = mapped_column(Integer, default=0)
    premium_min: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    premium_max: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    min_volume: Mapped[int] = mapped_column(BigInteger, default=0)
    min_oi: Mapped[int] = mapped_column(BigInteger, default=0)
    max_spread: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    min_liquidity: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    expiry_rule: Mapped[str] = mapped_column(String(32), default="nearest")
    version: Mapped[StrategyVersion] = relationship(back_populates="option_rule")


class ExitPolicy(Base):
    __tablename__ = "exit_policy"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_version.id"), unique=True)
    stop_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_style: Mapped[str] = mapped_column(String(16), default="points")
    stop_points: Mapped[Decimal] = mapped_column(_px(), default=Decimal("10"))
    stop_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("20"))
    stop_atr_mult: Mapped[Decimal] = mapped_column(_px(), default=Decimal("1"))
    target_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    target_style: Mapped[str] = mapped_column(String(16), default="rr")
    target_points: Mapped[Decimal] = mapped_column(_px(), default=Decimal("20"))
    target_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("40"))
    target_rr: Mapped[Decimal] = mapped_column(_px(), default=Decimal("2"))
    trail_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trail_style: Mapped[str] = mapped_column(String(16), default="points")
    trail_points: Mapped[Decimal] = mapped_column(_px(), default=Decimal("8"))
    trail_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("10"))
    trail_atr_mult: Mapped[Decimal] = mapped_column(_px(), default=Decimal("1"))
    trail_uses_live_atr: Mapped[bool] = mapped_column(Boolean, default=False)
    trail_arm_fraction: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0.5"))
    premium_trend_exit: Mapped[bool] = mapped_column(Boolean, default=True)
    reversal_ema: Mapped[bool] = mapped_column(Boolean, default=True)
    reversal_vwap: Mapped[bool] = mapped_column(Boolean, default=True)
    reversal_rsi: Mapped[bool] = mapped_column(Boolean, default=False)
    time_exit_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    time_exit: Mapped[time] = mapped_column(Time, default=time(15, 15))
    max_hold_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_hold_minutes: Mapped[int] = mapped_column(Integer, default=60)
    priority: Mapped[str] = mapped_column(
        String(200),
        default="EMERGENCY,STOP_LOSS,STRATEGY_INVALIDATED,TARGET,TRAILING_STOP,MAX_HOLD,TIME_EXIT",
    )
    version: Mapped[StrategyVersion] = relationship(back_populates="exit_policy")


class RiskPolicy(Base):
    __tablename__ = "risk_policy"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_version.id"), unique=True)
    max_capital: Mapped[Decimal] = mapped_column(_money(), default=Decimal("100000"))
    max_quantity: Mapped[int] = mapped_column(Integer, default=5000)
    max_daily_loss: Mapped[Decimal] = mapped_column(_money(), default=Decimal("5000"))
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=3)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=2)
    max_loss_per_position: Mapped[Decimal] = mapped_column(_money(), default=Decimal("2000"))
    max_exposure: Mapped[Decimal] = mapped_column(_money(), default=Decimal("200000"))
    max_spread: Mapped[Decimal] = mapped_column(_px(), default=Decimal("2"))
    min_option_volume: Mapped[int] = mapped_column(BigInteger, default=0)
    min_option_oi: Mapped[int] = mapped_column(BigInteger, default=0)
    entry_start: Mapped[time] = mapped_column(Time, default=time(9, 30))
    entry_stop: Mapped[time] = mapped_column(Time, default=time(15, 0))
    forced_square_off: Mapped[time] = mapped_column(Time, default=time(15, 15))
    max_trades_per_underlying: Mapped[int] = mapped_column(Integer, default=2)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    version: Mapped[StrategyVersion] = relationship(back_populates="risk_policy")


class Signal(Base):
    __tablename__ = "signal"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_version.id"))
    underlying: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    direction: Mapped[str] = mapped_column(String(16))
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    actionable: Mapped[bool] = mapped_column(Boolean, default=False)
    close_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    ema_value: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    momentum_value: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    oi_change: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    volume_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    volume_ema: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    vwap_value: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    rsi_value: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    atr_value: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    failed_reasons: Mapped[str] = mapped_column(String(500), default="")
    conditions: Mapped[list["SignalCondition"]] = relationship(back_populates="signal")


class SignalCondition(Base):
    __tablename__ = "signal_condition"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signal.id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    current_value: Mapped[str] = mapped_column(String(64))
    required_value: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(8))
    signal: Mapped[Signal] = relationship(back_populates="conditions")


class OrderRecord(Base):
    __tablename__ = "order_record"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signal.id"), nullable=True)
    position_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(8))
    exchange: Mapped[str] = mapped_column(String(8))
    tradingsymbol: Mapped[str] = mapped_column(String(64))
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String(8))
    price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    broker_order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tag: Mapped[str] = mapped_column(String(20))
    rejection_reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    fills: Mapped[list["Fill"]] = relationship(back_populates="order")


class Fill(Base):
    __tablename__ = "fill"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_record.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(_px())
    filled_at: Mapped[datetime] = mapped_column(DateTime)
    order: Mapped[OrderRecord] = relationship(back_populates="fills")


class Position(Base):
    __tablename__ = "position"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    user_label: Mapped[str] = mapped_column(String(32), default="local")
    underlying: Mapped[str] = mapped_column(String(16))
    exchange: Mapped[str] = mapped_column(String(8))
    tradingsymbol: Mapped[str] = mapped_column(String(64))
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    option_type: Mapped[str] = mapped_column(String(4))
    strike: Mapped[Decimal] = mapped_column(_px())
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    entry_price: Mapped[Decimal] = mapped_column(_px())
    quantity: Mapped[int] = mapped_column(Integer)
    stop_loss: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    target: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    trail_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    atr_at_entry: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(_money(), nullable=True)
    charges: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_version.id"))
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signal.id"), nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    mfe_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)


class PositionEvent(Base):
    __tablename__ = "position_event"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("position.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    event_type: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(64))
    price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_version.id"), nullable=True)
    underlying: Mapped[str] = mapped_column(String(16), default="")
    instrument: Mapped[str] = mapped_column(String(64), default="")
    user_label: Mapped[str] = mapped_column(String(32), default="local")
    input_close: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    input_rsi: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    input_atr: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    input_oi_change: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    result: Mapped[str] = mapped_column(String(32), default="")
    reason: Mapped[str] = mapped_column(String(500), default="")


class OiScannerConfig(Base):
    """Separate row per underlying. SENSEX open interest is far smaller than
    NIFTY, so the thresholds must not be shared."""

    __tablename__ = "oi_scanner_config"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    underlying_id: Mapped[int] = mapped_column(ForeignKey("underlying_profile.id"), unique=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=5)
    reference_time: Mapped[time] = mapped_column(Time, default=time(10, 0))
    strikes_above: Mapped[int] = mapped_column(Integer, default=10)
    strikes_below: Mapped[int] = mapped_column(Integer, default=10)
    option_type: Mapped[str] = mapped_column(String(4), default="ALL")
    expiry_mode: Mapped[str] = mapped_column(String(16), default="nearest")
    min_oi_change_interval: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("10000"))
    min_oi_change_reference: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("10000"))
    direction: Mapped[str] = mapped_column(String(24), default="ALL")
    refresh_seconds: Mapped[int] = mapped_column(Integer, default=5)
    snapshot_tolerance_minutes: Mapped[int] = mapped_column(Integer, default=3)


class StrikeSelectionConfig(Base):
    """Separate row per underlying. NIFTY settings never apply to SENSEX."""

    __tablename__ = "strike_selection_config"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    underlying_id: Mapped[int] = mapped_column(ForeignKey("underlying_profile.id"), unique=True)
    strikes_each_side: Mapped[int] = mapped_column(Integer, default=2)
    min_volume: Mapped[int] = mapped_column(BigInteger, default=1000)
    min_oi: Mapped[int] = mapped_column(BigInteger, default=5000)
    max_spread_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("2"))
    max_quote_age_seconds: Mapped[int] = mapped_column(Integer, default=10)
    min_premium: Mapped[Decimal] = mapped_column(_px(), default=Decimal("20"))
    max_premium: Mapped[Decimal] = mapped_column(_px(), default=Decimal("400"))
    min_reward_risk: Mapped[Decimal] = mapped_column(_px(), default=Decimal("1.5"))
    min_score: Mapped[Decimal] = mapped_column(_px(), default=Decimal("50"))
    preferred_delta_min: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0.35"))
    preferred_delta_max: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0.60"))
    acceptable_delta_min: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0.25"))
    acceptable_delta_max: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0.70"))
    delta_missing_action: Mapped[str] = mapped_column(String(8), default="warn")
    iv_high_ratio: Mapped[Decimal] = mapped_column(_px(), default=Decimal("1.15"))
    iv_high_action: Mapped[str] = mapped_column(String(8), default="warn")
    atr_multiplier: Mapped[Decimal] = mapped_column(_px(), default=Decimal("1"))
    stop_method: Mapped[str] = mapped_column(String(24), default="option_percent")
    stop_option_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("20"))
    stop_atr_multiplier: Mapped[Decimal] = mapped_column(_px(), default=Decimal("1"))
    target_reward_risk: Mapped[Decimal] = mapped_column(_px(), default=Decimal("2"))
    max_holding_minutes: Mapped[int] = mapped_column(Integer, default=60)
    near_expiry_days: Mapped[int] = mapped_column(Integer, default=2)
    far_expiry_days: Mapped[int] = mapped_column(Integer, default=21)
    account_capital: Mapped[Decimal] = mapped_column(_money(), default=Decimal("100000"))
    max_risk_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("1"))
    max_lots: Mapped[int] = mapped_column(Integer, default=5)
    max_capital_allocation: Mapped[Decimal] = mapped_column(_money(), default=Decimal("50000"))
    max_daily_loss: Mapped[Decimal] = mapped_column(_money(), default=Decimal("5000"))
    weight_liquidity: Mapped[Decimal] = mapped_column(_px(), default=Decimal("20"))
    weight_spread: Mapped[Decimal] = mapped_column(_px(), default=Decimal("15"))
    weight_delta: Mapped[Decimal] = mapped_column(_px(), default=Decimal("15"))
    weight_reward_risk: Mapped[Decimal] = mapped_column(_px(), default=Decimal("20"))
    weight_volume_oi: Mapped[Decimal] = mapped_column(_px(), default=Decimal("10"))
    weight_iv: Mapped[Decimal] = mapped_column(_px(), default=Decimal("5"))
    weight_premium: Mapped[Decimal] = mapped_column(_px(), default=Decimal("5"))
    weight_expiry: Mapped[Decimal] = mapped_column(_px(), default=Decimal("5"))
    weight_atm_distance: Mapped[Decimal] = mapped_column(_px(), default=Decimal("5"))


class StrikeSelectionRun(Base):
    __tablename__ = "strike_selection_run"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    underlying: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(16))
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signal.id"), nullable=True)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_version.id"), nullable=True)
    spot: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    futures_ltp: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    atm: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    strike_interval: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    atr: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    expected_move: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    conditions_passed: Mapped[int] = mapped_column(Integer, default=0)
    conditions_failed: Mapped[int] = mapped_column(Integer, default=0)
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    selected_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_score: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, default=0)
    no_trade_reasons: Mapped[str] = mapped_column(String(1000), default="")
    candidates: Mapped[list["StrikeSelectionCandidate"]] = relationship(back_populates="run")


class StrikeSelectionCandidate(Base):
    __tablename__ = "strike_selection_candidate"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strike_selection_run.id"))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    tradingsymbol: Mapped[str] = mapped_column(String(64))
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    strike: Mapped[Decimal] = mapped_column(_px())
    option_type: Mapped[str] = mapped_column(String(4))
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    moneyness: Mapped[str] = mapped_column(String(8), default="")
    strikes_from_atm: Mapped[int] = mapped_column(Integer, default=0)
    ltp: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    bid: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    ask: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    spread_percent: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    oi_change: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    iv: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    iv_band: Mapped[str] = mapped_column(String(8), default="")
    delta: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    gamma: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    theta_per_day: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    vega_per_point: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    days_to_expiry: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    target: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    reward_risk: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    allowed_lots: Mapped[int] = mapped_column(Integer, default=0)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    risk_amount: Mapped[Decimal | None] = mapped_column(_money(), nullable=True)
    score: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(8), default="PASS")
    reject_reasons: Mapped[str] = mapped_column(String(1000), default="")
    oi_interpretation: Mapped[str] = mapped_column(String(120), default="")
    run: Mapped[StrikeSelectionRun] = relationship(back_populates="candidates")


class PaperTradeProposal(Base):
    """Recorded from the selection screen. This module never places live orders."""

    __tablename__ = "paper_trade_proposal"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("strike_selection_run.id"), nullable=True)
    underlying: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(16))
    tradingsymbol: Mapped[str] = mapped_column(String(64))
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    option_type: Mapped[str] = mapped_column(String(4))
    strike: Mapped[Decimal] = mapped_column(_px())
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry: Mapped[Decimal] = mapped_column(_px())
    stop_loss: Mapped[Decimal] = mapped_column(_px())
    target: Mapped[Decimal] = mapped_column(_px())
    quantity: Mapped[int] = mapped_column(Integer)
    lots: Mapped[int] = mapped_column(Integer)
    risk_amount: Mapped[Decimal | None] = mapped_column(_money(), nullable=True)
    reward_risk: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    score: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    selection_reasons: Mapped[str] = mapped_column(String(1000), default="")
    warnings: Mapped[str] = mapped_column(String(1000), default="")
    position_id: Mapped[int | None] = mapped_column(ForeignKey("position.id"), nullable=True)
    # Which screen recorded it, and what the strategy said at that moment. Kept
    # so a trade taken without a confirmed signal stays identifiable afterwards.
    source: Mapped[str] = mapped_column(String(32), default="selection")
    direction_state: Mapped[str] = mapped_column(String(24), default="")
    direction_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signal_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    order_type: Mapped[str] = mapped_column(String(8), default="MARKET")
    requested_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_run"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    underlying: Mapped[str] = mapped_column(String(16))
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_version.id"))
    date_from: Mapped[datetime] = mapped_column(DateTime)
    date_to: Mapped[datetime] = mapped_column(DateTime)
    timeframe: Mapped[str] = mapped_column(String(16))
    slippage_points: Mapped[Decimal] = mapped_column(_px())
    brokerage_per_order: Mapped[Decimal] = mapped_column(_money())
    statutory_charge_bps: Mapped[Decimal] = mapped_column(_px())
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    gross_pnl: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    net_pnl: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    max_drawdown: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    average_win: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    average_loss: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    win_rate: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    profit_factor: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    average_holding_minutes: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    option_pnl_available: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(400), default="")


class BacktestTrade(Base):
    __tablename__ = "backtest_trade"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    backtest_run_id: Mapped[int] = mapped_column(ForeignKey("backtest_run.id"))
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    exit_time: Mapped[datetime] = mapped_column(DateTime)
    entry_price: Mapped[Decimal] = mapped_column(_px())
    exit_price: Mapped[Decimal] = mapped_column(_px())
    quantity: Mapped[int] = mapped_column(Integer)
    gross_pnl: Mapped[Decimal] = mapped_column(_money())
    net_pnl: Mapped[Decimal] = mapped_column(_money())
    exit_reason: Mapped[str] = mapped_column(String(32))
    holding_minutes: Mapped[Decimal] = mapped_column(_px())


class PaperAutoSetting(Base):
    __tablename__ = "paper_auto_setting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    min_signal_score: Mapped[Decimal] = mapped_column(_px(), default=Decimal("75"))
    candidate_strike_range: Mapped[int] = mapped_column(Integer, default=5)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=1)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=3)
    max_daily_loss: Mapped[Decimal] = mapped_column(_money(), default=Decimal("5000"))
    max_loss_per_trade: Mapped[Decimal] = mapped_column(_money(), default=Decimal("2000"))
    square_off: Mapped[time] = mapped_column(Time, default=time(15, 20))
    allow_reentry: Mapped[bool] = mapped_column(Boolean, default=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=15)
    entry_price_source: Mapped[str] = mapped_column(String(8), default="LTP")
    lots: Mapped[int] = mapped_column(Integer, default=1)
    ce_strategy_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    pe_strategy_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paper_instrument: Mapped[str] = mapped_column(String(16), default="OPTIONS")
    paper_mode: Mapped[str] = mapped_column(String(24), default="MANUAL")
    auto_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    index_strategy_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChargeRateRow(Base):
    __tablename__ = "charge_rate"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    broker: Mapped[str] = mapped_column(String(32), default="Zerodha")
    segment: Mapped[str] = mapped_column(String(16), default="NFO")
    charge_type: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8), default="BOTH")
    rate_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    flat_amount: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    cap_amount: Mapped[Decimal | None] = mapped_column(_money(), nullable=True)
    taxable: Mapped[bool] = mapped_column(Boolean, default=False)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PaperTrade(Base):
    __tablename__ = "paper_trade"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_paper_trade_idemp"),)

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    signal_id: Mapped[str] = mapped_column(String(160))
    underlying: Mapped[str] = mapped_column(String(16))
    option_type: Mapped[str] = mapped_column(String(4))
    instrument_kind: Mapped[str] = mapped_column(String(16), default="OPTION")
    tradingsymbol: Mapped[str] = mapped_column(String(64))
    exchange: Mapped[str] = mapped_column(String(8))
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[Decimal] = mapped_column(_px())
    instrument_token: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[int] = mapped_column(Integer)
    lot_size: Mapped[int] = mapped_column(Integer)
    lots: Mapped[int] = mapped_column(Integer)
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    entry_price: Mapped[Decimal] = mapped_column(_px())
    entry_underlying_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    entry_option_ltp: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    entry_signal_score: Mapped[Decimal] = mapped_column(_px())
    entry_passed: Mapped[int] = mapped_column(Integer)
    entry_total: Mapped[int] = mapped_column(Integer)
    stop_loss: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    target: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    trail_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    mfe_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    exit_underlying_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gross_pnl: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    brokerage: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    stt: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    transaction_charges: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    sebi_charges: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    gst: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    stamp_duty: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    other_charges: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    total_charges: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    net_pnl: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    charges_estimated: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    conditions: Mapped[list["PaperTradeCondition"]] = relationship(back_populates="trade")
    events: Mapped[list["PaperTradeEvent"]] = relationship(back_populates="trade")


class PaperTradeCondition(Base):
    __tablename__ = "paper_trade_condition"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("paper_trade.id"))
    name: Mapped[str] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean)
    current_value: Mapped[str] = mapped_column(String(80), default="")
    required_value: Mapped[str] = mapped_column(String(80), default="")
    trade: Mapped[PaperTrade] = relationship(back_populates="conditions")


class PaperTradeEvent(Base):
    __tablename__ = "paper_trade_event"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("paper_trade.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    message: Mapped[str] = mapped_column(String(400))
    underlying: Mapped[str] = mapped_column(String(16), default="")
    trade: Mapped[PaperTrade | None] = relationship(back_populates="events")


class MarketHistoricalData(Base):
    """Index/option history for backtests. Not the live candle feed."""

    __tablename__ = "market_historical_data"
    __table_args__ = (
        UniqueConstraint(
            "instrument",
            "timeframe",
            "timestamp",
            "contract_kind",
            "option_type",
            "strike",
            "expiry",
            name="ux_mhd_instrument_ts_tf",
        ),
        Index("ix_mhd_lookup", "instrument", "timeframe", "timestamp"),
        Index("ix_mhd_date", "instrument", "timeframe", "trading_date"),
    )

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    instrument: Mapped[str] = mapped_column(String(20))
    exchange: Mapped[str | None] = mapped_column(String(10), nullable=True)
    timeframe: Mapped[str] = mapped_column(String(20))
    trading_date: Mapped[date] = mapped_column(Date)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    open_price: Mapped[Decimal] = mapped_column(_px())
    high_price: Mapped[Decimal] = mapped_column(_px())
    low_price: Mapped[Decimal] = mapped_column(_px())
    close_price: Mapped[Decimal] = mapped_column(_px())
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contract_kind: Mapped[str] = mapped_column(String(16), default="INDEX")
    option_type: Mapped[str] = mapped_column(String(4), default="")
    strike: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    expiry: Mapped[date] = mapped_column(Date, default=date(1900, 1, 1))
    tradingsymbol: Mapped[str] = mapped_column(String(64), default="")
    created_date: Mapped[datetime] = mapped_column(DateTime)


class HistoricalImportJob(Base):
    __tablename__ = "historical_import_job"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")
    instruments: Mapped[str] = mapped_column(String(40))
    timeframe: Mapped[str] = mapped_column(String(20), default="1Minute")
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    current_instrument: Mapped[str] = mapped_column(String(20), default="")
    resume_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, default=0)
    records_invalid: Mapped[int] = mapped_column(Integer, default=0)
    progress_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    last_error: Mapped[str] = mapped_column(String(400), default="")
    log_message: Mapped[str] = mapped_column(String(400), default="")
    option_data_note: Mapped[str] = mapped_column(String(400), default="")
    events: Mapped[list["HistoricalImportEvent"]] = relationship(back_populates="job")


class HistoricalImportEvent(Base):
    __tablename__ = "historical_import_event"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("historical_import_job.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    message: Mapped[str] = mapped_column(String(400))
    job: Mapped[HistoricalImportJob] = relationship(back_populates="events")


class HistoricalPaperRun(Base):
    __tablename__ = "historical_paper_run"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")
    instrument: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str] = mapped_column(String(20), default="1Minute")
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    strategy_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy_label: Mapped[str] = mapped_column(String(80), default="")
    strategy_snapshot: Mapped[str] = mapped_column(Text, default="")
    threshold: Mapped[Decimal] = mapped_column(_px(), default=Decimal("75"))
    initial_capital: Mapped[Decimal] = mapped_column(_money(), default=Decimal("100000"))
    strike_range: Mapped[int] = mapped_column(Integer, default=5)
    charge_note: Mapped[str] = mapped_column(String(200), default="")
    option_data_available: Mapped[bool] = mapped_column(Boolean, default=False)
    option_data_note: Mapped[str] = mapped_column(String(400), default="")
    data_incomplete: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str] = mapped_column(String(400), default="")
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    gross_pnl: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    total_charges: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    net_pnl: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    max_drawdown: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    profit_factor: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    threshold_analysis: Mapped[str] = mapped_column(Text, default="")
    performance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    same_candle_rule: Mapped[str | None] = mapped_column(String(400), nullable=True)
    trades: Mapped[list["HistoricalPaperTrade"]] = relationship(back_populates="run")
    condition_stats: Mapped[list["HistoricalConditionStat"]] = relationship(back_populates="run")


class HistoricalPaperTrade(Base):
    __tablename__ = "historical_paper_trade"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("historical_paper_run.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    instrument: Mapped[str] = mapped_column(String(20))
    option_type: Mapped[str] = mapped_column(String(4))
    strike: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[Decimal] = mapped_column(_px())
    exit_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    signal_percent: Mapped[Decimal] = mapped_column(_px())
    passed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    gross_pnl: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    total_charges: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    net_pnl: Mapped[Decimal] = mapped_column(_money(), default=Decimal("0"))
    exit_reason: Mapped[str] = mapped_column(String(40), default="")
    buy_score: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    sell_score: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    explanation: Mapped[str | None] = mapped_column(String(400), nullable=True)
    indicators_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slippage: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    brokerage: Mapped[Decimal | None] = mapped_column(_money(), nullable=True)
    strategy_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    run: Mapped[HistoricalPaperRun] = relationship(back_populates="trades")


class HistoricalConditionStat(Base):
    __tablename__ = "historical_condition_stat"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("historical_paper_run.id"), index=True)
    side: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(64))
    evaluated: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    unavailable: Mapped[int] = mapped_column(Integer, default=0)
    run: Mapped[HistoricalPaperRun] = relationship(back_populates="condition_stats")


class ChartSignalRow(Base):
    """Persisted hypothetical BUY/SELL markers. Never sends a broker order."""

    __tablename__ = "chart_signal"
    __table_args__ = (
        UniqueConstraint("instrument", "timestamp", "signal_type", "option_type", "source", name="uq_chart_signal"),
    )

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    instrument: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    signal_type: Mapped[str] = mapped_column(String(8))
    option_type: Mapped[str] = mapped_column(String(4))
    strike: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    option_ltp: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    underlying_ltp: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    strategy_score: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    passed_conditions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_conditions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    stop_loss_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_hypothetical: Mapped[bool] = mapped_column(Boolean, default=True)
    tradingsymbol: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_pnl: Mapped[Decimal | None] = mapped_column(_money(), nullable=True)
    total_charges: Mapped[Decimal | None] = mapped_column(_money(), nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(_money(), nullable=True)
    charges_estimated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="LIVE_PAPER")
    paper_trade_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AlertSetting(Base):
    """Alert-only notification engine. Never sends a broker order."""

    __tablename__ = "alert_setting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sound_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    browser_notify: Mapped[bool] = mapped_column(Boolean, default=False)
    nifty_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sensex_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ce_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    pe_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    min_confidence: Mapped[Decimal] = mapped_column(_px(), default=Decimal("70"))
    min_score_separation: Mapped[Decimal] = mapped_column(_px(), default=Decimal("8"))
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    max_spread_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("2"))
    min_option_volume: Mapped[int] = mapped_column(Integer, default=100)
    min_option_oi: Mapped[int] = mapped_column(Integer, default=500)
    min_rr: Mapped[Decimal] = mapped_column(_px(), default=Decimal("1.5"))
    sl_percent: Mapped[Decimal] = mapped_column(_px(), default=Decimal("20"))
    target1_rr: Mapped[Decimal] = mapped_column(_px(), default=Decimal("1.5"))
    target2_rr: Mapped[Decimal] = mapped_column(_px(), default=Decimal("2.5"))
    supertrend_atr_mult: Mapped[Decimal] = mapped_column(_px(), default=Decimal("3"))
    time_start: Mapped[time] = mapped_column(Time, default=time(9, 20))
    time_end: Mapped[time] = mapped_column(Time, default=time(15, 10))
    alert_only: Mapped[bool] = mapped_column(Boolean, default=True)


class AlertSignal(Base):
    __tablename__ = "alert_signal"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    instrument: Mapped[str] = mapped_column(String(16), index=True)
    option_type: Mapped[str] = mapped_column(String(4))
    strike: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    tradingsymbol: Mapped[str] = mapped_column(String(64), default="")
    signal: Mapped[str] = mapped_column(String(8), default="WAIT")
    status: Mapped[str] = mapped_column(String(24), default="NEW")
    confidence: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    ce_score: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    pe_score: Mapped[Decimal] = mapped_column(_px(), default=Decimal("0"))
    entry_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    entry_low: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    entry_high: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    target1: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    target2: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    risk_reward: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    last_premium: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    blocked_json: Mapped[str] = mapped_column(Text, default="[]")
    warning: Mapped[str] = mapped_column(String(200), default="")
    data_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_only: Mapped[bool] = mapped_column(Boolean, default=True)
    instrument_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AlertLog(Base):
    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    instrument: Mapped[str] = mapped_column(String(16), default="")
    option_type: Mapped[str] = mapped_column(String(4), default="")
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    result: Mapped[str] = mapped_column(String(32), default="")
    reason: Mapped[str] = mapped_column(String(500), default="")
    close_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    score: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    signal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class TradingOrderAudit(Base):
    """Internal record of a chart BUY. Never stores API secrets or access tokens."""

    __tablename__ = "trading_order_audit"

    id: Mapped[int] = mapped_column(_pk(), primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    user_id: Mapped[str] = mapped_column(String(32), default="local")
    tenant_id: Mapped[str] = mapped_column(String(32), default="local")
    broker: Mapped[str] = mapped_column(String(16), default="Zerodha")
    execution_mode: Mapped[str] = mapped_column(String(8), default="PAPER")
    instrument: Mapped[str] = mapped_column(String(16), default="")
    exchange: Mapped[str] = mapped_column(String(8), default="")
    tradingsymbol: Mapped[str] = mapped_column(String(64), default="")
    option_type: Mapped[str] = mapped_column(String(4), default="")
    strike: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    transaction_type: Mapped[str] = mapped_column(String(8), default="BUY")
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    lots: Mapped[int] = mapped_column(Integer, default=1)
    order_type: Mapped[str] = mapped_column(String(8), default="MARKET")
    product: Mapped[str] = mapped_column(String(8), default="MIS")
    requested_price: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    signal_direction: Mapped[str] = mapped_column(String(16), default="")
    signal_score: Mapped[Decimal | None] = mapped_column(_px(), nullable=True)
    signal_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    client_order_id: Mapped[str] = mapped_column(String(64), default="")
    broker_order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    broker_status: Mapped[str] = mapped_column(String(32), default="")
    reject_reason: Mapped[str] = mapped_column(String(400), default="")
    local_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

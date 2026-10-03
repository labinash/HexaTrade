from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import as_utc_naive, now_utc
from app.db.models import (
    AppSetting,
    ChargeRateRow,
    ExitPolicy,
    OptionSelectionRule,
    PaperAutoSetting,
    RiskPolicy,
    Strategy,
    StrategyParameters,
    StrategyVersion,
    UnderlyingProfile,
)

UNDERLYINGS = ("NIFTY", "SENSEX")


def params_for(version: StrategyVersion, symbol: str) -> StrategyParameters | None:
    """The parameter row for one underlying. Each underlying carries its own
    direction and thresholds, so callers must say which one they mean."""
    for row in version.parameters:
        if row.underlying == symbol:
            return row
    return None


def seed(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(UnderlyingProfile)) == 0:
        db.add_all(
            [
                UnderlyingProfile(
                    symbol="NIFTY",
                    spot_exchange="NSE",
                    spot_symbol="NIFTY 50",
                    derivatives_exchange="NFO",
                    instrument_name="NIFTY",
                    atr_threshold=Decimal("15"),
                    session_start=time(9, 15),
                    session_end=time(15, 30),
                ),
                UnderlyingProfile(
                    symbol="SENSEX",
                    spot_exchange="BSE",
                    spot_symbol="SENSEX",
                    derivatives_exchange="BFO",
                    instrument_name="SENSEX",
                    atr_threshold=None,
                    session_start=time(9, 15),
                    session_end=time(15, 30),
                ),
            ]
        )
    if db.get(AppSetting, 1) is None:
        db.add(
            AppSetting(
                id=1,
                trading_mode="ANALYSIS",
                kill_switch=False,
                auto_entry_armed=False,
                auto_exit_armed=False,
                live_armed=False,
            )
        )
    if db.scalar(select(func.count()).select_from(Strategy)) == 0:
        strategy = Strategy(name="Index futures bullish")
        db.add(strategy)
        db.flush()
        _add_version(db, strategy, 1, "Initial user-specified parameters")
    if db.get(PaperAutoSetting, 1) is None:
        db.add(PaperAutoSetting(id=1))
    from app.db.models import AlertSetting

    if db.get(AlertSetting, 1) is None:
        db.add(AlertSetting(id=1))
    if db.scalar(select(func.count()).select_from(ChargeRateRow)) == 0:
        start = date(2026, 4, 1)
        db.add_all(
            [
                ChargeRateRow(broker="Zerodha", segment="NFO", charge_type="BROKERAGE", side="BOTH", rate_percent=Decimal("2.5"), flat_amount=Decimal("0"), cap_amount=Decimal("20"), taxable=True, effective_from=start),
                ChargeRateRow(broker="Zerodha", segment="NFO", charge_type="STT", side="SELL", rate_percent=Decimal("0.15"), flat_amount=Decimal("0"), taxable=False, effective_from=start),
                ChargeRateRow(broker="Zerodha", segment="NFO", charge_type="EXCHANGE", side="BOTH", rate_percent=Decimal("0.05065"), flat_amount=Decimal("0"), taxable=True, effective_from=start),
                ChargeRateRow(broker="Zerodha", segment="NFO", charge_type="SEBI", side="BOTH", rate_percent=Decimal("0.0001"), flat_amount=Decimal("0"), taxable=True, effective_from=start),
                ChargeRateRow(broker="Zerodha", segment="NFO", charge_type="STAMP", side="BUY", rate_percent=Decimal("0.003"), flat_amount=Decimal("0"), taxable=False, effective_from=start),
                ChargeRateRow(broker="Zerodha", segment="NFO", charge_type="OTHER", side="BOTH", rate_percent=Decimal("0.0001"), flat_amount=Decimal("0"), taxable=True, effective_from=start),
            ]
        )
    db.commit()


def disarm(db: Session) -> None:
    setting = db.get(AppSetting, 1)
    if setting is None:
        return
    setting.auto_entry_armed = False
    setting.auto_exit_armed = False
    setting.live_armed = False
    if hasattr(setting, "chart_live_enabled"):
        setting.chart_live_enabled = False
    db.commit()


def _add_version(db: Session, strategy: Strategy, number: int, note: str) -> StrategyVersion:
    version = StrategyVersion(
        strategy_id=strategy.id,
        version_number=number,
        created_at=as_utc_naive(now_utc()),
        note=note,
    )
    db.add(version)
    db.flush()
    # Assigned through the relationships so callers see the children without a
    # lazy load. The session runs with autoflush off, so a lazy load here would
    # query before the inserts and come back empty.
    version.parameters = [
        StrategyParameters(strategy_version_id=version.id, underlying=symbol) for symbol in UNDERLYINGS
    ]
    version.exit_policy = ExitPolicy(strategy_version_id=version.id)
    version.risk_policy = RiskPolicy(strategy_version_id=version.id)
    version.option_rule = OptionSelectionRule(strategy_version_id=version.id)
    db.flush()
    return version


def latest_version(db: Session) -> StrategyVersion | None:
    return db.scalar(select(StrategyVersion).order_by(StrategyVersion.id.desc()))


def clone_version(db: Session, note: str) -> StrategyVersion:
    current = latest_version(db)
    strategy = db.scalar(select(Strategy))
    if current is None or strategy is None:
        raise RuntimeError("strategy is not seeded")
    number = (db.scalar(select(func.max(StrategyVersion.version_number))) or 0) + 1
    created = _add_version(db, strategy, number, note)
    for symbol in UNDERLYINGS:
        source = params_for(current, symbol)
        if source is not None:
            _copy_params(source, params_for(created, symbol))
    _copy_exit(current.exit_policy, created.exit_policy)
    _copy_risk(current.risk_policy, created.risk_policy)
    _copy_rule(current.option_rule, created.option_rule)
    return created


def _copy_columns(source, dest, skip: set[str]) -> None:
    for column in source.__table__.columns:
        if column.key in skip:
            continue
        setattr(dest, column.key, getattr(source, column.key))


def _copy_params(source: StrategyParameters, dest: StrategyParameters) -> None:
    _copy_columns(source, dest, {"id", "strategy_version_id", "underlying"})


def _copy_exit(source: ExitPolicy, dest: ExitPolicy) -> None:
    _copy_columns(source, dest, {"id", "strategy_version_id"})


def _copy_risk(source: RiskPolicy, dest: RiskPolicy) -> None:
    _copy_columns(source, dest, {"id", "strategy_version_id"})


def _copy_rule(source: OptionSelectionRule, dest: OptionSelectionRule) -> None:
    _copy_columns(source, dest, {"id", "strategy_version_id"})

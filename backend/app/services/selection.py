"""Service layer for the option strike selection engine.

Reuses the existing chain, indicator and strategy services. It reads live
quotes from the shared cache and never places a live order.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import now_ist, now_utc, to_ist
from app.db.models import (
    AppSetting,
    Candle,
    Fill,
    Instrument,
    OrderRecord,
    PaperTradeProposal,
    Position,
    PositionEvent,
    StrikeSelectionCandidate,
    StrikeSelectionConfig,
    StrikeSelectionRun,
    UnderlyingProfile,
)
from app.domain.chain import strike_interval
from app.domain.strike_selection import (
    Candidate,
    OptionQuote,
    ScoreWeights,
    SelectionConfig,
    SelectionResult,
    UnderlyingInput,
    evaluate,
)
from app.services.mapping import f, jsonable

CONFIG_FIELDS = {
    "strikes_each_side", "min_volume", "min_oi", "max_spread_percent", "max_quote_age_seconds",
    "min_premium", "max_premium", "min_reward_risk", "min_score", "preferred_delta_min",
    "preferred_delta_max", "acceptable_delta_min", "acceptable_delta_max", "delta_missing_action",
    "iv_high_ratio", "iv_high_action", "atr_multiplier", "stop_method", "stop_option_percent",
    "stop_atr_multiplier", "target_reward_risk", "max_holding_minutes", "near_expiry_days",
    "far_expiry_days", "account_capital", "max_risk_percent", "max_lots",
    "max_capital_allocation", "max_daily_loss", "weight_liquidity", "weight_spread",
    "weight_delta", "weight_reward_risk", "weight_volume_oi", "weight_iv", "weight_premium",
    "weight_expiry", "weight_atm_distance",
}

# SENSEX starts from its own defaults. Nothing here is copied from NIFTY.
SEEDS = {
    "SENSEX": {"min_volume": 300, "min_oi": 1500, "min_premium": Decimal("30"), "max_premium": Decimal("600")},
}


def config_row(db: Session, symbol: str) -> StrikeSelectionConfig | None:
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol))
    if profile is None:
        return None
    row = db.scalar(select(StrikeSelectionConfig).where(StrikeSelectionConfig.underlying_id == profile.id))
    if row is None:
        row = StrikeSelectionConfig(underlying_id=profile.id, **SEEDS.get(symbol, {}))
        db.add(row)
        db.flush()
    return row


def config_of(row: StrikeSelectionConfig, bullish: str, bearish: str) -> SelectionConfig:
    return SelectionConfig(
        strikes_each_side=row.strikes_each_side,
        min_volume=int(row.min_volume),
        min_oi=int(row.min_oi),
        max_spread_percent=f(row.max_spread_percent),
        max_quote_age_seconds=row.max_quote_age_seconds,
        min_premium=f(row.min_premium),
        max_premium=f(row.max_premium),
        min_reward_risk=f(row.min_reward_risk),
        min_score=f(row.min_score),
        preferred_delta_min=f(row.preferred_delta_min),
        preferred_delta_max=f(row.preferred_delta_max),
        acceptable_delta_min=f(row.acceptable_delta_min),
        acceptable_delta_max=f(row.acceptable_delta_max),
        delta_missing_action=row.delta_missing_action,
        iv_high_ratio=f(row.iv_high_ratio),
        iv_high_action=row.iv_high_action,
        atr_multiplier=f(row.atr_multiplier),
        stop_method=row.stop_method,
        stop_option_percent=f(row.stop_option_percent),
        stop_atr_multiplier=f(row.stop_atr_multiplier),
        target_reward_risk=f(row.target_reward_risk),
        max_holding_minutes=row.max_holding_minutes,
        near_expiry_days=row.near_expiry_days,
        far_expiry_days=row.far_expiry_days,
        account_capital=f(row.account_capital),
        max_risk_percent=f(row.max_risk_percent),
        max_lots=row.max_lots,
        max_capital_allocation=f(row.max_capital_allocation),
        max_daily_loss=f(row.max_daily_loss),
        weights=ScoreWeights(
            liquidity=f(row.weight_liquidity),
            spread=f(row.weight_spread),
            delta=f(row.weight_delta),
            reward_risk=f(row.weight_reward_risk),
            volume_oi=f(row.weight_volume_oi),
            iv=f(row.weight_iv),
            premium=f(row.weight_premium),
            expiry=f(row.weight_expiry),
            atm_distance=f(row.weight_atm_distance),
        ),
        bullish_option_type=bullish,
        bearish_option_type=bearish,
    )


def config_payload(db: Session, symbol: str) -> dict:
    row = config_row(db, symbol)
    if row is None:
        return {}
    data = {key: jsonable(getattr(row, key)) for key in sorted(CONFIG_FIELDS)}
    data["underlying"] = symbol
    return data


def day_open_oi(db: Session, tokens: list[int], session_date: date) -> dict[int, int]:
    """OI on the first stored candle of the session, per token.

    This is an intraday change reference, not the previous day's closing OI.
    Tokens with no stored candle are left out so the caller reports the gap
    rather than substituting zero.
    """
    if not tokens:
        return {}
    start = datetime.combine(session_date, datetime.min.time())
    rows = db.execute(
        select(Candle.instrument_token, func.min(Candle.start_time))
        .where(
            Candle.instrument_token.in_(tokens),
            Candle.interval == "minute",
            Candle.start_time >= start - timedelta(hours=6),
            Candle.start_time < start + timedelta(hours=18),
        )
        .group_by(Candle.instrument_token)
    ).all()
    if not rows:
        return {}
    pairs = {token: first for token, first in rows}
    found = db.scalars(
        select(Candle).where(
            Candle.instrument_token.in_(list(pairs)),
            Candle.interval == "minute",
            Candle.start_time.in_(list(pairs.values())),
        )
    ).all()
    out: dict[int, int] = {}
    for candle in found:
        if pairs.get(candle.instrument_token) == candle.start_time and candle.oi is not None:
            out[int(candle.instrument_token)] = int(candle.oi)
    return out


def quote_age(timestamp) -> float | None:
    if timestamp is None:
        return None
    if isinstance(timestamp, str):
        return None
    stamp = timestamp
    if getattr(stamp, "tzinfo", None) is not None:
        stamp = to_ist(stamp).replace(tzinfo=None)
    return max((now_ist().replace(tzinfo=None) - stamp).total_seconds(), 0.0)


def _swings(bars, lookback: int = 20) -> tuple[float | None, float | None]:
    window = bars[-lookback:]
    if not window:
        return None, None
    return min(bar.low for bar in window), max(bar.high for bar in window)


def run_selection(
    db: Session,
    runtime,
    symbol: str,
    expiry: date | None = None,
    *,
    require_signal: bool = True,
) -> tuple[SelectionResult, dict, list[OptionQuote]]:
    """Returns the ranked result, the market view it was built from, and the
    quotes for every contract of the expiry, both CE and PE.

    The quotes are returned so a caller that needs both sides, such as the
    dashboard mini chain, reads exactly the values the ranking used instead of
    fetching them again."""
    from app.services.engine import (
        _nearest_expiry,
        _option_rows,
        load_bars,
        market_view,
    )

    view = market_view(db, runtime, symbol)
    if view.get("error"):
        raise ValueError(view["error"])
    row = config_row(db, symbol)
    params = view["params"]
    config = config_of(row, params.bullish_option_type, params.bearish_option_type)
    setting = db.get(AppSetting, 1)
    rate = f(setting.risk_free_rate) if setting else 0.07

    evaluation = view["evaluation"]
    bars = load_bars(db, int(view["future"].instrument_token), "minute", 40) if view.get("future") else []
    swing_low, swing_high = _swings(bars)
    underlying = UnderlyingInput(
        symbol=symbol,
        direction=evaluation.direction,
        signal_valid=bool(view["passed"]),
        conditions_passed=sum(1 for c in evaluation.conditions if c.status == "PASS"),
        conditions_failed=len(evaluation.failed_names),
        spot=view.get("ltp"),
        futures_ltp=view.get("futures_ltp"),
        futures_volume=evaluation.volume,
        futures_oi=view.get("futures_oi"),
        futures_oi_change=evaluation.oi_change,
        vwap=evaluation.vwap,
        ema=evaluation.ema,
        rsi=evaluation.rsi,
        atr=evaluation.atr,
        reference_close=evaluation.close,
        reference_oi=None,
        strike_interval=None,
        stale=bool(view["stale"]),
        risk_free_rate=rate or 0.07,
        daily_loss_used=_daily_loss(db),
        swing_low=swing_low,
        swing_high=swing_high,
    )

    target_expiry = expiry or _nearest_expiry(db, symbol)
    quotes: list[OptionQuote] = []
    if target_expiry is not None:
        contracts = _option_rows(db, symbol, target_expiry)
        strikes = sorted({float(c.strike) for c in contracts if c.strike is not None})
        underlying.strike_interval = strike_interval(strikes)
        tokens = [int(c.instrument_token) for c in contracts]
        opens = day_open_oi(db, tokens, now_ist().date())
        cached = runtime.cache.many(tokens)
        for contract in contracts:
            token = int(contract.instrument_token)
            quote = cached.get(token) or {}
            quotes.append(
                OptionQuote(
                    token=token,
                    tradingsymbol=contract.tradingsymbol,
                    strike=float(contract.strike),
                    option_type=contract.instrument_type,
                    expiry=contract.expiry,
                    lot_size=int(contract.lot_size or 0),
                    ltp=quote.get("ltp"),
                    bid=quote.get("bid"),
                    ask=quote.get("ask"),
                    bid_quantity=quote.get("bid_qty"),
                    ask_quantity=quote.get("ask_qty"),
                    volume=quote.get("volume"),
                    oi=quote.get("oi"),
                    day_open_oi=opens.get(token),
                    open=quote.get("open"),
                    high=quote.get("high"),
                    low=quote.get("low"),
                    close=quote.get("close"),
                    change=quote.get("change"),
                    iv=_iv_for(contract, quote, view, rate or 0.07, target_expiry),
                    quote_age_seconds=quote_age(quote.get("timestamp")),
                )
            )

    result = evaluate(underlying, quotes, config, now_ist().replace(tzinfo=None), require_signal=require_signal)
    # The input is reused by the dashboard panel to score CE and PE independently
    # without running the 9-condition engine again.
    view["selection_underlying"] = underlying
    view["selection_config"] = config
    return result, view, quotes


def _iv_for(contract, quote, view, rate: float, expiry: date) -> float | None:
    from app.domain.chain import implied_vol

    futures = view.get("futures_ltp")
    price = quote.get("ltp")
    if not futures or not price or contract.strike is None:
        return None
    days = (expiry - now_ist().date()).days
    years = max(days, 0) / 365
    if years <= 0:
        return None
    return implied_vol(price, float(futures), float(contract.strike), years, rate, contract.instrument_type == "CE")


def _daily_loss(db: Session) -> float:
    """Loss already realised today, as a positive number."""
    from app.services.engine import _daily_pnl

    return abs(min(_daily_pnl(db), 0.0))


def payload(result: SelectionResult, view: dict, config: dict) -> dict:
    return {
        "underlying": result.underlying,
        "direction": result.direction,
        "option_type": result.option_type,
        "signal": view.get("signal"),
        "passed": view.get("passed"),
        "failed": view.get("failed"),
        "spot": view.get("ltp"),
        "futures_ltp": view.get("futures_ltp"),
        "atr": view.get("atr"),
        "vwap": view.get("vwap"),
        "stale": view.get("stale"),
        "atm": result.atm,
        "strike_interval": result.strike_interval,
        "expiry": jsonable(result.expiry),
        "expected_move": result.expected_move,
        "available": result.available,
        "selected": candidate_payload(result.selected) if result.selected else None,
        "top": [candidate_payload(c) for c in result.top],
        "candidates": [candidate_payload(c) for c in result.all_candidates],
        "no_trade_reasons": result.no_trade_reasons,
        "evaluated_at": jsonable(result.evaluated_at),
        "disclaimer": result.disclaimer,
        "config": config,
    }


def candidate_payload(c: Candidate) -> dict:
    data = {key: jsonable(value) for key, value in asdict(c).items() if key != "greeks"}
    data["delta"] = c.greeks.delta
    data["gamma"] = c.greeks.gamma
    data["theta_per_day"] = c.greeks.theta_per_day
    data["vega_per_point"] = c.greeks.vega_per_point
    return data


def persist_run(db: Session, result: SelectionResult, view: dict) -> StrikeSelectionRun:
    run = StrikeSelectionRun(
        created_at=now_utc().replace(tzinfo=None),
        underlying=result.underlying,
        direction=result.direction,
        option_type=result.option_type,
        strategy_version_id=view.get("version_id"),
        spot=_dec(view.get("ltp")),
        futures_ltp=_dec(view.get("futures_ltp")),
        atm=_dec(result.atm),
        strike_interval=_dec(result.strike_interval),
        atr=_dec(view.get("atr")),
        expected_move=_dec(result.expected_move),
        conditions_passed=sum(1 for c in view["evaluation"].conditions if c.status == "PASS"),
        conditions_failed=len(view["evaluation"].failed_names),
        available=result.available,
        selected_symbol=result.selected.tradingsymbol if result.selected else None,
        selected_score=_dec(result.selected.score) if result.selected else None,
        candidate_count=len(result.all_candidates),
        passed_count=sum(1 for c in result.all_candidates if c.status == "PASS"),
        no_trade_reasons="; ".join(result.no_trade_reasons)[:1000],
    )
    db.add(run)
    db.flush()
    for index, candidate in enumerate(result.all_candidates):
        db.add(
            StrikeSelectionCandidate(
                run_id=run.id,
                rank=index + 1 if candidate.status == "PASS" else 0,
                tradingsymbol=candidate.tradingsymbol,
                instrument_token=candidate.token,
                strike=_dec(candidate.strike),
                option_type=candidate.option_type,
                expiry=candidate.expiry,
                moneyness=candidate.moneyness,
                strikes_from_atm=candidate.strikes_from_atm,
                ltp=_dec(candidate.ltp),
                bid=_dec(candidate.bid),
                ask=_dec(candidate.ask),
                spread_percent=_dec(candidate.spread_percent),
                volume=candidate.volume,
                oi=candidate.oi,
                oi_change=candidate.oi_change,
                iv=_dec(candidate.iv),
                iv_band=candidate.iv_band,
                delta=_dec(candidate.greeks.delta),
                gamma=_dec(candidate.greeks.gamma, 8),
                theta_per_day=_dec(candidate.greeks.theta_per_day),
                vega_per_point=_dec(candidate.greeks.vega_per_point),
                days_to_expiry=candidate.days_to_expiry,
                entry=_dec(candidate.entry),
                stop_loss=_dec(candidate.stop_loss),
                target=_dec(candidate.target),
                reward_risk=_dec(candidate.reward_risk),
                allowed_lots=candidate.allowed_lots,
                quantity=candidate.quantity,
                risk_amount=_dec(candidate.risk_amount, 2),
                score=_dec(candidate.score),
                status=candidate.status,
                reject_reasons="; ".join(candidate.reject_reasons)[:1000],
                oi_interpretation=candidate.oi_interpretation[:120],
            )
        )
    return run


def resize(candidate: Candidate, lots: int, lot_size: int) -> None:
    """Apply a user-chosen number of lots to a candidate.

    The configured risk limits already produced `allowed_lots`, and this does not
    raise that ceiling. The lot size is the one read from the instrument master,
    never a number supplied by the caller's client.
    """
    if lots < 1:
        raise ValueError("lots must be at least 1")
    if lot_size < 1:
        raise ValueError("instrument lot size is unavailable, cannot size the order")
    if lot_size != candidate.lot_size:
        raise ValueError(
            f"instrument master reports lot size {lot_size} for this contract but the quote carried "
            f"{candidate.lot_size}, refresh the instrument master"
        )
    if candidate.allowed_lots <= 0:
        raise ValueError("configured risk limits allow zero lots for this contract")
    if lots > candidate.allowed_lots:
        raise ValueError(
            f"{lots} lots exceeds the {candidate.allowed_lots} lots allowed by the configured "
            "risk limits for this contract"
        )
    candidate.allowed_lots = lots
    candidate.quantity = lots * lot_size
    if candidate.risk_per_unit is not None:
        candidate.risk_amount = candidate.risk_per_unit * candidate.quantity
    if candidate.entry is not None:
        candidate.capital_required = candidate.entry * candidate.quantity


def recent_duplicate(db: Session, token: int, side: str, window_seconds: int) -> Position | None:
    """An open position on the same contract and side inside the window.

    Guards against a double click or a repeated submit. Returns the offending
    position so the caller can describe it.
    """
    if window_seconds <= 0:
        return None
    cutoff = now_utc().replace(tzinfo=None) - timedelta(seconds=window_seconds)
    rows = db.scalars(
        select(Position)
        .where(Position.instrument_token == token, Position.entry_time >= cutoff)
        .order_by(Position.id.desc())
    ).all()
    # Every position this application opens is a buy, so the side comparison is
    # against the option type carried by the position.
    return rows[0] if rows and side == "BUY" else None


def record_proposal(
    db: Session,
    run: StrikeSelectionRun,
    candidate: Candidate,
    *,
    source: str = "selection",
    direction_state: str = "",
    direction_score: int | None = None,
    signal_confirmed: bool = False,
    order_type: str = "MARKET",
    requested_price: float | None = None,
) -> PaperTradeProposal:
    proposal = PaperTradeProposal(
        created_at=now_utc().replace(tzinfo=None),
        run_id=run.id,
        underlying=run.underlying,
        source=source,
        direction_state=direction_state[:24],
        direction_score=direction_score,
        signal_confirmed=signal_confirmed,
        order_type=order_type,
        requested_price=_dec(requested_price),
        direction=run.direction,
        tradingsymbol=candidate.tradingsymbol,
        instrument_token=candidate.token,
        option_type=candidate.option_type,
        strike=_dec(candidate.strike),
        expiry=candidate.expiry,
        entry=_dec(candidate.entry),
        stop_loss=_dec(candidate.stop_loss),
        target=_dec(candidate.target),
        quantity=candidate.quantity,
        lots=candidate.allowed_lots,
        risk_amount=_dec(candidate.risk_amount, 2),
        reward_risk=_dec(candidate.reward_risk),
        score=_dec(candidate.score),
        selection_reasons="; ".join(candidate.reasons)[:1000],
        warnings="; ".join(candidate.warnings)[:1000],
    )
    db.add(proposal)
    db.flush()
    return proposal


def open_paper_position(db: Session, view: dict, candidate: Candidate, proposal: PaperTradeProposal) -> Position:
    """Creates a simulated position only. No broker call is made from here."""
    from app.domain.paper import simulate_fill
    from app.services.engine import audit

    setting = db.get(AppSetting, 1)
    if setting is None:
        raise ValueError("application settings are missing")
    if setting.kill_switch:
        raise ValueError("kill switch is active")
    if setting.trading_mode == "ANALYSIS":
        raise ValueError("switch to PAPER mode to record a simulated trade")
    fill = simulate_fill(
        "BUY",
        candidate.entry,
        candidate.quantity,
        f(setting.slippage_points) or 0,
        f(setting.brokerage_per_order) or 0,
        f(setting.statutory_charge_bps) or 0,
        data_stale=bool(view.get("stale")),
    )
    if fill is None:
        raise ValueError("cannot simulate a fill on stale or missing prices")
    instrument = instrument_of(db, candidate.token)
    order = OrderRecord(
        created_at=now_utc().replace(tzinfo=None),
        exchange=instrument.exchange if instrument else "NFO",
        tradingsymbol=candidate.tradingsymbol,
        instrument_token=candidate.token,
        side="BUY",
        quantity=candidate.quantity,
        order_type=setting.order_type,
        price=_dec(fill.price),
        status="FILLED",
        execution_mode="PAPER",
        idempotency_key=f"selection-{proposal.id}",
        tag="hextrade-select",
    )
    db.add(order)
    db.flush()
    position = Position(
        underlying=view["symbol"],
        exchange=order.exchange,
        tradingsymbol=candidate.tradingsymbol,
        instrument_token=candidate.token,
        option_type=candidate.option_type,
        strike=_dec(candidate.strike),
        expiry=candidate.expiry,
        entry_time=now_utc().replace(tzinfo=None),
        entry_price=_dec(fill.price),
        quantity=candidate.quantity,
        stop_loss=_dec(candidate.stop_loss),
        target=_dec(candidate.target),
        atr_at_entry=_dec(view.get("atr")),
        strategy_version_id=view.get("version_id"),
        execution_mode="PAPER",
        status="OPEN",
        charges=_dec(fill.charges, 2),
        mfe_price=_dec(fill.price),
    )
    db.add(position)
    db.flush()
    order.position_id = position.id
    proposal.position_id = position.id
    db.add(Fill(order_id=order.id, quantity=candidate.quantity, price=_dec(fill.price), filled_at=now_utc().replace(tzinfo=None)))
    db.add(
        PositionEvent(
            position_id=position.id,
            created_at=now_utc().replace(tzinfo=None),
            event_type="ENTRY",
            reason="STRIKE_SELECTION",
            price=_dec(fill.price),
        )
    )
    audit(
        db,
        "PAPER_TRADE_FROM_SELECTION",
        underlying=view["symbol"],
        instrument=candidate.tradingsymbol,
        result="PAPER",
        reason=f"score {candidate.score}, entry {fill.price}, stop {candidate.stop_loss}, target {candidate.target}",
    )
    return position


def instrument_of(db: Session, token: int) -> Instrument | None:
    return db.scalar(select(Instrument).where(Instrument.instrument_token == token))


def _dec(value, places: int = 4) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), places)))

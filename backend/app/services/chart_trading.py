"""Trading controller for chart CE/PE buys.

Zerodha calls stay here. React never talks to Kite Connect.
Instrument identity (symbol, strike, lot size, quantity) is resolved from the
instrument master and the existing ATM ladder. Browser values are hints only.
"""

from __future__ import annotations

import logging
import time as pytime
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.kite.auth import current_session, kite_client, load_access_token, session_status
from app.core.clock import as_utc_naive, now_ist, now_utc
from app.core.config import get_settings
from app.db.bootstrap import latest_version
from app.db.models import AppSetting, Fill, Instrument, OrderRecord, Position, PositionEvent, TradingOrderAudit, UnderlyingProfile
from app.domain.chart_trading import (
    ALLOWED_UNDERLYINGS,
    DEFAULT_MARKET_PROTECTION_PERCENT,
    LIVE_ENABLE_PHRASE,
    LIVE_ORDER_PHRASE,
    POSITION_KIND,
    STATUS_BLOCKED,
    STATUS_CANCELLED,
    STATUS_COMPLETE,
    STATUS_SUBMITTED,
    assert_moneyness,
    assert_option_type,
    assert_order_type,
    assert_product,
    assert_side,
    assert_underlying,
    chart_order_tag,
    current_value,
    display_status,
    invested_value,
    live_badge,
    live_unrealized_pnl,
    market_protection_percent,
    parse_option_type,
    quantity_from_lots,
    recommended_action,
    recommended_option,
    validate_hint,
)
from app.domain.execution import LiveBroker, OrderRefused, OrderRequest
from app.domain.paper import simulate_fill
from app.domain.risk import RiskContext, evaluate_risk
from app.services.mapping import jsonable
from app.services.option_panel import instrument_lot_size, panel as option_panel

log = logging.getLogger("hextrade.chart_trading")

SNAPSHOT_TTL = 8.0


def settings_payload(db: Session, runtime) -> dict:
    setting = db.get(AppSetting, 1)
    version = latest_version(db)
    risk = version.risk_policy if version else None
    enabled = bool(setting and setting.chart_live_enabled)
    protection = None
    if setting and setting.market_protection_percent is not None:
        protection = float(setting.market_protection_percent)
    return {
        "chart_live_enabled": enabled,
        "live_armed": bool(getattr(runtime, "live_latch", False)),
        "live_session_ack": bool(getattr(runtime, "chart_live_session_ack", False)),
        "badge": live_badge(enabled),
        "kill_switch": bool(setting.kill_switch) if setting else False,
        "trading_mode": setting.trading_mode if setting else "ANALYSIS",
        "broker": session_status(current_session(db)),
        "product": setting.product if setting else "MIS",
        "order_type": "MARKET",
        "lots": setting.lots if setting else 1,
        "max_lots_per_trade": int(setting.max_lots_per_trade) if setting and setting.max_lots_per_trade else 2,
        "max_quantity": int(risk.max_quantity) if risk else None,
        "max_daily_loss": float(risk.max_daily_loss) if risk else None,
        "max_trades_per_day": int(risk.max_trades_per_day) if risk else None,
        "max_open_positions": int(risk.max_open_positions) if risk else None,
        "entry_start": risk.entry_start.isoformat(timespec="minutes") if risk else None,
        "entry_stop": risk.entry_stop.isoformat(timespec="minutes") if risk else None,
        "market_protection_percent": protection,
        "market_protection_default": DEFAULT_MARKET_PROTECTION_PERCENT,
        "allowed_instruments": list(ALLOWED_UNDERLYINGS),
        "position_kind": POSITION_KIND,
        "live_enable_phrase": LIVE_ENABLE_PHRASE,
        "live_order_phrase": LIVE_ORDER_PHRASE,
        "note": "Enable Live Trading is OFF after every API restart. BUY CE/PE is paper until it is ON.",
    }


def set_live_enabled(db: Session, runtime, *, enabled: bool, confirmation: str = "") -> dict:
    setting = db.get(AppSetting, 1)
    if setting is None:
        raise OrderRefused("application settings are missing")
    if enabled:
        if confirmation != LIVE_ENABLE_PHRASE:
            raise OrderRefused(f'Type {LIVE_ENABLE_PHRASE} to enable live Zerodha orders.')
        if session_status(current_session(db)) not in {"connected", "expiring"}:
            raise OrderRefused("Connect Zerodha before enabling live trading.")
        if setting.kill_switch:
            raise OrderRefused("kill switch is on")
        if setting.market_protection_percent is None or float(setting.market_protection_percent) <= 0:
            setting.market_protection_percent = Decimal(str(DEFAULT_MARKET_PROTECTION_PERCENT))
        setting.chart_live_enabled = True
        setting.trading_mode = "LIVE"
        setting.live_armed = True
        runtime.live_latch = True
        runtime.chart_live_session_ack = False
    else:
        setting.chart_live_enabled = False
        setting.live_armed = False
        runtime.live_latch = False
        runtime.chart_live_session_ack = False
    db.commit()
    return settings_payload(db, runtime)


def chart_strikes(db: Session, runtime, symbol: str) -> dict:
    """ITM/ATM/OTM quotes plus the existing dashboard signal. No second engine."""
    name = assert_underlying(symbol)
    board = option_panel(db, runtime, name)
    trend = board.get("direction")
    preferred = recommended_option(trend) or board.get("preferred_option")
    rungs = []
    side = preferred or "CE"
    ladder = (board.get("ladder") or {}).get(side) or {}
    options = board.get("options") or {}
    live_rows = []
    try:
        live_rows = _cached_net_positions(db, runtime)
    except Exception:
        log.exception("chart strike live overlay failed")
        live_rows = []
    for label in ("ITM", "ATM", "OTM"):
        strike = ladder.get(label)
        ce = (options.get("CE") or {}).get(label) if side == "CE" else _card_for_strike(options.get("CE") or {}, strike)
        pe = (options.get("PE") or {}).get(label) if side == "PE" else _card_for_strike(options.get("PE") or {}, strike)
        if side == "CE":
            pe = _card_for_strike(options.get("PE") or {}, strike)
        else:
            ce = _card_for_strike(options.get("CE") or {}, strike)
        rungs.append(
            {
                "label": label,
                "strike": strike,
                "ce": _public_contract(ce, live_rows),
                "pe": _public_contract(pe, live_rows),
            }
        )
    setting = settings_payload(db, runtime)
    return {
        **{k: board.get(k) for k in (
            "underlying", "underlying_ltp", "futures_ltp", "atm_strike", "strike_interval",
            "expiry", "lot_size", "direction", "condition_count", "condition_total",
            "signal_confirmed", "preferred_option", "market_closed", "stale", "can_trade",
            "blocked_reason", "timestamp",
        )},
        "trend": trend,
        "recommended_side": preferred,
        "recommended_action": recommended_action(trend, board.get("signal_confirmed")),
        "signal": board.get("signal"),
        "rungs": rungs,
        "trading": setting,
        "note": (
            "Direction and 9-condition scores come from the existing dashboard engine. "
            "BUY CE/PE here is paper unless Enable Live Trading is ON. No broker order from a signal alone."
        ),
    }


def preview_order(db: Session, runtime, body: dict) -> dict:
    contract = resolve_contract(db, runtime, body)
    setting = settings_payload(db, runtime)
    live = bool(setting["chart_live_enabled"])
    return {
        "mode": "LIVE" if live else "PAPER",
        "badge": "LIVE ZERODHA" if live else "PAPER",
        "live_session_ack_required": live and not setting["live_session_ack"],
        "signal": contract["signal"],
        "trend": contract["trend"],
        "recommended_side": contract["recommended_side"],
        "instrument": contract["tradingsymbol"],
        "exchange": contract["exchange"],
        "ltp": contract["ltp"],
        "strike": contract["strike"],
        "option_type": contract["option_type"],
        "moneyness": contract["moneyness"],
        "lots": contract["lots"],
        "quantity": contract["quantity"],
        "lot_size": contract["lot_size"],
        "order_type": contract["order_type"],
        "product": contract["product"],
        "estimated_value": contract["estimated_value"],
        "market_protection_percent": setting["market_protection_percent"],
        "note": "Real BUY in the connected Zerodha account." if live else "PAPER TRADE — nothing is sent to Zerodha.",
    }


def place_order(db: Session, runtime, body: dict) -> dict:
    contract = resolve_contract(db, runtime, body)
    setting = db.get(AppSetting, 1)
    live = bool(setting and setting.chart_live_enabled)
    client_id = str(body.get("clientOrderId") or body.get("client_order_id") or "").strip()
    if not client_id:
        raise OrderRefused("clientOrderId is required so a retry cannot place a second order.")
    existing = db.scalar(select(OrderRecord).where(OrderRecord.idempotency_key == client_id))
    if existing is not None:
        payload = _order_payload(existing, replayed=True, live=existing.execution_mode == "LIVE")
        _record_trade_audit(db, contract, client_id, existing, live=existing.execution_mode == "LIVE", extra=body)
        return payload

    if live:
        return _place_live(db, runtime, setting, contract, body, client_id)
    return _place_paper(db, runtime, setting, contract, client_id)


def resolve_contract(db: Session, runtime, body: dict) -> dict:
    name = assert_underlying(body.get("underlying") or body.get("instrument") or "")
    kind = assert_option_type(body.get("optionType") or body.get("option_type") or "")
    moneyness = assert_moneyness(body.get("moneyness") or "ATM")
    side = assert_side(body.get("transactionType") or body.get("side") or "BUY")
    order_type = assert_order_type(body.get("orderType") or body.get("order_type") or "MARKET")
    setting = db.get(AppSetting, 1)
    product = assert_product(body.get("product") or (setting.product if setting else "MIS"))
    lots = int(body.get("lots") or 1)
    board = option_panel(db, runtime, name)
    card = ((board.get("options") or {}).get(kind) or {}).get(moneyness)
    if not card or not card.get("instrument_token"):
        raise OrderRefused(f"{moneyness} {kind} is not available on the current ATM ladder.")
    token = int(card["instrument_token"])
    lot_size = instrument_lot_size(db, token)
    if lot_size is None:
        lot_size = int(card.get("lot_size") or 0)
    quantity = quantity_from_lots(lots, lot_size or 0)
    max_lots = int(setting.max_lots_per_trade) if setting and setting.max_lots_per_trade else 2
    if lots > max_lots:
        raise OrderRefused(f"LIVE ORDER BLOCKED. Maximum lots per trade is {max_lots}.")
    instrument = db.scalar(select(Instrument).where(Instrument.instrument_token == token).order_by(Instrument.id.desc()))
    if instrument is None:
        raise OrderRefused("Contract is not in the instrument master. Refresh instruments.")
    resolved = {
        "underlying": name,
        "option_type": kind,
        "moneyness": moneyness,
        "side": side,
        "order_type": order_type,
        "product": product or (setting.product if setting else "MIS"),
        "lots": lots,
        "lot_size": lot_size,
        "quantity": quantity,
        "token": token,
        "tradingsymbol": instrument.tradingsymbol,
        "exchange": instrument.exchange,
        "strike": float(instrument.strike) if instrument.strike is not None else float(card.get("strike") or 0),
        "expiry": instrument.expiry,
        "ltp": card.get("ltp"),
        "bid": card.get("bid"),
        "ask": card.get("ask"),
        "oi": card.get("oi"),
        "volume": card.get("volume"),
        "trend": board.get("direction"),
        "recommended_side": recommended_option(board.get("direction")),
        "signal": board.get("signal"),
        "signal_confirmed": board.get("signal_confirmed"),
        "stale": board.get("stale"),
        "market_closed": board.get("market_closed"),
        "estimated_value": None if card.get("ltp") is None else round(float(card["ltp"]) * quantity, 2),
        "board": board,
        "instrument": instrument,
        "spread": None if card.get("bid") is None or card.get("ask") is None else float(card["ask"]) - float(card["bid"]),
    }
    validate_hint(
        expected=resolved,
        strike=body.get("strike"),
        tradingsymbol=body.get("tradingsymbol"),
        quantity=body.get("quantity"),
        option_type=body.get("optionType") or body.get("option_type"),
    )
    return resolved


def sync_broker(db: Session, runtime, *, force: bool = True) -> dict:
    snapshot = _load_snapshot(db, runtime, force=force)
    return snapshot


def live_markers(db: Session, runtime, symbol: str, last_unix: int | None) -> list[dict]:
    name = str(symbol or "").upper()
    if name not in ALLOWED_UNDERLYINGS or not last_unix:
        return []
    try:
        rows = _cached_net_positions(db, runtime)
    except Exception:
        return []
    out = []
    for row in rows:
        if row.get("underlying") != name:
            continue
        qty = int(row.get("quantity") or 0)
        closed = qty == 0
        kind = row.get("option_type") or "CE"
        if closed:
            marker = "POSITION CLOSED"
            role = "live_closed"
        elif qty < 0:
            marker = "LIVE SELL"
            role = "live_sell"
        elif kind == "PE":
            marker = "LIVE BUY PE"
            role = "live_buy_pe"
        else:
            marker = "LIVE BUY CE"
            role = "live_buy_ce"
        out.append(
            {
                "signal_id": f"LIVE|{row.get('tradingsymbol')}|{row.get('product')}",
                "instrument": name,
                "timestamp": datetime.now().isoformat(),
                "time_unix": last_unix,
                "signal_type": "BUY" if qty >= 0 else "SELL",
                "option_type": kind,
                "strike": row.get("strike"),
                "tradingsymbol": row.get("tradingsymbol"),
                "quantity": abs(qty),
                "entry_price": row.get("average_price"),
                "option_ltp": row.get("ltp"),
                "net_pnl": row.get("unrealized_pnl"),
                "source": "LIVE_ZERODHA",
                "is_hypothetical": False,
                "marker_role": role,
                "live_marker": marker,
                "badge": "LIVE ZERODHA",
                "note": "LIVE ZERODHA position from net holdings. Not a paper trade.",
            }
        )
    return out


def _place_paper(db: Session, runtime, setting, contract: dict, client_id: str) -> dict:
    if setting is None:
        raise OrderRefused("application settings are missing")
    if setting.kill_switch:
        raise OrderRefused("kill switch is on")
    if setting.trading_mode == "ANALYSIS":
        raise OrderRefused("Switch to PAPER mode, or enable live trading, before BUY CE/PE.")
    fill = simulate_fill(
        contract["side"],
        contract["ltp"],
        contract["quantity"],
        float(setting.slippage_points or 0),
        float(setting.brokerage_per_order or 0),
        float(setting.statutory_charge_bps or 0),
        data_stale=bool(contract.get("stale")),
    )
    if fill is None:
        raise OrderRefused("cannot simulate a fill on stale or missing prices")
    version = latest_version(db)
    if version is None:
        raise OrderRefused("strategy version is missing")
    order = OrderRecord(
        idempotency_key=client_id,
        execution_mode="PAPER",
        side=contract["side"],
        exchange=contract["exchange"],
        tradingsymbol=contract["tradingsymbol"],
        instrument_token=contract["token"],
        quantity=contract["quantity"],
        order_type=contract["order_type"],
        price=Decimal(str(fill.price)),
        status="COMPLETE",
        tag=chart_order_tag(client_id),
        created_at=as_utc_naive(now_utc()),
    )
    db.add(order)
    db.flush()
    position = Position(
        underlying=contract["underlying"],
        exchange=contract["exchange"],
        tradingsymbol=contract["tradingsymbol"],
        instrument_token=contract["token"],
        option_type=contract["option_type"],
        strike=Decimal(str(contract["strike"])),
        expiry=contract["expiry"],
        entry_time=as_utc_naive(now_utc()),
        entry_price=Decimal(str(fill.price)),
        quantity=contract["quantity"],
        strategy_version_id=version.id,
        execution_mode="PAPER",
        status="OPEN",
        charges=Decimal(str(fill.charges)),
        mfe_price=Decimal(str(fill.price)),
    )
    db.add(position)
    db.flush()
    order.position_id = position.id
    db.add(Fill(order_id=order.id, quantity=contract["quantity"], price=Decimal(str(fill.price)), filled_at=as_utc_naive(now_utc())))
    db.add(PositionEvent(position_id=position.id, created_at=as_utc_naive(now_utc()), event_type="ENTRY", reason="CHART_SIGNAL", price=Decimal(str(fill.price))))
    db.commit()
    _record_trade_audit(db, contract, client_id, order, live=False)
    payload = _order_payload(order, replayed=False, live=False)
    payload.update(
        {
            "position_id": position.id,
            "executed_paper_price": float(fill.price),
            "ltp": contract["ltp"],
            "display_status": STATUS_COMPLETE,
            "stage": "POSITION CREATED",
            "badge": "PAPER",
            "note": "Simulated trade only. No order was sent to the broker.",
        }
    )
    return payload


def _place_live(db: Session, runtime, setting, contract: dict, body: dict, client_id: str) -> dict:
    if not getattr(runtime, "chart_live_session_ack", False):
        phrase = str(body.get("liveConfirmation") or body.get("confirmation") or "")
        if phrase != LIVE_ORDER_PHRASE:
            raise OrderRefused("Confirm the first live order this session with CONFIRM LIVE BUY.")
        runtime.chart_live_session_ack = True
    _assert_live_risk(db, runtime, setting, contract)
    access = load_access_token(db, get_settings())
    if not access:
        raise OrderRefused("broker session is not valid")
    from app.adapters.kite.orders import KiteOrderGateway

    kite = kite_client(get_settings(), access)
    gateway = KiteOrderGateway(kite)
    protection = market_protection_percent(setting.market_protection_percent)
    tag = chart_order_tag(client_id)
    order = OrderRecord(
        idempotency_key=client_id,
        execution_mode="LIVE",
        side=contract["side"],
        exchange=contract["exchange"],
        tradingsymbol=contract["tradingsymbol"],
        instrument_token=contract["token"],
        quantity=contract["quantity"],
        order_type=contract["order_type"],
        price=None if contract["order_type"] == "MARKET" else Decimal(str(contract["ltp"] or 0)),
        status="ORDER_REQUESTED",
        tag=tag,
        created_at=as_utc_naive(now_utc()),
    )
    db.add(order)
    db.flush()
    request = OrderRequest(
        idempotency_key=client_id,
        side=contract["side"],
        exchange=contract["exchange"],
        tradingsymbol=contract["tradingsymbol"],
        token=contract["token"],
        quantity=contract["quantity"],
        order_type=contract["order_type"],
        price=None if contract["order_type"] == "MARKET" else contract["ltp"],
        product=contract["product"],
        tag=tag,
        mode="LIVE",
    )
    broker = LiveBroker(
        armed=True,
        kill_switch=bool(setting.kill_switch),
        market_protection_percent=protection,
        supports_market_protection=gateway.supports_market_protection,
    )
    try:
        result = broker.submit(request, gateway, now_utc().timestamp())
    except OrderRefused:
        order.status = "REJECTED"
        order.rejection_reason = STATUS_BLOCKED
        db.commit()
        _record_trade_audit(db, contract, client_id, order, live=True, extra=body)
        raise
    except Exception as exc:
        found = None
        try:
            found = gateway.find_by_tag(tag)
        except Exception:
            found = None
        if found is None:
            order.status = "REJECTED"
            order.rejection_reason = str(exc)[:300]
            db.commit()
            _record_trade_audit(db, contract, client_id, order, live=True, extra=body)
            raise OrderRefused(order.rejection_reason) from exc
        order.broker_order_id = found.broker_order_id
        order.status = found.status
        db.commit()
        _record_trade_audit(db, contract, client_id, order, live=True, extra=body)
        runtime.zerodha_snapshot = None
        return _order_payload(order, replayed=False, live=True, ltp=contract["ltp"])
    order.broker_order_id = result.broker_order_id
    order.status = result.status or "OPEN"
    db.commit()
    _record_trade_audit(db, contract, client_id, order, live=True, extra=body)
    runtime.zerodha_snapshot = None
    payload = _order_payload(order, replayed=False, live=True, ltp=contract["ltp"])
    payload["stage"] = "OPEN" if display_status(order.status) != STATUS_COMPLETE else "COMPLETE"
    payload["display_status"] = display_status(order.status, submitted=True)
    if payload["display_status"] == STATUS_COMPLETE:
        payload["stage"] = "POSITION CREATED"
    return payload


def _assert_live_risk(db: Session, runtime, setting, contract: dict) -> None:
    if setting.kill_switch:
        raise OrderRefused(f"{STATUS_BLOCKED}. Kill switch is on.")
    version = latest_version(db)
    risk = version.risk_policy if version else None
    profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == contract["underlying"]))
    if profile is None or risk is None:
        raise OrderRefused(f"{STATUS_BLOCKED}. Risk policy is unavailable.")
    now = now_ist().time().replace(microsecond=0)
    snapshot = _load_snapshot(db, runtime, force=False)
    net = [row for row in snapshot.get("positions", {}).get("net") or [] if int(row.get("quantity") or 0) != 0]
    trades_today = snapshot.get("orders") or []
    daily = _zerodha_day_pnl(snapshot)
    available = snapshot.get("margins", {}).get("available")
    estimated = contract.get("estimated_value") or 0
    if available is not None and estimated and float(estimated) > float(available):
        raise OrderRefused(f"{STATUS_BLOCKED}. Insufficient funds/margin (need ₹{estimated}, available ₹{available}).")
    ctx = RiskContext(
        mode="LIVE",
        kill_switch=bool(setting.kill_switch),
        data_stale=bool(contract.get("stale")),
        token_valid=session_status(current_session(db)) in {"connected", "expiring"},
        now=now,
        session_start=profile.session_start,
        session_end=profile.session_end,
        entry_start=risk.entry_start,
        entry_stop=risk.entry_stop,
        square_off=risk.forced_square_off,
        mis_cutoff=setting.mis_cutoff,
        daily_pnl=daily,
        max_daily_loss=float(risk.max_daily_loss),
        trades_today=len(trades_today),
        max_trades_per_day=risk.max_trades_per_day,
        open_positions=len(net),
        max_open_positions=risk.max_open_positions,
        exposure=sum(abs(float(row.get("average_price") or 0) * int(row.get("quantity") or 0)) for row in net),
        max_exposure=float(risk.max_exposure),
        quantity=contract["quantity"],
        lot_size=contract["lot_size"],
        max_quantity=risk.max_quantity,
        capital_required=float(estimated or 0),
        max_capital=float(risk.max_capital),
        spread=contract.get("spread"),
        max_spread=float(risk.max_spread),
        option_volume=int(contract.get("volume") or 0),
        min_option_volume=int(risk.min_option_volume),
        option_oi=int(contract.get("oi") or 0),
        min_option_oi=int(risk.min_option_oi),
        exchange=contract["exchange"],
        expected_exchange=profile.derivatives_exchange,
        product=contract["product"],
        underlying_trades=sum(1 for row in trades_today if str(row.get("underlying") or "") == contract["underlying"]),
        max_trades_per_underlying=risk.max_trades_per_underlying,
        cooldown_active=False,
        duplicate_signal=False,
        redis_ok=True,
        database_ok=runtime.db_ok,
    )
    decision = evaluate_risk(ctx)
    if not decision.allowed:
        raise OrderRefused(f"{STATUS_BLOCKED}. {decision.reasons[0]}")


def _load_snapshot(db: Session, runtime, *, force: bool) -> dict:
    now = pytime.monotonic()
    cached = getattr(runtime, "zerodha_snapshot", None)
    stamp = float(getattr(runtime, "zerodha_snapshot_at", 0) or 0)
    if cached and not force and now - stamp < SNAPSHOT_TTL:
        return _with_live_prices(runtime, cached)
    access = load_access_token(db, get_settings())
    if not access:
        empty = _empty_snapshot("broker session is not valid")
        runtime.zerodha_snapshot = empty
        runtime.zerodha_snapshot_at = now
        return empty
    from app.adapters.kite.orders import KiteOrderGateway

    kite = kite_client(get_settings(), access)
    gateway = KiteOrderGateway(kite)
    try:
        books = gateway.positions()
        orders = gateway.orders()
        trades = gateway.trades()
        margins = gateway.margins()
    except Exception as exc:
        log.exception("zerodha snapshot failed")
        empty = _empty_snapshot(str(exc)[:200])
        runtime.zerodha_snapshot = empty
        runtime.zerodha_snapshot_at = now
        return empty
    snapshot = {
        "ok": True,
        "error": "",
        "position_kind": POSITION_KIND,
        "positions": {
            "kind": POSITION_KIND,
            "net": [_normalize_position(db, runtime, row) for row in books.get("net") or []],
            "day": [_normalize_position(db, runtime, row) for row in books.get("day") or []],
        },
        "orders": [_normalize_order(row) for row in orders],
        "trades": [_normalize_trade(row) for row in trades],
        "margins": _normalize_margins(margins),
        "fetched_at": now_ist().replace(microsecond=0).isoformat(),
        "badge": "LIVE ZERODHA",
        "note": "Zerodha net positions. Paper trades are not included.",
    }
    snapshot["open_pnl"] = round(
        sum(float(row.get("unrealized_pnl") or 0) for row in snapshot["positions"]["net"] if int(row.get("quantity") or 0)),
        2,
    )
    runtime.zerodha_snapshot = snapshot
    runtime.zerodha_snapshot_at = now
    return _with_live_prices(runtime, snapshot)


def _with_live_prices(runtime, snapshot: dict) -> dict:
    books = snapshot.get("positions") or {}
    for kind in ("net", "day"):
        for row in books.get(kind) or []:
            quote = runtime.cache.get(int(row["instrument_token"])) if row.get("instrument_token") else {}
            ltp = quote.get("ltp") if quote.get("ltp") is not None else row.get("broker_ltp")
            row["ltp"] = ltp
            qty = int(row.get("quantity") or 0)
            row["unrealized_pnl"] = live_unrealized_pnl(row.get("average_price"), ltp, qty)
            row["invested_value"] = invested_value(row.get("average_price"), qty)
            row["current_value"] = current_value(ltp, qty)
            realized = row.get("realized_pnl")
            unreal = row.get("unrealized_pnl")
            row["net_pnl"] = None if unreal is None else round(float(unreal) + float(realized or 0), 2)
            row["status"] = "OPEN" if qty else "FLAT"
    snapshot["open_pnl"] = round(
        sum(float(row.get("unrealized_pnl") or 0) for row in books.get("net") or [] if int(row.get("quantity") or 0)),
        2,
    )
    return snapshot


def _cached_net_positions(db: Session, runtime) -> list[dict]:
    snapshot = _load_snapshot(db, runtime, force=False)
    return [row for row in (snapshot.get("positions") or {}).get("net") or [] if int(row.get("quantity") or 0)]


def _normalize_position(db: Session, runtime, row: dict) -> dict:
    token = int(row.get("instrument_token") or 0)
    symbol = str(row.get("tradingsymbol") or "")
    instrument = db.scalar(select(Instrument).where(Instrument.instrument_token == token).order_by(Instrument.id.desc())) if token else None
    qty = int(row.get("quantity") or 0)
    avg = _num(row.get("average_price"))
    broker_ltp = _num(row.get("last_price"))
    quote = runtime.cache.get(token) if token else {}
    ltp = quote.get("ltp") if quote.get("ltp") is not None else broker_ltp
    option_type = None
    if instrument and instrument.instrument_type in {"CE", "PE"}:
        option_type = instrument.instrument_type
    else:
        option_type = parse_option_type(symbol)
    underlying = None
    if instrument and instrument.instrument_name in ALLOWED_UNDERLYINGS:
        underlying = instrument.instrument_name
    else:
        text = symbol.upper()
        underlying = "NIFTY" if text.startswith("NIFTY") else "SENSEX" if text.startswith("SENSEX") else None
    unreal = live_unrealized_pnl(avg, ltp, qty)
    realized = _num(row.get("realised") if row.get("realised") is not None else row.get("realised_pnl"))
    return {
        "source": "ZERODHA",
        "badge": "LIVE ZERODHA",
        "position_kind": POSITION_KIND,
        "exchange": row.get("exchange"),
        "tradingsymbol": symbol,
        "instrument_token": token,
        "product": row.get("product"),
        "quantity": qty,
        "average_price": avg,
        "ltp": ltp,
        "broker_ltp": broker_ltp,
        "invested_value": invested_value(avg, qty),
        "current_value": current_value(ltp, qty),
        "unrealized_pnl": unreal,
        "realized_pnl": realized,
        "net_pnl": None if unreal is None else round(float(unreal) + float(realized or 0), 2),
        "broker_pnl": _num(row.get("pnl")),
        "unrealized_label": "LIVE UNREALIZED P&L",
        "option_type": option_type,
        "strike": float(instrument.strike) if instrument and instrument.strike is not None else None,
        "expiry": instrument.expiry.isoformat() if instrument and instrument.expiry else None,
        "underlying": underlying,
        "status": "OPEN" if qty else "FLAT",
    }


def _normalize_order(row: dict) -> dict:
    status = display_status(row.get("status"), submitted=True)
    symbol = str(row.get("tradingsymbol") or "")
    text = symbol.upper()
    underlying = "NIFTY" if text.startswith("NIFTY") else "SENSEX" if text.startswith("SENSEX") else None
    return {
        "source": "ZERODHA",
        "kind": "order",
        "order_id": str(row.get("order_id") or ""),
        "exchange": row.get("exchange"),
        "tradingsymbol": symbol,
        "option_type": parse_option_type(symbol),
        "transaction_type": row.get("transaction_type"),
        "quantity": int(row.get("quantity") or 0),
        "filled_quantity": int(row.get("filled_quantity") or 0),
        "price": _num(row.get("price")),
        "average_price": _num(row.get("average_price")),
        "status": row.get("status"),
        "display_status": status,
        "product": row.get("product"),
        "order_type": row.get("order_type"),
        "tag": row.get("tag"),
        "status_message": row.get("status_message") or row.get("rejection_reason") or "",
        "order_timestamp": row.get("order_timestamp"),
        "exchange_timestamp": row.get("exchange_timestamp"),
        "underlying": underlying,
        "badge": "LIVE ZERODHA",
    }


def _normalize_trade(row: dict) -> dict:
    symbol = str(row.get("tradingsymbol") or "")
    text = symbol.upper()
    underlying = "NIFTY" if text.startswith("NIFTY") else "SENSEX" if text.startswith("SENSEX") else None
    stamp = row.get("fill_timestamp") or row.get("exchange_timestamp") or row.get("order_timestamp")
    return {
        "source": "ZERODHA",
        "kind": "trade",
        "trade_id": str(row.get("trade_id") or ""),
        "order_id": str(row.get("order_id") or ""),
        "exchange": row.get("exchange"),
        "tradingsymbol": symbol,
        "option_type": parse_option_type(symbol),
        "transaction_type": row.get("transaction_type"),
        "quantity": int(row.get("quantity") or 0),
        "price": _num(row.get("average_price") if row.get("average_price") is not None else row.get("price")),
        "product": row.get("product"),
        "fill_timestamp": stamp,
        "underlying": underlying,
        "status": "COMPLETE",
        "badge": "LIVE ZERODHA",
    }


def _normalize_margins(payload: dict) -> dict:
    available = payload.get("available") if isinstance(payload.get("available"), dict) else {}
    live = available.get("live_balance")
    cash = available.get("cash")
    net = payload.get("net")
    value = live if live is not None else cash if cash is not None else net
    return {"available": _num(value), "net": _num(net), "raw_ok": bool(payload)}


def _zerodha_day_pnl(snapshot: dict) -> float:
    rows = (snapshot.get("positions") or {}).get("day") or []
    total = 0.0
    for row in rows:
        pnl = row.get("broker_pnl")
        if pnl is None:
            pnl = row.get("net_pnl")
        total += float(pnl or 0)
    return total


def _empty_snapshot(error: str) -> dict:
    return {
        "ok": False,
        "error": error,
        "position_kind": POSITION_KIND,
        "positions": {"kind": POSITION_KIND, "net": [], "day": []},
        "orders": [],
        "trades": [],
        "margins": {"available": None, "net": None, "raw_ok": False},
        "open_pnl": 0,
        "badge": "LIVE ZERODHA",
        "note": "Zerodha net positions. Paper trades are not included.",
        "fetched_at": None,
    }


def _order_payload(order: OrderRecord, *, replayed: bool, live: bool, ltp=None) -> dict:
    display = display_status(order.status, submitted=True)
    if order.status == "ORDER_REQUESTED":
        display = STATUS_SUBMITTED
    return {
        "mode": "LIVE" if live else "PAPER",
        "badge": "LIVE ZERODHA" if live else "PAPER",
        "is_paper_trade": not live,
        "replayed": replayed,
        "order_id": order.broker_order_id,
        "local_order_id": order.id,
        "tradingsymbol": order.tradingsymbol,
        "quantity": order.quantity,
        "status": order.status,
        "display_status": display,
        "rejection_reason": order.rejection_reason,
        "ltp": ltp if ltp is not None else jsonable(order.price),
        "note": "Order submitted to Zerodha. Submitted is not the same as executed." if live else "Simulated trade only. No order was sent to the broker.",
    }


def _public_contract(card: dict | None, live_rows: list[dict]) -> dict | None:
    if not card:
        return None
    token = card.get("instrument_token")
    live = next((row for row in live_rows if int(row.get("instrument_token") or 0) == int(token or 0)), None)
    return {
        "available": bool(card.get("instrument_token")),
        "moneyness_label": card.get("moneyness_label"),
        "tradingsymbol": card.get("tradingsymbol"),
        "instrument_token": card.get("instrument_token"),
        "strike": card.get("strike"),
        "expiry": card.get("expiry"),
        "ltp": card.get("ltp"),
        "bid": card.get("bid"),
        "ask": card.get("ask"),
        "oi": card.get("oi"),
        "volume": card.get("volume"),
        "lot_size": card.get("lot_size"),
        "option_type": card.get("option_type"),
        "live_position": live,
        "unavailable_reason": card.get("unavailable_reason") or "",
    }


def _card_for_strike(side_map: dict, strike) -> dict | None:
    if strike is None:
        return None
    for card in side_map.values():
        if card and card.get("strike") is not None and float(card["strike"]) == float(strike):
            return card
    return None


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_trade_audit(db: Session, contract: dict, client_id: str, order: OrderRecord, *, live: bool, extra: dict | None = None) -> None:
    """Persist a chart order row. Tokens and API secrets are never written."""
    session = current_session(db)
    now = as_utc_naive(now_utc())
    row = db.scalar(select(TradingOrderAudit).where(TradingOrderAudit.client_order_id == client_id).order_by(TradingOrderAudit.id.desc()))
    if row is None:
        row = TradingOrderAudit(created_at=now, client_order_id=client_id[:64])
        db.add(row)
    row.updated_at = now
    row.user_id = (session.user_id if session else "local")[:32]
    row.tenant_id = "local"
    row.broker = "Zerodha"
    row.execution_mode = "LIVE" if live else "PAPER"
    row.instrument = str(contract.get("underlying") or "")[:16]
    row.exchange = str(contract.get("exchange") or order.exchange or "")[:8]
    row.tradingsymbol = str(order.tradingsymbol or contract.get("tradingsymbol") or "")[:64]
    row.option_type = str(contract.get("option_type") or "")[:4]
    row.strike = Decimal(str(contract["strike"])) if contract.get("strike") is not None else None
    row.transaction_type = str(contract.get("side") or order.side or "BUY")[:8]
    row.quantity = int(order.quantity or contract.get("quantity") or 0)
    row.lots = int(contract.get("lots") or 1)
    row.order_type = str(order.order_type or contract.get("order_type") or "MARKET")[:8]
    row.product = str(contract.get("product") or "MIS")[:8]
    row.requested_price = Decimal(str(contract["ltp"])) if contract.get("ltp") is not None else None
    row.signal_direction = str(contract.get("trend") or (extra or {}).get("signal") or "")[:16]
    score = contract.get("board", {}).get("direction_score") if isinstance(contract.get("board"), dict) else None
    row.signal_score = Decimal(str(score)) if score is not None else None
    row.signal_timestamp = now
    row.broker_order_id = order.broker_order_id
    row.broker_status = str(order.status or "")[:32]
    row.reject_reason = str(order.rejection_reason or "")[:400]
    row.local_order_id = order.id
    db.commit()


def broker_profile(db: Session, runtime) -> dict:
    access = load_access_token(db, get_settings())
    if not access:
        raise OrderRefused("Connect Zerodha first")
    from app.adapters.kite.orders import KiteOrderGateway

    kite = kite_client(get_settings(), access)
    raw = KiteOrderGateway(kite).profile()
    return {
        "user_id": raw.get("user_id"),
        "user_name": raw.get("user_name"),
        "broker": "Zerodha",
        "exchanges": raw.get("exchanges") or [],
        "products": raw.get("products") or [],
        "order_types": raw.get("order_types") or [],
        "badge": "LIVE ZERODHA",
        "note": "Profile only. Access tokens are not returned.",
    }


def listed_instruments(db: Session, underlying: str) -> dict:
    name = assert_underlying(underlying)
    rows = db.scalars(
        select(Instrument)
        .where(Instrument.instrument_name == name, Instrument.instrument_type.in_(("CE", "PE")))
        .order_by(Instrument.expiry, Instrument.strike)
    ).all()
    seen: set[tuple] = set()
    out = []
    for row in rows:
        key = (row.exchange, row.tradingsymbol)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "exchange": row.exchange,
                "tradingsymbol": row.tradingsymbol,
                "instrument_token": row.instrument_token,
                "option_type": row.instrument_type,
                "strike": float(row.strike) if row.strike is not None else None,
                "expiry": row.expiry.isoformat() if row.expiry else None,
                "lot_size": row.lot_size,
            }
        )
    return {"underlying": name, "instruments": out[:400], "source": "instrument_master", "note": "Resolved from the local instrument master. Lot size is never hard-coded."}


def cancel_live_order(db: Session, runtime, order_id: str) -> dict:
    if not getattr(runtime, "live_latch", False) and not (db.get(AppSetting, 1) and db.get(AppSetting, 1).chart_live_enabled):
        raise OrderRefused("Live trading is not enabled.")
    access = load_access_token(db, get_settings())
    if not access:
        raise OrderRefused("broker session is not valid")
    from app.adapters.kite.orders import KiteOrderGateway

    kite = kite_client(get_settings(), access)
    KiteOrderGateway(kite).cancel(order_id)
    local = db.scalar(select(OrderRecord).where(OrderRecord.broker_order_id == str(order_id)))
    if local is not None:
        local.status = "CANCELLED"
    audit_row = db.scalar(select(TradingOrderAudit).where(TradingOrderAudit.broker_order_id == str(order_id)).order_by(TradingOrderAudit.id.desc()))
    if audit_row is not None:
        audit_row.broker_status = "CANCELLED"
        audit_row.updated_at = as_utc_naive(now_utc())
    db.commit()
    runtime.zerodha_snapshot = None
    return {"order_id": str(order_id), "display_status": STATUS_CANCELLED, "badge": "LIVE ZERODHA"}


def order_status(db: Session, runtime, order_id: str) -> dict:
    snapshot = _load_snapshot(db, runtime, force=False)
    match = next((row for row in snapshot.get("orders") or [] if str(row.get("order_id")) == str(order_id)), None)
    local = db.scalar(select(OrderRecord).where(OrderRecord.broker_order_id == str(order_id)))
    if match is None and local is None:
        raise OrderRefused("Order was not found.")
    status = (match or {}).get("status") or (local.status if local else "")
    return {
        "order_id": str(order_id),
        "status": status,
        "display_status": display_status(status, submitted=True),
        "tradingsymbol": (match or {}).get("tradingsymbol") or (local.tradingsymbol if local else ""),
        "quantity": (match or {}).get("quantity") or (local.quantity if local else None),
        "average_price": (match or {}).get("average_price"),
        "status_message": (match or {}).get("status_message") or (local.rejection_reason if local else ""),
        "badge": "LIVE ZERODHA",
        "note": "Submitted is not the same as executed.",
    }


def dashboard_brief(db: Session, runtime) -> dict:
    now = pytime.monotonic()
    cached = getattr(runtime, "chart_brief", None)
    stamp = float(getattr(runtime, "chart_brief_at", 0) or 0)
    if cached and now - stamp < 6:
        cached["trading"] = settings_payload(db, runtime)
        last = db.scalar(select(TradingOrderAudit).order_by(TradingOrderAudit.id.desc()))
        cached["last_order"] = _audit_public(last)
        return cached
    markets = []
    for symbol in ALLOWED_UNDERLYINGS:
        try:
            board = chart_strikes(db, runtime, symbol)
        except Exception:
            log.exception("dashboard brief failed for %s", symbol)
            continue
        markets.append(
            {
                "symbol": symbol,
                "trend": board.get("trend"),
                "recommended_action": board.get("recommended_action"),
                "recommended_side": board.get("recommended_side"),
                "condition_count": board.get("condition_count"),
                "condition_total": board.get("condition_total"),
                "rungs": [
                    {
                        "label": row.get("label"),
                        "strike": row.get("strike"),
                        "ce_ltp": (row.get("ce") or {}).get("ltp"),
                        "pe_ltp": (row.get("pe") or {}).get("ltp"),
                    }
                    for row in board.get("rungs") or []
                ],
            }
        )
    last = db.scalar(select(TradingOrderAudit).order_by(TradingOrderAudit.id.desc()))
    payload = {
        "markets": markets,
        "last_order": _audit_public(last),
        "trading": settings_payload(db, runtime),
        "note": "Strategy comes from the existing dashboard engine. Positions come from Zerodha net holdings.",
    }
    runtime.chart_brief = payload
    runtime.chart_brief_at = now
    return payload


def _audit_public(row: TradingOrderAudit | None) -> dict | None:
    if row is None:
        return None
    return {
        "tradingsymbol": row.tradingsymbol,
        "option_type": row.option_type,
        "strike": float(row.strike) if row.strike is not None else None,
        "quantity": row.quantity,
        "broker_order_id": row.broker_order_id,
        "broker_status": row.broker_status,
        "display_status": display_status(row.broker_status, submitted=True),
        "execution_mode": row.execution_mode,
        "badge": "LIVE ZERODHA" if row.execution_mode == "LIVE" else "PAPER",
        "reject_reason": row.reject_reason,
        "created_at": row.created_at.isoformat(sep=" ") if row.created_at else None,
    }

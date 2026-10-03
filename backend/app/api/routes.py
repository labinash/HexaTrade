from datetime import datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.kite.auth import (
    clear_session,
    current_session,
    exchange_request_token,
    load_access_token,
    login_url,
    session_status,
    store_session,
)
from app.adapters.kite.instruments import refresh_instruments
from app.core.clock import now_utc, reference_aligned
from app.core.config import get_settings
from app.core.security import mask_key
from app.db.bootstrap import clone_version, latest_version, params_for
from app.db.models import (
    AppSetting,
    AuditEvent,
    OrderRecord,
    Position,
    StrategyParameters,
    UnderlyingProfile,
)
from app.db.session import session_factory
from app.domain.strategy import direction_state
from app.services.engine import (
    _exit_position_if_needed,
    audit,
    dashboard,
    public_chain,
    ticker_view,
)
from app.services.mapping import assign, jsonable
from app.services.oi_scanner import scan as scan_oi
from app.services.selection import CONFIG_FIELDS as SELECTION_FIELDS
from app.services.selection import config_payload as selection_config_payload
from app.services.selection import config_row as selection_config_row
from app.services.option_panel import candidate_for_token, instrument_lot_size, panel as option_panel
from app.services.selection import (
    open_paper_position,
    payload as selection_payload,
    persist_run,
    recent_duplicate,
    record_proposal,
    resize,
    run_selection,
)

router = APIRouter(prefix="/api")
LIVE_PHRASE = "PLACE REAL ORDERS"


def get_db():
    db = session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def runtime_of(request: Request):
    return request.app.state.runtime


def require_db(request: Request):
    runtime = runtime_of(request)
    if not runtime.db_ok:
        db = None
        try:
            db = session_factory()()
            db.execute(text("SELECT 1"))
            runtime.db_ok = True
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Database is unavailable") from exc
        finally:
            if db is not None:
                db.close()
    return runtime


def _time_fields(row) -> dict:
    data = {}
    for column in row.__table__.columns:
        if column.key in {"id", "strategy_version_id"}:
            continue
        data[column.key] = jsonable(getattr(row, column.key))
    return data


def _clock_of(value: str | None, field: str) -> time | None:
    if not value:
        return None
    try:
        hour, minute = value.split(":")[:2]
        return time(int(hour), int(minute))
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail=f"{field} must look like HH:MM") from None


def _date_of(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="expiry must look like YYYY-MM-DD") from None


def _apply_times(data: dict, names: set[str]) -> dict:
    cleaned = dict(data)
    for name in names:
        if name in cleaned and isinstance(cleaned[name], str) and cleaned[name]:
            hour, minute = cleaned[name].split(":")[:2]
            cleaned[name] = time(int(hour), int(minute))
    return cleaned


@router.get("/health")
def health(request: Request):
    runtime = runtime_of(request)
    return {
        "status": "ok" if runtime.db_ok else "degraded",
        "database": "up" if runtime.db_ok else "down",
        "redis": "up" if runtime.bus.ok else "down",
        "trading_loop": "running" if runtime.db_ok else "stopped",
    }


@router.get("/ticker")
def ticker(request: Request):
    """Header feed. Does not block the UI on a slow SQL login."""
    runtime = runtime_of(request)
    db = None
    try:
        if runtime.db_ok:
            db = session_factory()()
            payload = ticker_view(db, runtime)
            payload["as_of"] = jsonable(payload["as_of"])
            payload["database"] = True
            return payload
    except Exception:
        if runtime.last_ticker:
            cached = dict(runtime.last_ticker)
            cached["database"] = False
            cached["as_of"] = jsonable(cached.get("as_of"))
            return cached
        return {"items": [], "broker": "disconnected", "mode": "ANALYSIS", "database": False}
    finally:
        if db is not None:
            db.close()
    if runtime.last_ticker:
        cached = dict(runtime.last_ticker)
        cached["as_of"] = jsonable(cached.get("as_of"))
        cached["database"] = False
        return cached
    return {"items": [], "broker": "disconnected", "mode": "ANALYSIS", "database": False}


@router.get("/broker/status")
def broker_status(request: Request):
    """Broker screen stays usable when SQL Server is briefly unreachable."""
    settings = get_settings()
    runtime = runtime_of(request)
    payload = {
        "status": "disconnected",
        "user_id": None,
        "api_key_masked": mask_key(settings.kite_api_key),
        "expires_at": None,
        "redirect_url": settings.kite_redirect_url,
        "trading_mode": "ANALYSIS",
        "kill_switch": False,
        "auto_entry_armed": False,
        "auto_exit_armed": False,
        "live_armed": runtime.live_latch,
        "product": "MIS",
        "order_type": "LIMIT",
        "lots": 1,
        "database": "down",
        "redis": "up" if runtime.bus.ok else "down",
        "notes": [
            "Zerodha sessions end at 06:00 IST. This app does not log in with a password or TOTP.",
            "Whitelist a static IP on the Kite developer console. This app cannot set that.",
            "Keep order flow under 10 orders per second.",
            "A working screen is not evidence the strategy makes money.",
        ],
    }
    if not runtime.db_ok:
        payload["error"] = "Database is unavailable"
        return payload
    db = None
    try:
        db = session_factory()()
        row = current_session(db)
        setting = db.get(AppSetting, 1)
        payload.update(
            {
                "status": session_status(row),
                "user_id": row.user_id if row else None,
                "expires_at": row.expires_at.isoformat(sep=" ") if row else None,
                "trading_mode": setting.trading_mode if setting else "ANALYSIS",
                "kill_switch": bool(setting.kill_switch) if setting else False,
                "auto_entry_armed": bool(setting.auto_entry_armed) if setting else False,
                "auto_exit_armed": bool(setting.auto_exit_armed) if setting else False,
                "product": setting.product if setting else "MIS",
                "order_type": setting.order_type if setting else "LIMIT",
                "lots": setting.lots if setting else 1,
                "database": "up",
            }
        )
        runtime.db_ok = True
        return payload
    except Exception:
        runtime.db_ok = False
        payload["error"] = "Database is unavailable"
        return payload
    finally:
        if db is not None:
            db.close()


@router.get("/broker/login-url")
def broker_login(request: Request):
    require_db(request)
    try:
        return {"url": login_url(get_settings())}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/broker/callback")
def broker_callback(request_token: str, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    settings = get_settings()
    try:
        session = exchange_request_token(settings, request_token)
        store_session(db, settings, session["user_id"], session["access_token"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Zerodha login did not complete") from exc
    return RedirectResponse("http://localhost:5999/broker?connected=1")


class RequestTokenBody(BaseModel):
    request_token: str


@router.post("/broker/session")
def broker_session(body: RequestTokenBody, request: Request, db: Session = Depends(get_db)):
    """Manual exchange for when the console redirect URL does not point at this app."""
    require_db(request)
    settings = get_settings()
    token = body.request_token.strip()
    if "request_token=" in token:
        token = token.split("request_token=", 1)[1].split("&", 1)[0]
    if not token:
        raise HTTPException(status_code=400, detail="Paste the request_token from the redirect URL")
    try:
        session = exchange_request_token(settings, token)
        store_session(db, settings, session["user_id"], session["access_token"])
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Zerodha rejected that request_token. It expires within minutes, so log in again and paste the newest one.",
        ) from exc
    return {"status": "connected", "user_id": session["user_id"]}


@router.post("/broker/logout")
def broker_logout(request: Request, db: Session = Depends(get_db)):
    require_db(request)
    token = load_access_token(db, get_settings())
    if token:
        try:
            from app.adapters.kite.auth import kite_client

            kite_client(get_settings(), token).invalidate_access_token(token)
        except Exception:
            pass
    clear_session(db)
    return {"status": "disconnected"}


@router.post("/instruments/refresh")
def refresh(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    token = load_access_token(db, get_settings())
    if not token:
        raise HTTPException(status_code=401, detail="Connect Zerodha first")
    from app.adapters.kite.auth import kite_client

    count = refresh_instruments(db, kite_client(get_settings(), token))
    runtime.instruments_ready = True
    return {"stored": count}


@router.get("/chain")
def chain(
    request: Request,
    underlying: str = "NIFTY",
    expiry: str | None = None,
    strikes_above: int = 10,
    strikes_below: int = 10,
    db: Session = Depends(get_db),
):
    runtime = require_db(request)
    expiry_date = datetime.fromisoformat(expiry).date() if expiry else None
    return public_chain(db, runtime, underlying.upper(), expiry_date, strikes_above, strikes_below)


@router.get("/dashboard")
def dash(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    return dashboard(db, runtime)


@router.get("/dashboard/options/{underlying}")
def dashboard_options(underlying: str, request: Request, expiry: str | None = None, db: Session = Depends(get_db)):
    """Option trading panel for one underlying. Read only."""
    runtime = require_db(request)
    try:
        return option_panel(db, runtime, underlying.upper(), _date_of(expiry))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


class PaperOrderBody(BaseModel):
    underlying: str
    instrument_token: int
    side: str = "BUY"
    lots: int = 1
    order_type: str = "MARKET"
    requested_price: float | None = None
    expiry: str | None = None
    acknowledge: bool = False
    override_duplicate: bool = False


@router.post("/paper-orders")
def create_paper_order(body: PaperOrderBody, request: Request, db: Session = Depends(get_db)):
    """Records a simulated buy from the dashboard panel.

    Nothing here reaches the broker. Quantity is derived on the server from the
    instrument master lot size, so a quantity sent by the browser is ignored.
    """
    runtime = require_db(request)
    if not body.acknowledge:
        raise HTTPException(status_code=400, detail="Acknowledge that this is a simulated trade before continuing")
    if body.side != "BUY":
        raise HTTPException(status_code=400, detail="This application only buys options, so side must be BUY")
    if body.order_type not in {"MARKET", "LIMIT"}:
        raise HTTPException(status_code=400, detail="order_type must be MARKET or LIMIT")
    symbol = body.underlying.upper()

    setting = db.get(AppSetting, 1)
    window = int(setting.duplicate_window_seconds) if setting else 60
    if not body.override_duplicate:
        clash = recent_duplicate(db, body.instrument_token, body.side, window)
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A {clash.tradingsymbol} position was opened less than {window}s ago. "
                    "Confirm again if this second order is intended."
                ),
            )

    try:
        result, view, quotes = run_selection(db, runtime, symbol, _date_of(body.expiry), require_signal=False)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if view.get("stale"):
        raise HTTPException(status_code=409, detail="Market data is stale, no simulated fill was recorded")

    candidate = candidate_for_token(view, quotes, body.instrument_token)
    if candidate is None:
        raise HTTPException(status_code=404, detail="That contract is not in the current candidate window")
    if candidate.status != "PASS":
        raise HTTPException(status_code=409, detail="; ".join(candidate.reject_reasons) or "contract failed the configured filters")

    lot_size = instrument_lot_size(db, body.instrument_token)
    if lot_size is None:
        raise HTTPException(status_code=409, detail="Instrument lot size is unavailable, refresh the instrument master")
    try:
        resize(candidate, body.lots, lot_size)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    params = view["params"]
    state, score = direction_state(view["evaluation"], params)
    run = persist_run(db, result, view)
    proposal = record_proposal(
        db, run, candidate,
        source="dashboard_panel",
        direction_state=state,
        direction_score=score,
        signal_confirmed=bool(view.get("passed")),
        order_type=body.order_type,
        requested_price=body.requested_price,
    )
    try:
        position = open_paper_position(db, view, candidate, proposal)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "mode": "PAPER",
        "is_paper_trade": True,
        "proposal_id": proposal.id,
        "position_id": position.id,
        "run_id": run.id,
        "tradingsymbol": candidate.tradingsymbol,
        "option_type": candidate.option_type,
        "strike": jsonable(position.strike),
        "expiry": jsonable(position.expiry),
        "lots": body.lots,
        "quantity": position.quantity,
        "lot_size": lot_size,
        "requested_price": body.requested_price,
        "executed_paper_price": jsonable(position.entry_price),
        "stop_loss": jsonable(position.stop_loss),
        "target": jsonable(position.target),
        "direction": state,
        "direction_score": score,
        "candidate_score": candidate.score,
        "signal_confirmed": bool(view.get("passed")),
        "note": "Simulated trade only. No order was sent to the broker.",
    }


@router.get("/strategy")
def get_strategy(request: Request, db: Session = Depends(get_db)):
    require_db(request)
    version = latest_version(db)
    profiles = db.scalars(select(UnderlyingProfile)).all()
    return {
        "version_id": version.id,
        "version_number": version.version_number,
        "note": version.note,
        # One block per underlying. NIFTY and SENSEX are configured separately and
        # can carry different directions and thresholds.
        "parameters": {row.underlying: _time_fields(row) for row in version.parameters},
        "exits": _time_fields(version.exit_policy),
        "risk": _time_fields(version.risk_policy),
        "selection": _time_fields(version.option_rule),
        "underlyings": [
            {"symbol": row.symbol, "atr_threshold": jsonable(row.atr_threshold), "derivatives_exchange": row.derivatives_exchange}
            for row in profiles
        ],
    }


class StrategyBody(BaseModel):
    parameters: dict
    underlying: str = "NIFTY"
    note: str = "Updated from the strategy screen"


@router.put("/strategy")
def put_strategy(body: StrategyBody, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    data = _apply_times(body.parameters, {"momentum_time", "oi_time", "time_start", "time_end", "pe_midday_time"})
    minutes = int(data.get("timeframe_minutes") or 1)
    for name in ("momentum_time", "oi_time", "pe_midday_time"):
        clock = data.get(name)
        if isinstance(clock, time) and not reference_aligned(clock, minutes):
            raise HTTPException(status_code=400, detail=f"{name} does not align to the {minutes}-minute timeframe")
    unit = data.get("momentum_unit")
    if unit is not None and unit not in {"points", "percent"}:
        raise HTTPException(status_code=400, detail="momentum_unit must be points or percent")
    direction = data.get("direction")
    if direction is not None and direction not in {"BULLISH", "BEARISH"}:
        raise HTTPException(status_code=400, detail="direction must be BULLISH or BEARISH")
    for low, high in (("rsi_min", "rsi_max"), ("rsi_min_bearish", "rsi_max_bearish")):
        if data.get(low) is not None and data.get(high) is not None and float(data[low]) >= float(data[high]):
            raise HTTPException(status_code=400, detail=f"{low} must be below {high}")
    version = clone_version(db, body.note)
    row = params_for(version, body.underlying)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no strategy parameters for {body.underlying}")
    # underlying is the key of the row, never a value the client can rewrite.
    columns = {column.key for column in StrategyParameters.__table__.columns} - {"underlying"}
    assign(row, data, columns)
    return {"version_id": version.id, "version_number": version.version_number, "underlying": body.underlying}


@router.put("/exits")
def put_exits(body: dict, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    version = clone_version(db, "Exit policy update")
    assign(version.exit_policy, _apply_times(body, {"time_exit"}), set(body))
    return {"version_id": version.id}


@router.put("/risk")
def put_risk(body: dict, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    thresholds = body.pop("atr_thresholds", {})
    version = clone_version(db, "Risk policy update")
    assign(version.risk_policy, _apply_times(body, {"entry_start", "entry_stop", "forced_square_off"}), set(body))
    for symbol, threshold in thresholds.items():
        profile = db.scalar(select(UnderlyingProfile).where(UnderlyingProfile.symbol == symbol.upper()))
        if profile is not None:
            profile.atr_threshold = threshold
    return {"version_id": version.id}


@router.put("/option-selection")
def put_selection(body: dict, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    version = clone_version(db, "Option selection update")
    assign(version.option_rule, body, set(body))
    return {"version_id": version.id}


@router.get("/oi-scanner")
def oi_scanner(
    request: Request,
    underlying: str = "NIFTY",
    expiry: str | None = None,
    expiry_mode: str | None = None,
    option_type: str | None = None,
    reference_time: str | None = None,
    interval_minutes: int | None = None,
    strikes_above: int | None = None,
    strikes_below: int | None = None,
    show_all: bool = False,
    min_oi_change_interval: float | None = None,
    min_oi_change_reference: float | None = None,
    direction: str | None = None,
    as_of: str | None = None,
    db: Session = Depends(get_db),
):
    runtime = require_db(request)
    overrides = {
        "expiry_mode": expiry_mode,
        "option_type": option_type.upper() if option_type else None,
        "interval_minutes": interval_minutes,
        "strikes_above": strikes_above,
        "strikes_below": strikes_below,
        "min_oi_change_interval": min_oi_change_interval,
        "min_oi_change_reference": min_oi_change_reference,
        "direction": direction.upper() if direction else None,
        "show_all": show_all,
        "reference_time": _clock_of(reference_time, "reference_time"),
        "specific_expiry": _date_of(expiry),
    }
    if overrides["specific_expiry"] is not None:
        overrides["expiry_mode"] = "specific"
    try:
        return scan_oi(db, runtime, underlying.upper(), overrides, _clock_of(as_of, "as_of"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/strike-selection")
def strike_selection(
    request: Request,
    underlying: str = "NIFTY",
    expiry: str | None = None,
    persist: bool = False,
    db: Session = Depends(get_db),
):
    runtime = require_db(request)
    symbol = underlying.upper()
    target = datetime.strptime(expiry, "%Y-%m-%d").date() if expiry else None
    try:
        result, view, _ = run_selection(db, runtime, symbol, target)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    run_id = None
    if persist:
        run_id = persist_run(db, result, view).id
    body = selection_payload(result, view, selection_config_payload(db, symbol))
    body["run_id"] = run_id
    return body


@router.get("/strike-selection/config")
def get_strike_config(request: Request, db: Session = Depends(get_db)):
    require_db(request)
    return {
        symbol: selection_config_payload(db, symbol)
        for symbol in [row.symbol for row in db.scalars(select(UnderlyingProfile)).all()]
    }


@router.put("/strike-selection/config")
def put_strike_config(body: dict, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    symbol = str(body.pop("underlying", "")).upper()
    row = selection_config_row(db, symbol)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{symbol or 'underlying'} is not configured")
    weights = {key: value for key, value in body.items() if key.startswith("weight_") and value is not None}
    if weights and sum(float(value) for value in weights.values()) <= 0:
        raise HTTPException(status_code=400, detail="Score weights must add up to more than zero")
    assign(row, body, SELECTION_FIELDS)
    audit(db, "SELECTION_CONFIG_UPDATED", underlying=symbol, reason=", ".join(sorted(body)))
    return selection_config_payload(db, symbol)


class PaperTradeBody(BaseModel):
    underlying: str = "NIFTY"
    tradingsymbol: str
    expiry: str | None = None
    acknowledge: bool = False


@router.post("/strike-selection/paper-trade")
def selection_paper_trade(body: PaperTradeBody, request: Request, db: Session = Depends(get_db)):
    """Records a simulated trade. This endpoint never reaches the broker."""
    runtime = require_db(request)
    if not body.acknowledge:
        raise HTTPException(status_code=400, detail="Acknowledge that this is a simulated trade before continuing")
    symbol = body.underlying.upper()
    target = datetime.strptime(body.expiry, "%Y-%m-%d").date() if body.expiry else None
    try:
        result, view, _ = run_selection(db, runtime, symbol, target)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    candidate = next(
        (c for c in result.top if c.tradingsymbol == body.tradingsymbol and c.status == "PASS"),
        None,
    )
    if candidate is None:
        raise HTTPException(
            status_code=409,
            detail=f"{body.tradingsymbol} is no longer among the passing candidates, re-run the selection",
        )
    run = persist_run(db, result, view)
    proposal = record_proposal(db, run, candidate)
    try:
        position = open_paper_position(db, view, candidate, proposal)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "proposal_id": proposal.id,
        "position_id": position.id,
        "run_id": run.id,
        "mode": "PAPER",
        "tradingsymbol": candidate.tradingsymbol,
        "entry": jsonable(position.entry_price),
        "stop_loss": jsonable(position.stop_loss),
        "target": jsonable(position.target),
        "quantity": position.quantity,
    }


class ModeBody(BaseModel):
    mode: str
    confirmation: str = ""


@router.post("/trading/mode")
def set_mode(body: ModeBody, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    mode = body.mode.upper()
    if mode not in {"ANALYSIS", "PAPER", "LIVE"}:
        raise HTTPException(status_code=400, detail="Unknown mode")
    if mode == "LIVE" and body.confirmation != LIVE_PHRASE:
        raise HTTPException(status_code=400, detail=f'Type {LIVE_PHRASE} to allow live mode')
    setting = db.get(AppSetting, 1)
    setting.trading_mode = mode
    if mode != "LIVE":
        runtime.live_latch = False
        setting.live_armed = False
    audit(db, "STRATEGY_EVALUATED", result=mode, reason="trading mode changed")
    return {"mode": mode, "live_armed": runtime.live_latch}


class ArmBody(BaseModel):
    confirmation: str


@router.post("/trading/arm")
def arm(body: ArmBody, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    if body.confirmation != LIVE_PHRASE:
        raise HTTPException(status_code=400, detail=f'Type {LIVE_PHRASE}')
    setting = db.get(AppSetting, 1)
    setting.trading_mode = "LIVE"
    setting.live_armed = True
    setting.auto_entry_armed = True
    setting.auto_exit_armed = True
    runtime.live_latch = True
    audit(db, "STRATEGY_EVALUATED", result="LIVE", reason="live trading armed for this process")
    return {"live_armed": True, "warning": "This mode can place real orders in the connected Zerodha account."}


@router.post("/trading/kill-switch")
def kill(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    setting = db.get(AppSetting, 1)
    setting.kill_switch = True
    setting.auto_entry_armed = False
    runtime.live_latch = False
    setting.live_armed = False
    audit(db, "KILL_SWITCH", result="ON", reason="new entries stopped")
    for position in db.scalars(select(Position).where(Position.status == "OPEN")).all():
        _exit_position_if_needed(runtime, db, setting, position, manual=True)
    return {"kill_switch": True}


@router.post("/trading/resume")
def resume(body: ModeBody, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    setting = db.get(AppSetting, 1)
    setting.kill_switch = False
    if setting.trading_mode == "LIVE" and body.confirmation != LIVE_PHRASE:
        raise HTTPException(status_code=400, detail=f'Type {LIVE_PHRASE} to resume live protection')
    if setting.trading_mode == "PAPER":
        setting.auto_entry_armed = True
        setting.auto_exit_armed = True
    if setting.trading_mode == "LIVE" and body.confirmation == LIVE_PHRASE:
        runtime.live_latch = True
        setting.live_armed = True
        setting.auto_entry_armed = True
        setting.auto_exit_armed = True
    return {"auto_entry_armed": setting.auto_entry_armed, "live_armed": runtime.live_latch}


@router.get("/positions")
def positions(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    rows = []
    for row in db.scalars(select(Position).order_by(Position.id.desc())).all():
        quote = runtime.cache.get(int(row.instrument_token))
        ltp = quote.get("ltp")
        pnl = None if ltp is None else (float(ltp) - float(row.entry_price)) * row.quantity
        rows.append(
            {
                "id": row.id,
                "mode": row.execution_mode,
                "status": row.status,
                "underlying": row.underlying,
                "symbol": row.tradingsymbol,
                "option_type": row.option_type,
                "strike": jsonable(row.strike),
                "entry_price": jsonable(row.entry_price),
                "exit_price": jsonable(row.exit_price),
                "ltp": ltp,
                "quantity": row.quantity,
                "pnl": pnl if row.status == "OPEN" else jsonable(row.realized_pnl),
                "stop_loss": jsonable(row.stop_loss),
                "target": jsonable(row.target),
                "exit_reason": row.exit_reason,
            }
        )
    return rows


@router.post("/positions/{position_id}/exit")
def exit_position(position_id: int, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    position = db.get(Position, position_id)
    if position is None or position.status != "OPEN":
        raise HTTPException(status_code=404, detail="Open position not found")
    setting = db.get(AppSetting, 1)
    reason = _exit_position_if_needed(runtime, db, setting, position, manual=True)
    audit(db, "MANUAL_EXIT", underlying=position.underlying, instrument=position.tradingsymbol, result=reason or "", reason="manual")
    return {"result": reason}


@router.get("/orders")
def orders(request: Request, db: Session = Depends(get_db)):
    require_db(request)
    rows = db.scalars(select(OrderRecord).order_by(OrderRecord.id.desc()).limit(100)).all()
    return [
        {
            "id": row.id,
            "mode": row.execution_mode,
            "side": row.side,
            "symbol": row.tradingsymbol,
            "quantity": row.quantity,
            "status": row.status,
            "price": jsonable(row.price),
            "reason": row.rejection_reason,
            "created_at": jsonable(row.created_at),
        }
        for row in rows
    ]


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    order = db.get(OrderRecord, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.execution_mode != "LIVE" or not order.broker_order_id:
        order.status = "CANCELLED"
        return {"status": order.status}
    if not runtime.live_latch:
        raise HTTPException(status_code=409, detail="Live trading is not armed in this process")
    token = load_access_token(db, get_settings())
    from app.adapters.kite.auth import kite_client
    from app.adapters.kite.orders import KiteOrderGateway

    KiteOrderGateway(kite_client(get_settings(), token)).cancel(order.broker_order_id)
    order.status = "CANCELLED"
    return {"status": order.status}


@router.get("/audit")
def audit_log(request: Request, event_type: str | None = None, db: Session = Depends(get_db)):
    require_db(request)
    query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(200)
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
    rows = db.scalars(query).all()
    return [
        {
            "id": row.id,
            "created_at": jsonable(row.created_at),
            "event_type": row.event_type,
            "underlying": row.underlying,
            "instrument": row.instrument,
            "result": row.result,
            "reason": row.reason,
        }
        for row in rows
    ]


@router.get("/paper-trading")
def paper_trading(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    from app.services.paper_engine import workspace

    return workspace(db, runtime)


@router.get("/paper-trading/trades/{trade_id}")
def paper_trade_detail(trade_id: int, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    from app.services.paper_engine import trade_detail

    payload = trade_detail(db, trade_id, runtime)
    if payload is None:
        raise HTTPException(status_code=404, detail="Paper trade not found")
    return payload


@router.get("/paper-trading/settings")
def get_paper_settings(request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.paper_engine import _config_dict, load_config

    return _config_dict(load_config(db))


@router.put("/paper-trading/settings")
def put_paper_settings(body: dict, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.paper_engine import _config_dict, save_config

    try:
        config = save_config(db, body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _config_dict(config)


class IndexPaperBody(BaseModel):
    instrument: str
    side: str


@router.post("/paper-trading/index")
def paper_index(body: IndexPaperBody, request: Request, db: Session = Depends(get_db)):
    """Paper BUY or SELL the front-month index future. Never reaches the broker."""
    runtime = require_db(request)
    from app.services.paper_engine import _trade_dict, open_index_paper

    try:
        trade = open_index_paper(db, runtime, body.instrument, body.side)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    db.commit()
    return {
        **_trade_dict(trade, float(trade.entry_price)),
        "note": "PAPER TRADING — NO REAL ORDERS. Index futures paper, not option premium.",
    }


@router.get("/chart")
def chart(
    request: Request,
    instrument: str = "NIFTY",
    timeframe: str = "1m",
    mode: str = "LIVE_PAPER",
    date: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    range: str = "1D",
    db: Session = Depends(get_db),
):
    runtime = require_db(request)
    from app.services.chart_service import chart_payload

    try:
        return chart_payload(
            db,
            runtime,
            instrument=instrument,
            timeframe=timeframe,
            mode=mode,
            trading_date=date,
            start_date=start,
            end_date=end,
            range_key=range,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/alerts")
def get_alerts(request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.alert_engine import workspace

    return workspace(db)


@router.put("/alerts/settings")
def put_alert_settings(body: dict, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.alert_engine import config_dict, save_config

    return config_dict(save_config(db, body))


@router.post("/alerts/{signal_id}/acknowledge")
def ack_alert(signal_id: int, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.alert_engine import acknowledge

    payload = acknowledge(db, signal_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return payload


@router.post("/alerts/{signal_id}/dismiss")
def dismiss_alert(signal_id: int, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.alert_engine import dismiss

    payload = dismiss(db, signal_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return payload


class HistoricalImportBody(BaseModel):
    instruments: str | list[str] = "BOTH"
    timeframe: str = "1Minute"
    mode: str = "INITIAL"
    date_from: str | None = None
    date_to: str | None = None


class HistoricalPaperBody(BaseModel):
    instrument: str = "NIFTY"
    date_from: str | None = None
    date_to: str | None = None
    date: str | None = None
    timeframe: str = "1Minute"
    threshold: float = 75
    initial_capital: float = 100000
    strike_range: int = 5
    strategy: dict | None = None
    sweep: dict | None = None


@router.get("/historical-data")
def historical_data(request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.historical_import import workspace

    return workspace(db)


@router.post("/historical-data/import")
def historical_import(body: HistoricalImportBody, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.historical_import import job_payload, queue_import

    try:
        job = queue_import(
            db,
            instruments=body.instruments,
            timeframe=body.timeframe,
            mode=body.mode,
            date_from=_optional_date(body.date_from),
            date_to=_optional_date(body.date_to),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return job_payload(job)


@router.get("/historical-data/jobs/{job_id}")
def historical_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.db.models import HistoricalImportJob
    from app.services.historical_import import job_payload

    row = db.get(HistoricalImportJob, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Import job not found")
    return job_payload(row)


@router.post("/historical-data/jobs/{job_id}/resume")
def historical_resume(job_id: int, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.historical_import import job_payload, resume_import

    try:
        job = resume_import(db, job_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return job_payload(job)


@router.get("/historical-paper")
def historical_paper_home(request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.db.models import HistoricalPaperRun
    from app.services.historical_repo import stored_coverage

    coverage = stored_coverage(db)
    usable = next((row for row in coverage if row["available"]), None)
    runs = list(db.scalars(select(HistoricalPaperRun).order_by(HistoricalPaperRun.id.desc()).limit(12)).all())
    return {
        "default_from": (usable or {}).get("from_date"),
        "default_to": (usable or {}).get("to_date"),
        "coverage": coverage,
        "note": "PAPER SIMULATION — NO REAL ORDERS. When CE/PE history is missing, analysis and paper trades use NIFTY and SENSEX index closes (BUY = long, SELL = short).",
        "runs": [
            {
                "id": row.id,
                "status": row.status,
                "instrument": row.instrument,
                "from_date": row.date_from.isoformat(),
                "to_date": row.date_to.isoformat(),
                "net_pnl": float(row.net_pnl),
                "total_trades": row.total_trades,
                "option_data_available": row.option_data_available,
            }
            for row in runs
        ],
    }


@router.post("/historical-paper/runs")
def historical_paper_start(body: HistoricalPaperBody, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.services.historical_backtest import queue_run, run_payload, stored_walk_cover

    day = _optional_date(body.date)
    start = _optional_date(body.date_from) or day
    end = _optional_date(body.date_to) or day
    if start is None or end is None:
        cover = stored_walk_cover(db, "NIFTY" if body.instrument.upper() == "BOTH" else body.instrument.upper(), body.timeframe or "1Minute")
        if cover is None:
            raise HTTPException(status_code=400, detail="No historical data stored. Import a range first.")
        start = start or cover[0]
        end = end or cover[1]
    if end < start:
        raise HTTPException(status_code=400, detail="To date is before from date")
    try:
        run = queue_run(
            db,
            instrument=body.instrument,
            date_from=start,
            date_to=end,
            threshold=body.threshold,
            initial_capital=body.initial_capital,
            strike_range=body.strike_range,
            timeframe=body.timeframe,
            strategy=body.strategy,
            sweep=body.sweep,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return run_payload(db, run)


@router.get("/historical-paper/runs/{run_id}")
def historical_paper_get(run_id: int, request: Request, db: Session = Depends(get_db)):
    require_db(request)
    from app.db.models import HistoricalPaperRun
    from app.services.historical_backtest import run_payload

    row = db.get(HistoricalPaperRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return run_payload(db, row)


def _optional_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must look like YYYY-MM-DD") from None


@router.get("/calendar-patterns")
def calendar_patterns(
    request: Request,
    instrument: str = "BOTH",
    date_from: str | None = None,
    date_to: str | None = None,
    timeframe: str = "1Minute",
    weekday: str | None = None,
    week_of_month: str | None = None,
    weekday_ordinal: str | None = None,
    month: str | None = None,
    quarter: str | None = None,
    expiry_proximity: str | None = None,
    regime: str | None = None,
    min_sample: int = 20,
    db: Session = Depends(get_db),
):
    require_db(request)
    from app.services.calendar_analyzer import analyze_calendar

    try:
        return analyze_calendar(
            db,
            instrument=instrument,
            date_from=_optional_date(date_from),
            date_to=_optional_date(date_to),
            timeframe=timeframe,
            weekday=weekday,
            week_of_month=week_of_month,
            weekday_ordinal=weekday_ordinal,
            month=month,
            quarter=quarter,
            expiry_proximity=expiry_proximity,
            regime=regime,
            min_sample=min_sample,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.websocket("/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    runtime = websocket.app.state.runtime
    try:
        while True:
            if not runtime.db_ok:
                await websocket.send_json({"error": "database down"})
            else:
                db = session_factory()()
                try:
                    await websocket.send_json(dashboard(db, runtime))
                    db.commit()
                finally:
                    db.close()
            await asyncio_sleep()
    except WebSocketDisconnect:
        return


async def asyncio_sleep():
    import asyncio

    await asyncio.sleep(2)

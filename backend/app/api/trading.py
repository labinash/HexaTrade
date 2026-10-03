"""Chart / Zerodha trading API. Kite calls stay on the backend."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fastapi.responses import RedirectResponse

from app.adapters.kite.auth import login_url
from app.api.routes import get_db, require_db
from app.core.config import get_settings
from app.domain.execution import OrderRefused
from app.services import chart_trading as trading

router = APIRouter(prefix="/api")


class LiveToggleBody(BaseModel):
    enabled: bool
    confirmation: str = ""


class ChartOrderBody(BaseModel):
    underlying: str
    optionType: str
    moneyness: str = "ATM"
    transactionType: str = "BUY"
    lots: int = 1
    quantity: int | None = None
    orderType: str = "MARKET"
    product: str = "MIS"
    variety: str = "regular"
    strategy: str = "CHART_SIGNAL"
    signal: str | None = None
    strike: float | None = None
    tradingsymbol: str | None = None
    exchange: str | None = None
    ltp: float | None = None
    clientOrderId: str
    liveConfirmation: str = ""
    acknowledge: bool = False


def _refuse(exc: OrderRefused) -> None:
    reason = exc.reason or str(exc)
    code = 409 if "BLOCKED" in reason or "Confirm" in reason or "lot" in reason.lower() else 400
    if "Connect Zerodha" in reason or "session" in reason.lower():
        code = 401
    raise HTTPException(status_code=code, detail=reason) from exc


@router.get("/trading/live-settings")
def live_settings(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    return trading.settings_payload(db, runtime)


@router.put("/trading/live-settings")
def put_live_settings(body: LiveToggleBody, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    try:
        return trading.set_live_enabled(db, runtime, enabled=body.enabled, confirmation=body.confirmation)
    except OrderRefused as exc:
        _refuse(exc)


@router.get("/trading/chart-strikes")
def chart_strikes(request: Request, instrument: str = "NIFTY", db: Session = Depends(get_db)):
    runtime = require_db(request)
    try:
        return trading.chart_strikes(db, runtime, instrument)
    except OrderRefused as exc:
        _refuse(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/trading/zerodha/preview")
def preview_order(body: ChartOrderBody, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    try:
        return trading.preview_order(db, runtime, body.model_dump())
    except OrderRefused as exc:
        _refuse(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/trading/zerodha/order")
def place_order(body: ChartOrderBody, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    try:
        return trading.place_order(db, runtime, body.model_dump())
    except OrderRefused as exc:
        _refuse(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/trading/zerodha/positions")
def positions(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    snapshot = trading.sync_broker(db, runtime, force=False)
    books = snapshot.get("positions") or {}
    return {
        "kind": books.get("kind") or "net",
        "net": books.get("net") or [],
        "day": books.get("day") or [],
        "open_pnl": snapshot.get("open_pnl"),
        "badge": "LIVE ZERODHA",
        "note": snapshot.get("note"),
        "error": snapshot.get("error") or "",
        "ok": snapshot.get("ok"),
        "fetched_at": snapshot.get("fetched_at"),
    }


@router.get("/trading/zerodha/orders")
def orders(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    snapshot = trading.sync_broker(db, runtime, force=False)
    return {
        "kind": "orders",
        "orders": snapshot.get("orders") or [],
        "badge": "LIVE ZERODHA",
        "error": snapshot.get("error") or "",
        "fetched_at": snapshot.get("fetched_at"),
    }


@router.get("/trading/zerodha/trades")
def trades(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    snapshot = trading.sync_broker(db, runtime, force=False)
    return {
        "kind": "trades",
        "trades": snapshot.get("trades") or [],
        "badge": "LIVE ZERODHA",
        "error": snapshot.get("error") or "",
        "fetched_at": snapshot.get("fetched_at"),
    }


@router.post("/trading/zerodha/sync")
def sync(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    return trading.sync_broker(db, runtime, force=True)


@router.get("/trading/zerodha/snapshot")
def snapshot(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    data = trading.sync_broker(db, runtime, force=False)
    data["settings"] = trading.settings_payload(db, runtime)
    return data


@router.get("/trading/zerodha/login")
def zerodha_login(request: Request):
    require_db(request)
    try:
        return {"url": login_url(get_settings())}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/trading/zerodha/callback")
def zerodha_callback(request: Request):
    """Reuse the existing broker callback so tokens stay on the backend."""
    query = request.url.query
    target = f"/api/broker/callback?{query}" if query else "/api/broker/callback"
    return RedirectResponse(target)


@router.get("/trading/zerodha/profile")
def zerodha_profile(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    try:
        return trading.broker_profile(db, runtime)
    except OrderRefused as exc:
        _refuse(exc)


@router.get("/trading/zerodha/instruments")
def zerodha_instruments(request: Request, underlying: str = "NIFTY", db: Session = Depends(get_db)):
    require_db(request)
    try:
        return trading.listed_instruments(db, underlying)
    except OrderRefused as exc:
        _refuse(exc)


@router.get("/trading/zerodha/order/{order_id}")
def zerodha_order(order_id: str, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    try:
        return trading.order_status(db, runtime, order_id)
    except OrderRefused as exc:
        _refuse(exc)


@router.post("/trading/zerodha/order/{order_id}/cancel")
def zerodha_cancel(order_id: str, request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    try:
        return trading.cancel_live_order(db, runtime, order_id)
    except OrderRefused as exc:
        _refuse(exc)


@router.get("/trading/dashboard-brief")
def dashboard_brief(request: Request, db: Session = Depends(get_db)):
    runtime = require_db(request)
    return trading.dashboard_brief(db, runtime)

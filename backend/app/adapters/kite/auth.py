import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import as_utc_naive, is_expired, now_utc, session_expiry, within_minutes_of_expiry
from app.core.config import Settings
from app.core.security import decrypt_token, encrypt_token
from app.db.models import BrokerSession

log = logging.getLogger("hextrade.kite.auth")


def login_url(settings: Settings) -> str:
    if not settings.kite_api_key:
        raise ValueError("KITE_API_KEY is not configured")
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=settings.kite_api_key)
    return kite.login_url()


def exchange_request_token(settings: Settings, request_token: str) -> dict:
    if not settings.kite_api_secret:
        raise ValueError("KITE_API_SECRET is not configured")
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=settings.kite_api_key)
    session = kite.generate_session(request_token, api_secret=settings.kite_api_secret)
    return {"user_id": session["user_id"], "access_token": session["access_token"]}


def store_session(db: Session, settings: Settings, user_id: str, access_token: str, login_at: datetime | None = None) -> BrokerSession:
    encrypted = encrypt_token(settings.app_master_key, access_token)
    expires = session_expiry(login_at or now_utc())
    existing = db.scalars(select(BrokerSession)).all()
    for row in existing:
        db.delete(row)
    row = BrokerSession(
        user_id=user_id,
        access_token_encrypted=encrypted,
        expires_at=as_utc_naive(expires),
        created_at=as_utc_naive(now_utc()),
    )
    db.add(row)
    db.commit()
    return row


def current_session(db: Session) -> BrokerSession | None:
    return db.scalar(select(BrokerSession).order_by(BrokerSession.id.desc()))


def session_status(row: BrokerSession | None) -> str:
    if row is None:
        return "disconnected"
    expires = row.expires_at.replace(tzinfo=None)
    from datetime import timezone

    aware = expires.replace(tzinfo=timezone.utc)
    if is_expired(aware):
        return "expired"
    if within_minutes_of_expiry(aware):
        return "expiring"
    return "connected"


def load_access_token(db: Session, settings: Settings) -> str | None:
    row = current_session(db)
    if row is None or session_status(row) == "expired":
        return None
    return decrypt_token(settings.app_master_key, row.access_token_encrypted)


def clear_session(db: Session) -> None:
    for row in db.scalars(select(BrokerSession)).all():
        db.delete(row)
    db.commit()


def kite_client(settings: Settings, access_token: str):
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=settings.kite_api_key)
    kite.set_access_token(access_token)
    return kite

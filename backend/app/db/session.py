import logging

import pyodbc
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings

log = logging.getLogger("hextrade.db")

_engine = None
_Session = None
_driver: str | None = None


def pick_driver(preferred: str = "") -> str:
    installed = set(pyodbc.drivers())
    if preferred and preferred in installed:
        return preferred
    for name in (
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ):
        if name in installed:
            return name
    raise RuntimeError("No SQL Server ODBC driver is installed")


def _safe_database_name(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError("SQLSERVER_DATABASE must be alphanumeric")
    return name


def ensure_database(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    driver = pick_driver(settings.sqlserver_odbc_driver)
    if settings.sqlserver_connection_string.strip():
        return driver
    database = _safe_database_name(settings.sqlserver_database)
    master = create_engine(settings.sqlalchemy_url(driver, "master"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with master.connect() as conn:
        conn.execute(
            text(f"IF DB_ID(N'{database}') IS NULL CREATE DATABASE [{database}]")
        )
    master.dispose()
    return driver


def init_engine(settings: Settings | None = None):
    global _engine, _Session, _driver
    settings = settings or get_settings()
    _driver = ensure_database(settings)
    _engine = create_engine(
        settings.sqlalchemy_url(_driver),
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=20,
        pool_recycle=1800,
        future=True,
    )
    _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    log.info("SQL Server connected with %s", _driver)
    return _engine


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _Session is None:
        init_engine()
    return _Session


def get_db():
    factory = session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()

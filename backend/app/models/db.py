"""Database engine and session management (PostgreSQL/Neon or SQLite).

Remote databases such as Neon add a network round trip to every statement, so
the engine avoids avoidable ones: no ping on every checkout (only after the
connection sat idle), and read-only API requests run in autocommit mode (no
BEGIN / ROLLBACK round trips).
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, exc
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_is_sqlite = _settings.database_url.startswith("sqlite")

# Idle connections may have been closed server-side (e.g. Neon auto-suspend); re-check them.
_IDLE_PING_SECONDS = 60

if _is_sqlite:
    engine = create_engine(_settings.database_url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    engine = create_engine(
        _settings.database_url,
        # Server-side prepared statements do not mix with transaction-pooling proxies (Neon pooler / PgBouncer).
        connect_args={"prepare_threshold": None},
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
    )

    @event.listens_for(engine, "checkin")
    def _mark_idle(_dbapi_connection, record):  # pragma: no cover - driver hook
        record.info["idle_since"] = time.monotonic()

    @event.listens_for(engine, "checkout")
    def _ping_if_idle(dbapi_connection, record, _proxy):  # pragma: no cover - driver hook
        idle_since = record.info.get("idle_since")
        if idle_since is None or time.monotonic() - idle_since < _IDLE_PING_SECONDS:
            return
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
        except Exception as error:  # stale connection: let the pool open a new one
            raise exc.DisconnectionError() from error


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
ReadSessionLocal = sessionmaker(
    bind=engine.execution_options(isolation_level="AUTOCOMMIT"), autoflush=False, expire_on_commit=False
)


def init_db() -> None:
    from app.models import entities  # noqa: F401  (register models)

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency for endpoints that write."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_db() -> Iterator[Session]:
    """FastAPI dependency for read-only endpoints (autocommit: no transaction round trips)."""
    db = ReadSessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background work."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

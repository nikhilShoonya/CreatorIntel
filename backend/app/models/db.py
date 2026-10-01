"""Database engine and session management (PostgreSQL/Neon or SQLite).

Remote databases such as Neon add a network round trip to every statement, so
the engine avoids avoidable ones: no ping on every checkout (only after the
connection sat idle), and read-only API requests run in autocommit mode (no
BEGIN / ROLLBACK round trips).
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, exc
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import get_settings


class Base(DeclarativeBase):
    pass


_BACKEND_DIR = Path(__file__).resolve().parents[2]
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
        # Opening a connection to a remote database costs several round trips (TLS); keep enough of them
        # open for the parallel dashboard queries so pages never wait for a new connection.
        pool_size=_settings.db_pool_size,
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
            # The ping must not leave a transaction open: read-only sessions switch the connection to
            # autocommit, which the driver refuses while a transaction is in progress.
            if not dbapi_connection.autocommit:
                dbapi_connection.rollback()
        except Exception as error:  # stale connection: let the pool open a new one
            raise exc.DisconnectionError() from error


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
ReadSessionLocal = sessionmaker(
    bind=engine.execution_options(isolation_level="AUTOCOMMIT"), autoflush=False, expire_on_commit=False
)


BASELINE_REVISION = "0001_baseline"
_BASELINE_TABLES = {
    "creators", "uploads", "upload_items", "video_tracking_uploads", "video_tracking_creators",
    "video_tracking_videos", "video_tracking_view_history", "video_tracking_sentiment", "video_tracking_jobs",
}


def init_db() -> None:
    """Bring the database schema up to date with Alembic migrations (runs at startup)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from app.models import entities  # noqa: F401  (register models)
    from app.models import usage as _usage  # noqa: F401
    from app.video_performance import models as _vt  # noqa: F401

    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        existing = set(inspect(connection).get_table_names())
        if "alembic_version" not in existing and existing & _BASELINE_TABLES:
            # Database created before migrations existed: add any missing baseline tables, then mark it as baseline.
            missing = [t for t in Base.metadata.sorted_tables if t.name in _BASELINE_TABLES - existing]
            if missing:
                Base.metadata.create_all(connection, tables=missing)
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")


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


def warm_pool() -> int:
    """Open the pool's connections up front so the first page loads don't pay the connection set-up."""
    if _is_sqlite:
        return 0
    connections = []
    try:
        for _ in range(_settings.db_pool_size):
            connections.append(engine.connect())
        for connection in connections:
            connection.exec_driver_sql("SELECT 1")
            connection.rollback()
        return len(connections)
    finally:
        for connection in connections:
            connection.close()


def ping_database() -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
        connection.rollback()


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

"""Alembic environment: uses the app's own engine/models (no separate URL configuration)."""

from alembic import context

from app.models.db import Base, engine
from app.models import entities  # noqa: F401  (register Creator Analytics tables)
from app.models import usage  # noqa: F401  (register shared api_usage table)
from app.video_performance import models as video_tracking_models  # noqa: F401  (register video_tracking_* tables)

target_metadata = Base.metadata


def run_migrations() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:  # called programmatically at startup
        _run(connection)
        return
    with engine.begin() as conn:
        _run(conn)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite needs batch mode for ALTERs
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations()

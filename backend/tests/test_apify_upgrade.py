"""Upgrade installed 0006 databases, including the schema shipped before its last column was added."""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.models.db import Base
from app.video_performance.models import VtApifyRun, VtVideo


@pytest.mark.parametrize("missing_column", [True, False])
def test_upgrade_installed_0006_preserves_existing_provider_work(tmp_path, missing_column):
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    engine = create_engine(f"sqlite:///{(tmp_path / 'installed.db').as_posix()}")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0006_apify_reels")
            if missing_column:
                connection.exec_driver_sql("ALTER TABLE video_tracking_apify_work DROP COLUMN finished_at")
            vid = connection.execute(VtVideo.__table__.insert().values(
                platform="instagram", video_identifier="UpgradeReel01", video_url="https://instagram.com/reel/UpgradeReel01/",
            )).inserted_primary_key[0]
            connection.execute(VtApifyRun.__table__.insert().values(
                id="installed-job", remote_run_id="existing-remote", actor_id="apify/instagram-reel-scraper",
                month="2026-10", inputs=[{"id": vid, "identifier": "UpgradeReel01"}], budget_units=1, status="running",
            ))
            connection.execute(text("INSERT INTO video_tracking_apify_work (video_id, run_id) VALUES (:vid, :run)"),
                               {"vid": vid, "run": "installed-job"})
            command.upgrade(config, "head")
            assert "finished_at" in {c["name"] for c in inspect(connection).get_columns("video_tracking_apify_work")}
            assert connection.execute(text("SELECT run_id, finished_at FROM video_tracking_apify_work")).one() == ("installed-job", None)
            assert connection.execute(text("SELECT remote_run_id, budget_units FROM video_tracking_apify_runs")).one() == ("existing-remote", 1)
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        engine.dispose()

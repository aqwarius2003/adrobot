from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from src.core.migrations import migrate_database


def test_pin_migration_preserves_existing_draft_and_archive(tmp_path):
    url = f"sqlite:///{tmp_path / 'existing.db'}"
    engine = create_engine(url)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes.update(database_url=url, skip_logging=True)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "a1b2c3d4e5f6")
        connection.execute(
            text("""
            INSERT INTO campaigns (id, remote_campaign_id, name, alias, created_at, updated_at)
            VALUES (1, 100, 'Existing', 'existing', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        )
        connection.execute(
            text("""
            INSERT INTO streams (id, campaign_id, remote_stream_id, name, position,
                is_managed, stream_type, stream_schema, revision, sync_status, created_at, updated_at)
            VALUES (1, 1, 1002, 'Flow', 1, 1, 'default', 'landings', 7, 'dirty',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        )
        connection.execute(
            text("""
            INSERT INTO offers (id, remote_offer_id, name, is_available, updated_at)
            VALUES (1, 501, 'Pinned', 1, CURRENT_TIMESTAMP),
                (2, 502, 'Archived', 1, CURRENT_TIMESTAMP)
        """)
        )
        connection.execute(
            text("""
            INSERT INTO stream_offers (id, stream_id, offer_id, position, baseline_share,
                baseline_state, baseline_pinned, draft_share, draft_state, draft_pinned,
                archive_reason, has_been_published)
            VALUES (1, 1, 1, 1, 50, 'active', 0, 100, 'active', 1, NULL, 1),
                (2, 1, 2, 2, 50, 'active', 1, 0, 'removed', 0, 'local_remove', 1)
        """)
        )
    try:
        for _ in range(2):
            migrate_database(url)
            with engine.connect() as connection:
                rows = connection.execute(
                    text("""
                    SELECT baseline_share, baseline_state, baseline_pinned, draft_share,
                        draft_state, draft_pinned, archive_reason, has_been_published,
                        draft_auto_pinned FROM stream_offers ORDER BY id
                """)
                ).all()
                assert rows == [
                    (50, "active", 0, 100, "active", 1, None, 1, 0),
                    (50, "active", 1, 0, "removed", 0, "local_remove", 1, 0),
                ]
                assert connection.execute(
                    text("SELECT revision, sync_status FROM streams")
                ).one() == (7, "dirty")
    finally:
        engine.dispose()

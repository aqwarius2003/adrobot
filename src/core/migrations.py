"""Apply the existing Alembic schema without changing application logging."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy.engine import Connection

from alembic import command


def migrate_database(database_url: str, *, connection: Connection | None = None) -> None:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["database_url"] = database_url
    config.attributes["skip_logging"] = True
    if connection is not None:
        config.attributes["connection"] = connection
    command.upgrade(config, "head")

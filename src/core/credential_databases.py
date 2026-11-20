"""Separate real drafts and operations for each authenticated tracker credential."""

import re
from functools import lru_cache
from pathlib import Path
from threading import RLock

from alembic.config import Config
from sqlalchemy.engine import make_url

from alembic import command
from src.core.database import _create_engine_and_session

_initialization_lock = RLock()


@lru_cache(maxsize=32)
def _open_scoped_database(database_url: str):
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["database_url"] = database_url
    config.attributes["skip_logging"] = True
    command.upgrade(config, "head")
    engine, factory = _create_engine_and_session(database_url)
    # The scope is reopened lazily after restart, so recover its expired leases here too.
    from src.main import recover_interrupted_streams

    recover_interrupted_streams(engine)
    return factory


def get_credential_database(base_url: str, scope: str):
    if not re.fullmatch(r"[0-9a-f]{64}", scope):
        raise ValueError("Invalid credential scope")
    url = make_url(base_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("Credential-isolated real mode requires a file-based SQLite database")
    directory = Path(url.database).resolve().parent / "real_keys"
    directory.mkdir(parents=True, exist_ok=True)
    scoped_url = url.set(database=str(directory / f"{scope}.db")).render_as_string(
        hide_password=False
    )
    with _initialization_lock:
        return scoped_url, _open_scoped_database(scoped_url)

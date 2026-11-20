import hashlib
import os

import pytest
from sqlalchemy import text

from src.core.database import (
    _create_engine_and_session,
    demo_engine,
    is_safe_test_db,
    real_engine,
)
from src.simulator.storage import simulator_storage


def _file_hash(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_test_engines_point_to_temporary_directory():
    """Verify that real_engine and demo_engine connect to safe temporary files."""
    real_url = str(real_engine.url)
    demo_url = str(demo_engine.url)

    assert is_safe_test_db(real_url)
    assert is_safe_test_db(demo_url)
    assert "data/app.db" not in real_url.replace("\\", "/")
    assert "data/demo_app.db" not in demo_url.replace("\\", "/")


def test_simulator_storage_isolated_to_temp_file():
    """Verify that simulator_storage does not point to data/simulator_state.json."""
    assert simulator_storage.state_file is not None
    assert is_safe_test_db(simulator_storage.state_file)
    assert "data/simulator_state.json" not in simulator_storage.state_file.replace("\\", "/")


def test_destructive_ddl_blocked_on_non_temp_database():
    """Verify that DROP TABLE on a non-temporary database engine raises RuntimeError."""
    non_temp_file = os.path.join(os.getcwd(), "data", "fake_non_temp_control.db")
    non_temp_url = f"sqlite:///{non_temp_file}"

    eng, _ = _create_engine_and_session(non_temp_url)
    try:
        # Creating tables should work
        with eng.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS test_guard (id INTEGER PRIMARY KEY)"))
            conn.commit()

        # Dropping table MUST be blocked by guard_destructive_ddl
        with pytest.raises(
            RuntimeError, match="Destructive operation blocked on non-temporary runtime database"
        ):
            with eng.connect() as conn:
                conn.execute(text("DROP TABLE test_guard"))
                conn.commit()
    finally:
        eng.dispose()
        if os.path.exists(non_temp_file):
            try:
                os.remove(non_temp_file)
            except Exception:
                pass


def test_runtime_data_files_preserved_after_tests(runtime_file_hashes):
    """All pre-existing runtime files retain the hashes captured before collection."""
    for path, original_hash in runtime_file_hashes.items():
        assert _file_hash(path) == original_hash, f"Runtime file changed during tests: {path}"


def test_canary_database_records_strictly_preserved():
    """Verify record-level data preservation in a canary SQLite database with WAL and checkpoints."""
    from sqlalchemy import Column, Integer, MetaData, String, Table, select

    canary_db_path = os.path.join(os.getcwd(), "data", "canary_workspace_control.db")
    canary_url = f"sqlite:///{canary_db_path}"

    engine, _ = _create_engine_and_session(canary_url)
    try:
        meta = MetaData()
        test_table = Table(
            "canary_data",
            meta,
            Column("id", Integer, primary_key=True),
            Column("marker", String(64)),
            Column("payload", String(255)),
        )
        meta.create_all(engine)

        # Insert 10 canary records
        with engine.connect() as conn:
            for i in range(10):
                conn.execute(
                    test_table.insert().values(
                        id=i + 1, marker=f"CANARY_MARKER_{i}", payload=f"VAL_{i * 42}"
                    )
                )
            conn.commit()
            # Ensure WAL checkpoint
            conn.execute(text("PRAGMA wal_checkpoint(FULL)"))

        # Compute pre-test record state
        with engine.connect() as conn:
            pre_records = conn.execute(select(test_table).order_by(test_table.c.id)).fetchall()

        assert len(pre_records) == 10
        pre_signature = hashlib.sha256(str(pre_records).encode("utf-8")).hexdigest()

        # Now verify that guard_destructive_ddl prevents DROP TABLE on this non-temp canary DB
        with pytest.raises(
            RuntimeError, match="Destructive operation blocked on non-temporary runtime database"
        ):
            with engine.connect() as conn:
                conn.execute(text("DROP TABLE canary_data"))

        # Post-check: ensure zero records were dropped or modified
        with engine.connect() as conn:
            post_records = conn.execute(select(test_table).order_by(test_table.c.id)).fetchall()

        post_signature = hashlib.sha256(str(post_records).encode("utf-8")).hexdigest()
        assert pre_signature == post_signature, "Canary records were altered or lost!"
        assert len(post_records) == 10
    finally:
        engine.dispose()
        if os.path.exists(canary_db_path):
            try:
                os.remove(canary_db_path)
            except Exception:
                pass

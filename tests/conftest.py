import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Compare with this run's starting files, not a historical manual backup.
_RUNTIME_HASHES = {
    path: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (Path(__file__).resolve().parents[1] / "data").rglob("*")
    if path.is_file()
}

# 1. Initialize safe isolated test environment BEFORE importing app code with side-effects
_TEST_TEMP_DIR = tempfile.mkdtemp(prefix="adrobot_pytest_")
_TEST_REAL_DB = os.path.join(_TEST_TEMP_DIR, "test_real.db")
_TEST_DEMO_DB = os.path.join(_TEST_TEMP_DIR, "test_demo.db")
_TEST_SEC_DB = os.path.join(_TEST_TEMP_DIR, "test_security.db")
_TEST_SIM_STATE = os.path.join(_TEST_TEMP_DIR, "test_simulator_state.json")

os.environ["ADROBOT_TESTING"] = "1"
os.environ["ADROBOT_TEST_DIR"] = _TEST_TEMP_DIR
os.environ["APP_ENV"] = "development"
# The regression suite explicitly exercises both connections. Default-launch
# acceptance tests run in separate processes with optional tools disabled.
os.environ["DEMO_ENABLED"] = "true"
os.environ["KT_MODE"] = "real"
os.environ["DEMO_SIMULATOR_URL"] = "http://simulator.test:8001"
os.environ["REAL_DATABASE_URL"] = f"sqlite:///{_TEST_REAL_DB}"
os.environ["DEMO_DATABASE_URL"] = f"sqlite:///{_TEST_DEMO_DB}"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_REAL_DB}"
os.environ["SECURITY_DATABASE_URL"] = f"sqlite:///{_TEST_SEC_DB}"
os.environ["SIMULATOR_STATE_FILE"] = _TEST_SIM_STATE

from src.core.config import settings  # noqa: E402

settings.REAL_DATABASE_URL = os.environ["REAL_DATABASE_URL"]
settings.DEMO_DATABASE_URL = os.environ["DEMO_DATABASE_URL"]
settings.DATABASE_URL = os.environ["DATABASE_URL"]
settings.APP_ENV = "development"
if hasattr(settings, "SECURITY_DATABASE_URL"):
    settings.SECURITY_DATABASE_URL = os.environ["SECURITY_DATABASE_URL"]

from src.core.database import configure_engines  # noqa: E402
from src.simulator.storage import simulator_storage  # noqa: E402

# Ensure runtime engines point to safe temporary test databases
configure_engines(
    real_url=os.environ["REAL_DATABASE_URL"],
    demo_url=os.environ["DEMO_DATABASE_URL"],
)

# Isolate simulator storage
simulator_storage.state_file = _TEST_SIM_STATE
simulator_storage.reset()


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_temp_dir():
    """Ensure test databases and temporary simulator state are cleaned up on session end."""
    yield
    for path, original_hash in _RUNTIME_HASHES.items():
        assert path.exists(), f"Runtime file was removed by tests: {path}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash, (
            f"Runtime file was modified by tests: {path}"
        )
    try:
        shutil.rmtree(_TEST_TEMP_DIR, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def test_temp_dir() -> str:
    return _TEST_TEMP_DIR


@pytest.fixture(scope="session")
def runtime_file_hashes():
    return _RUNTIME_HASHES

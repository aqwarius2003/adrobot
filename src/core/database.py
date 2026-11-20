import os
import tempfile
import urllib.parse
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.core.config import settings


class Base(DeclarativeBase):
    pass


def is_safe_test_db(db_url: str) -> bool:
    """Check if the database URL points to a temporary/in-memory test database."""
    if not db_url or ":memory:" in db_url:
        return True
    clean_url = urllib.parse.unquote(db_url)
    path = clean_url.replace("sqlite:///", "")
    norm_path = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    temp_dir = os.path.normcase(os.path.normpath(os.path.abspath(tempfile.gettempdir())))
    test_dir = os.environ.get("ADROBOT_TEST_DIR")
    if test_dir and norm_path.startswith(
        os.path.normcase(os.path.normpath(os.path.abspath(test_dir)))
    ):
        return True
    if norm_path.startswith(temp_dir):
        return True
    return False


def _create_engine_and_session(db_url: str) -> tuple[Any, sessionmaker[Session]]:
    # Ensure data directory exists if using sqlite file
    if db_url.startswith("sqlite"):
        path = db_url.replace("sqlite:///", "")
        if path and not path.startswith(":memory:"):
            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

    connect_args = {}
    engine_kwargs: dict = {"future": True, "pool_pre_ping": True}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in db_url:
            from sqlalchemy.pool import StaticPool

            engine_kwargs["poolclass"] = StaticPool

    eng = create_engine(
        db_url,
        connect_args=connect_args,
        **engine_kwargs,
    )

    if db_url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        if not is_safe_test_db(db_url):

            @event.listens_for(eng, "before_cursor_execute")
            def guard_destructive_ddl(conn, cursor, statement, parameters, context, executemany):
                stmt_upper = statement.strip().upper()
                if stmt_upper.startswith("DROP TABLE") or stmt_upper.startswith("DROP VIEW"):
                    raise RuntimeError(
                        f"Destructive operation blocked on non-temporary runtime database '{db_url}': '{statement}'"
                    )

    factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=eng,
        expire_on_commit=False,
    )
    return eng, factory


# Two strictly isolated engines and session factories
real_engine, RealSessionLocal = _create_engine_and_session(settings.get_real_database_url())
demo_engine, DemoSessionLocal = _create_engine_and_session(settings.get_demo_database_url())


def configure_engines(real_url: str | None = None, demo_url: str | None = None) -> None:
    """Rebind engines and session factories, e.g. for testing environments."""
    global real_engine, demo_engine, RealSessionLocal, DemoSessionLocal
    global engine, SessionLocal

    r_url = real_url or settings.get_real_database_url()
    d_url = demo_url or settings.get_demo_database_url()

    real_engine, RealSessionLocal = _create_engine_and_session(r_url)
    demo_engine, DemoSessionLocal = _create_engine_and_session(d_url)

    engine = demo_engine if settings.is_demo_mode else real_engine
    SessionLocal = DemoSessionLocal if settings.is_demo_mode else RealSessionLocal


def get_session_factory(mode: str) -> sessionmaker[Session]:
    clean = (mode or "").strip().lower()
    if clean == "demo":
        return DemoSessionLocal
    return RealSessionLocal


def init_db(mode: str | None = None) -> None:
    """Initialize database tables for real, demo, or both."""
    import src.domain.models  # noqa: F401

    if mode == "real":
        Base.metadata.create_all(bind=real_engine)
    elif mode == "demo":
        Base.metadata.create_all(bind=demo_engine)
    else:
        Base.metadata.create_all(bind=real_engine)
        Base.metadata.create_all(bind=demo_engine)


# Legacy bindings for backward compatibility with single-engine helpers/tests
engine = demo_engine if settings.is_demo_mode else real_engine
SessionLocal = DemoSessionLocal if settings.is_demo_mode else RealSessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

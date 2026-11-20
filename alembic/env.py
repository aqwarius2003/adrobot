from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

# Import domain models here so Alembic detects all tables
import src.domain.models  # noqa: F401
from alembic import context
from src.core.config import settings
from src.core.database import Base

config = context.config

if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override URL from application settings based on mode (real, demo, or security)
mode = context.get_x_argument(as_dictionary=True).get("mode", settings.KT_MODE)
if mode == "demo":
    target_url = settings.get_demo_database_url()
elif mode == "security":
    target_url = settings.get_security_database_url()
else:
    target_url = settings.get_real_database_url()

target_url = config.attributes.get("database_url", target_url)
config.set_main_option("sqlalchemy.url", target_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

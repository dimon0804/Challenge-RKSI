import os
import logging
import sys
from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config

DATABASE_URL = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = None

# Пропускаем настройку логгера через fileConfig, чтобы не требовать полную ini-конфигурацию логов
# logging.basicConfig(level=logging.INFO)

# Добавляем корень проекта (/app) в PYTHONPATH, чтобы работал import app.models.*
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.models.base import Base  # type: ignore
from app.models.user import User  # noqa: F401
from app.models.api_key import ApiKey  # noqa: F401
from app.models.challenge import Challenge  # noqa: F401
from app.models.endpoint import Endpoint  # noqa: F401
from app.models.attempt import Attempt  # noqa: F401
from app.models.log import LogEntry  # noqa: F401

target_metadata = Base.metadata

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

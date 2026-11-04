from __future__ import annotations

import asyncio
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings
from app.db import models  # noqa: F401  - регистрирует таблицы в metadata
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

ASYNC_DRIVERS = ("+asyncpg", "+aiosqlite", "+asyncmy", "+aiomysql")


def _database_url() -> str:
    """URL, переданный программно (тесты/CI), иначе — из настроек приложения."""
    url = config.get_main_option("sqlalchemy.url", None)
    if not url:
        url = get_settings().db_dsn
    return url.replace("%", "%%")


config.set_main_option("sqlalchemy.url", _database_url())


def render_item(type_: str, obj: object, autogen_context) -> bool:  # type: ignore[no-untyped-def]
    """Alembic рендерит JSONB как `postgresql.JSONB(astext_type=Text())`,
    но импорт Text не добавляет — дописываем его сами."""
    if type_ == "type" and isinstance(obj, sa.JSON):
        autogen_context.imports.add("from sqlalchemy import Text")
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_item=render_item,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or ""
    if any(driver in url for driver in ASYNC_DRIVERS):
        asyncio.run(run_async_migrations())
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

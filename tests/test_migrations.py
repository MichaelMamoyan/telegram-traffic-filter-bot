from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.db import models  # noqa: F401
from app.db.base import Base

ROOT = Path(__file__).resolve().parent.parent


def _config(dsn: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", dsn)
    return config


@pytest.fixture
def db_path(tmp_path: Path):
    path = tmp_path / "migrations.db"
    previous = os.environ.get("DB_DSN")
    os.environ["DB_DSN"] = f"sqlite:///{path}"
    yield path
    if previous is None:
        os.environ.pop("DB_DSN", None)
    else:
        os.environ["DB_DSN"] = previous


def test_migrations_produce_schema_matching_models(db_path: Path) -> None:
    """Защита от дрейфа: `alembic upgrade head` должен дать ровно схему моделей."""
    dsn = f"sqlite:///{db_path}"
    upgrade(_config(dsn), "head")

    engine = create_engine(dsn)
    with engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diffs == [], f"Миграции разошлись с моделями: {diffs}"


def test_downgrade_to_base_is_clean(db_path: Path) -> None:
    dsn = f"sqlite:///{db_path}"
    config = _config(dsn)
    upgrade(config, "head")
    downgrade(config, "base")

    engine = create_engine(dsn)
    with engine.connect() as conn:
        tables = set(MigrationContext.configure(conn).connection.dialect.get_table_names(conn))
    engine.dispose()
    assert tables <= {"alembic_version"}, f"После откатa остались таблицы: {tables}"

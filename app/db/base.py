from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB на Postgres, обычный JSON на SQLite (нужно для тестов без поднятия БД).
JSONType = JSON().with_variant(JSONB(), "postgresql")
TimestampTZ = DateTime(timezone=True)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict] = {dict[str, Any]: JSONType, list[str]: JSONType}


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        TimestampTZ, server_default=func.now(), default=utcnow, nullable=False
    )


def make_engine(
    dsn: str, echo: bool = False, pool_size: int = 5, max_overflow: int = 10
) -> AsyncEngine:
    kwargs: dict[str, Any] = {"echo": echo, "pool_pre_ping": True}
    if dsn.startswith("postgresql"):
        # pool_recycle: управляемые базы закрывают простаивающие соединения,
        # и без переподключения бот ловит "connection was closed".
        kwargs |= {
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_recycle": 1800,
        }
    return create_async_engine(dsn, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

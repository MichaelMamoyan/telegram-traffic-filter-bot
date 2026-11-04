from __future__ import annotations

import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DB_DSN", "sqlite+aiosqlite:///:memory:")

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base, make_engine, make_sessionmaker


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()

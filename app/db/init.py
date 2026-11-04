from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db.base import Base
from app.db.repo import ChannelRepo
from app.enums import Segment

log = logging.getLogger(__name__)


async def create_schema(engine: AsyncEngine) -> None:
    """Создаёт таблицы, если их нет.

    На проде схемой управляет Alembic (`alembic upgrade head` в compose),
    но для локального запуска и тестов этого достаточно.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_channels(sessionmaker: async_sessionmaker, path: Path) -> int:
    """Первичная заливка каналов из JSON (см. channels.example.json)."""
    if not path.is_file():
        log.warning("SEED_CHANNELS_FILE=%s не найден или не файл, пропускаю", path)
        return 0

    payload = json.loads(path.read_text(encoding="utf-8"))
    valid = {segment.value for segment in Segment}
    count = 0
    async with sessionmaker() as session:
        channels = ChannelRepo(session)
        for item in payload:
            segments = [s for s in item.get("segments", []) if s in valid]
            unknown = set(item.get("segments", [])) - valid
            if unknown:
                log.warning("Канал %s: неизвестные сегменты %s", item.get("title"), unknown)
            await channels.upsert(
                chat_id=int(item["chat_id"]),
                title=item.get("title") or str(item["chat_id"]),
                username=item.get("username"),
                segments=segments,
                allow_minors=item.get("allow_minors", False),
                weight=item.get("weight"),
                priority=item.get("priority"),
                is_active=item.get("is_active", True),
                offer_text=item.get("offer_text"),
                button_text=item.get("button_text"),
            )
            count += 1
        await session.commit()
    log.info("Залито каналов из seed-файла: %s", count)
    return count

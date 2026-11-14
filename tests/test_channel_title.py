"""Название канала не должно устаревать: при переименовании в Telegram
бот подтягивает свежее имя при выдаче оффера."""

from __future__ import annotations

import pytest_asyncio
from fake_bot import FakeSession, make_bot

from app.db.base import Base, make_engine, make_sessionmaker
from app.db.repo import ChannelRepo
from app.enums import Segment
from app.services import channel_title
from app.services.channel_title import refresh_title


@pytest_asyncio.fixture(autouse=True)
def _clear_cache():
    channel_title._checked.clear()
    yield
    channel_title._checked.clear()


@pytest_asyncio.fixture
async def setup():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    bot, mock = make_bot()
    async with sessionmaker() as session:
        await ChannelRepo(session).upsert(
            chat_id=-1001, title="Старое имя", segments=[Segment.MALE_ADULT.value]
        )
        await session.commit()
    yield sessionmaker, bot, mock
    await bot.session.close()
    await engine.dispose()


def _patch_title(monkeypatch, title: str):
    from aiogram.types import Chat

    async def fake(self, bot, method, timeout=None):  # type: ignore[no-untyped-def]
        if type(method).__name__ == "GetChat":
            self.calls.append(method)
            return Chat(id=-1001, type="channel", title=title)
        return await FakeSession.make_request.__wrapped__(self, bot, method, timeout) \
            if hasattr(FakeSession.make_request, "__wrapped__") else True

    monkeypatch.setattr(FakeSession, "make_request", fake)


async def test_renamed_channel_title_is_refreshed(setup, monkeypatch) -> None:
    sessionmaker, bot, mock = setup
    _patch_title(monkeypatch, "DETOX W$RK")

    async with sessionmaker() as session:
        channels = ChannelRepo(session)
        channel = await channels.get_by_chat_id(-1001)
        await refresh_title(bot, channel, channels)
        await session.commit()

    async with sessionmaker() as session:
        assert (await ChannelRepo(session).get_by_chat_id(-1001)).title == "DETOX W$RK"


async def test_refresh_is_cached_per_ttl(setup, monkeypatch) -> None:
    """Второй вызов подряд не должен лезть в API."""
    sessionmaker, bot, mock = setup
    _patch_title(monkeypatch, "DETOX W$RK")

    async with sessionmaker() as session:
        channels = ChannelRepo(session)
        channel = await channels.get_by_chat_id(-1001)
        await refresh_title(bot, channel, channels)
        await refresh_title(bot, channel, channels)

    assert len(mock.called("GetChat")) == 1


async def test_unchanged_title_is_not_rewritten(setup, monkeypatch) -> None:
    sessionmaker, bot, mock = setup
    _patch_title(monkeypatch, "Старое имя")

    async with sessionmaker() as session:
        channels = ChannelRepo(session)
        channel = await channels.get_by_chat_id(-1001)
        await refresh_title(bot, channel, channels)
        await session.commit()

    async with sessionmaker() as session:
        assert (await ChannelRepo(session).get_by_chat_id(-1001)).title == "Старое имя"


async def test_api_error_does_not_break_offer(setup, monkeypatch) -> None:
    sessionmaker, bot, mock = setup
    mock.fail_methods.add("GetChat")

    async with sessionmaker() as session:
        channels = ChannelRepo(session)
        channel = await channels.get_by_chat_id(-1001)
        await refresh_title(bot, channel, channels)  # не должно бросить

    async with sessionmaker() as session:
        assert (await ChannelRepo(session).get_by_chat_id(-1001)).title == "Старое имя"

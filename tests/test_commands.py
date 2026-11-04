"""Меню команд: области видимости в Telegram замещают друг друга, а не дополняют.

Поймали на живом боте: админу прописали отдельный список, и у него из меню
пропали /start и /reset — чат-скоуп полностью вытеснил общий.
"""

from __future__ import annotations

import pytest_asyncio
from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault
from fake_bot import make_bot

from app.config import Settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.db.repo import BuyerRepo
from app.services.commands import (
    ADMIN_COMMANDS,
    BUYER_COMMANDS,
    USER_COMMANDS,
    setup_commands,
)

ADMIN_ID = 1
BUYER_ID = 777


def _names(commands) -> list[str]:  # type: ignore[no-untyped-def]
    return [c.command for c in commands]


def test_base_commands_are_present_in_every_scope() -> None:
    """Никто не должен терять /start и /reset из-за своей роли."""
    base = set(_names(USER_COMMANDS))
    assert base == {"start", "reset"}
    assert base <= set(_names(BUYER_COMMANDS)), "байер потерял базовые команды"
    assert base <= set(_names(ADMIN_COMMANDS)), "админ потерял базовые команды"


def test_role_commands_do_not_leak_to_everyone() -> None:
    everyone = set(_names(USER_COMMANDS))
    assert "admin" not in everyone and "stats" not in everyone
    assert "admin" not in set(_names(BUYER_COMMANDS)), "байеру админка не положена"
    assert {"stats", "mylinks", "today"} <= set(_names(BUYER_COMMANDS))
    assert {"admin", "cancel"} <= set(_names(ADMIN_COMMANDS))


def test_no_duplicate_commands_in_any_list() -> None:
    for commands in (USER_COMMANDS, BUYER_COMMANDS, ADMIN_COMMANDS):
        names = _names(commands)
        assert len(names) == len(set(names)), f"дубли в списке: {names}"


@pytest_asyncio.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    bot, mock = make_bot()
    yield bot, mock, sessionmaker
    await bot.session.close()
    await engine.dispose()


async def test_setup_assigns_a_list_to_every_audience(env) -> None:
    bot, mock, sessionmaker = env
    async with sessionmaker() as session:
        await BuyerRepo(session).create(name="Вася", tg_id=BUYER_ID)
        await BuyerRepo(session).create(name="Без телеграма")  # tg_id не указан
        await session.commit()

    settings = Settings(bot_token="42:TEST", admin_ids=[ADMIN_ID], _env_file=None)
    await setup_commands(bot, settings, sessionmaker)

    calls = mock.called("SetMyCommands")
    by_chat = {
        call.scope.chat_id: _names(call.commands)
        for call in calls
        if isinstance(call.scope, BotCommandScopeChat)
    }
    default = [
        _names(call.commands)
        for call in calls
        if isinstance(call.scope, BotCommandScopeDefault)
    ]

    assert default == [_names(USER_COMMANDS)]
    assert by_chat[ADMIN_ID] == _names(ADMIN_COMMANDS)
    assert by_chat[BUYER_ID] == _names(BUYER_COMMANDS)
    assert len(by_chat) == 2, "байеру без Telegram ID команды слать некуда"


async def test_admin_who_is_also_a_buyer_keeps_the_admin_list(env) -> None:
    """Один человек может быть и админом, и байером — админский список главнее."""
    bot, mock, sessionmaker = env
    async with sessionmaker() as session:
        await BuyerRepo(session).create(name="Админ-байер", tg_id=ADMIN_ID)
        await session.commit()

    settings = Settings(bot_token="42:TEST", admin_ids=[ADMIN_ID], _env_file=None)
    await setup_commands(bot, settings, sessionmaker)

    scoped = [
        _names(call.commands)
        for call in mock.called("SetMyCommands")
        if isinstance(call.scope, BotCommandScopeChat) and call.scope.chat_id == ADMIN_ID
    ]
    assert scoped == [_names(ADMIN_COMMANDS)], "байерский список не должен затирать админский"


async def test_unreachable_chat_does_not_break_startup(env) -> None:
    """«chat not found» у того, кто не открывал бота, не должен ронять запуск."""
    bot, mock, sessionmaker = env
    mock.fail_methods.add("SetMyCommands")
    settings = Settings(bot_token="42:TEST", admin_ids=[ADMIN_ID], _env_file=None)

    async with sessionmaker() as session:
        await BuyerRepo(session).create(name="Вася", tg_id=BUYER_ID)
        await session.commit()

    await setup_commands(bot, settings, sessionmaker)  # не должно бросить

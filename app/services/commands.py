from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.db.repo import BuyerRepo

log = logging.getLogger(__name__)

# ВАЖНО: области видимости в Telegram не складываются, а замещают друг друга.
# Список для конкретного чата полностью вытесняет общий, поэтому в списки
# байера и админа обязательно входят и базовые команды — иначе у них просто
# пропадут /start и /reset.

USER_COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="reset", description="Пройти опрос заново"),
]

BUYER_COMMANDS = [
    *USER_COMMANDS,
    BotCommand(command="stats", description="Моя статистика"),
    BotCommand(command="today", description="Статистика за сегодня"),
    BotCommand(command="mylinks", description="Мои ссылки"),
]

ADMIN_COMMANDS = [
    *USER_COMMANDS,
    BotCommand(command="admin", description="Админ-панель"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
]


async def _apply(bot: Bot, commands: list[BotCommand], chat_id: int, who: str) -> bool:
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=chat_id))
        return True
    except TelegramAPIError as exc:
        # «chat not found» — человек ещё ни разу не открывал бота. Это нормально:
        # команды проставятся при следующем запуске или при его первом /start.
        log.warning("Не удалось задать команды (%s %s): %s", who, chat_id, exc)
        return False


async def apply_buyer_commands(bot: Bot, tg_id: int) -> bool:
    return await _apply(bot, BUYER_COMMANDS, tg_id, "байер")


async def setup_commands(
    bot: Bot, settings: Settings, sessionmaker: async_sessionmaker
) -> None:
    try:
        await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    except TelegramAPIError as exc:
        # Меню команд — удобство, а не функциональность: запуск оно ронять не должно.
        log.warning("Не удалось задать общий список команд: %s", exc)

    async with sessionmaker() as session:
        buyers = await BuyerRepo(session).list_all()

    for buyer in buyers:
        if buyer.tg_id and not settings.is_admin(buyer.tg_id):
            await apply_buyer_commands(bot, buyer.tg_id)

    for admin_id in settings.admin_ids:
        await _apply(bot, ADMIN_COMMANDS, admin_id, "админ")

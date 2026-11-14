from __future__ import annotations

import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.db.models import Channel
from app.db.repo import ChannelRepo

log = logging.getLogger(__name__)

# Канал переименовывают в Telegram, а бот узнаёт об этом только когда его
# назначают админом. Поэтому при выдаче оффера подтягиваем свежее название,
# но не чаще раза в TTL на канал — иначе на каждого юзера был бы лишний запрос.
_TTL = 300.0
_checked: dict[int, float] = {}


async def refresh_title(bot: Bot, channel: Channel, channels: ChannelRepo) -> None:
    now = time.monotonic()
    if now - _checked.get(channel.chat_id, 0.0) < _TTL:
        return
    _checked[channel.chat_id] = now
    try:
        chat = await bot.get_chat(channel.chat_id)
    except TelegramAPIError as exc:
        log.warning("Не удалось обновить название канала %s: %s", channel.chat_id, exc)
        return
    if chat.title and chat.title != channel.title:
        log.info("Канал %s переименован: %r -> %r", channel.chat_id, channel.title, chat.title)
        await channels.set_title(channel, chat.title)

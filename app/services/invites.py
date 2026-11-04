from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.db.models import Channel, User
from app.db.repo import ChannelRepo

log = logging.getLogger(__name__)


class InviteService:
    """Выдача инвайт-ссылок с join request — основа точного учёта подписок (п.3 ТЗ).

    Ссылка создаётся с creates_join_request=True: юзер не попадает в канал сразу,
    а присылает заявку, которую бот перехватывает событием chat_join_request.
    Именно в этот момент мы достоверно знаем, что конкретный юзер подписался,
    и инкрементим конверсию его реф-ссылки.
    """

    def __init__(self, bot: Bot, per_user_links: bool = False) -> None:
        self.bot = bot
        self.per_user_links = per_user_links

    async def invite_for(
        self, channel: Channel, user: User, channels: ChannelRepo
    ) -> str | None:
        if self.per_user_links:
            link = await self.create_link(channel, name=f"uid {user.tg_id}")
            if link:
                return link

        if channel.invite_link:
            return channel.invite_link

        link = await self.create_link(channel, name="filter-bot")
        if link:
            await channels.set_invite_link(channel, link)
            return link

        # Фолбэк: публичный канал по @username. Точный учёт подписок при этом
        # невозможен (нет заявки) — в логах предупреждаем явно.
        if channel.username:
            log.warning(
                "Канал %s: нет прав на создание инвайта, отдаём публичную ссылку "
                "(подписки считаться не будут)",
                channel.title,
            )
            return f"https://t.me/{channel.username.lstrip('@')}"
        return None

    async def create_link(self, channel: Channel, name: str) -> str | None:
        try:
            invite = await self.bot.create_chat_invite_link(
                chat_id=channel.chat_id, name=name[:32], creates_join_request=True
            )
            return invite.invite_link
        except TelegramAPIError as exc:
            log.error(
                "Не удалось создать инвайт для %s (%s): %s. "
                "Проверьте, что бот — админ канала с правом «Добавление подписчиков».",
                channel.title,
                channel.chat_id,
                exc,
            )
            return None

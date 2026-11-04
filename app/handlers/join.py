from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import ADMINISTRATOR, KICKED, LEFT, ChatMemberUpdatedFilter
from aiogram.types import ChatJoinRequest, ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.config import Settings
from app.db.repo import ChannelRepo, OfferRepo, UserRepo, log_event
from app.enums import EventType

log = logging.getLogger(__name__)
router = Router(name="join")


@router.chat_join_request()
async def on_join_request(
    event: ChatJoinRequest, session: AsyncSession, settings: Settings, bot: Bot
) -> None:
    """Перехватываем заявку на вступление: одобряем и фиксируем конверсию.

    Это единственный способ достоверно связать подписчика канала с конкретной
    реф-ссылкой: event.from_user даёт точный telegram_id, а не «+1 подписчик».
    """
    channels = ChannelRepo(session)
    channel = await channels.get_by_chat_id(event.chat.id)
    if channel is None:
        # Канал ещё не заведён в админке — регистрируем, чтобы не терять заявку.
        channel = await channels.upsert(
            chat_id=event.chat.id,
            title=event.chat.title or str(event.chat.id),
            username=event.chat.username,
            segments=[],
            is_active=False,
        )
        log.warning(
            "Заявка в незаведённый канал %s (%s) — создан черновик, настройте сегменты",
            channel.title,
            channel.chat_id,
        )

    users = UserRepo(session)
    user = await users.get(event.from_user.id)

    await log_event(
        session,
        event.from_user.id,
        EventType.JOIN_REQUEST,
        user.ref_link_id if user else None,
        {
            "channel_id": channel.id,
            "chat_id": channel.chat_id,
            "invite_link": event.invite_link.invite_link if event.invite_link else None,
            "from_bot": user is not None,
        },
    )

    approved = False
    if settings.auto_approve_join_requests:
        if settings.approve_delay:
            await asyncio.sleep(settings.approve_delay)
        try:
            await event.approve()
            approved = True
        except TelegramAPIError as exc:
            log.error(
                "Не смог одобрить заявку %s в %s: %s. Нужно право «Добавление подписчиков».",
                event.from_user.id,
                channel.title,
                exc,
            )

    if user is None:
        # Юзер пришёл в канал не через бота (прямая ссылка) — в воронку не попадает.
        log.info("Заявка от %s вне воронки бота", event.from_user.id)
        return

    await users.mark_subscribed(user, channel)
    await OfferRepo(session).mark_joined(user.tg_id, channel)
    await log_event(
        session,
        user.tg_id,
        EventType.SUBSCRIBED,
        user.ref_link_id,
        {"channel_id": channel.id, "channel": channel.title, "approved": approved},
    )

    if approved:
        try:
            await bot.send_message(
                user.tg_id, texts.JOIN_APPROVED.format(channel=texts.esc(channel.title))
            )
        except TelegramAPIError:
            await users.mark_blocked(user.tg_id)


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED | LEFT))
async def on_member_left(event: ChatMemberUpdated, session: AsyncSession) -> None:
    """Отписки: конверсия, которая «отвалилась», не должна висеть в статистике."""
    channel = await ChannelRepo(session).get_by_chat_id(event.chat.id)
    if channel is None:
        return
    tg_id = event.new_chat_member.user.id
    users = UserRepo(session)
    user = await users.get(tg_id)
    if user is None or user.subscribed_channel_id != channel.id:
        return
    await users.mark_unsubscribed(tg_id)
    await OfferRepo(session).mark_left(tg_id, channel.id)
    await log_event(
        session, tg_id, EventType.UNSUBSCRIBED, user.ref_link_id, {"channel_id": channel.id}
    )


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=ADMINISTRATOR))
async def on_bot_promoted(
    event: ChatMemberUpdated, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    """Бота сделали админом канала — сразу заводим черновик канала в БД."""
    me = await bot.me()
    if event.new_chat_member.user.id != me.id:
        return
    if event.chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP):
        return

    channels = ChannelRepo(session)
    existing = await channels.get_by_chat_id(event.chat.id)
    channel = await channels.upsert(
        chat_id=event.chat.id,
        title=event.chat.title or str(event.chat.id),
        username=event.chat.username,
        segments=None if existing else [],
        is_active=existing.is_active if existing else False,
    )
    log.info("Бот админ в канале %s (%s)", channel.title, channel.chat_id)

    can_invite = getattr(event.new_chat_member, "can_invite_users", False)
    note = (
        "✅ Право «Добавление подписчиков» есть."
        if can_invite
        else "⚠️ Нет права «Добавление подписчиков» — бот не сможет выдавать "
        "инвайты и считать подписки."
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                f"📢 Бот добавлен админом в <b>{texts.esc(channel.title)}</b>\n"
                f"chat_id: <code>{channel.chat_id}</code>\n{note}\n\n"
                "Откройте /admin → Каналы и выберите сегменты.",
            )
        except TelegramAPIError:
            continue

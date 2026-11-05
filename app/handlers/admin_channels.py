from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    MessageOriginChannel,
    MessageOriginChat,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.models import Channel
from app.db.repo import ChannelRepo
from app.enums import SEGMENT_TITLES, Segment
from app.filters import IsAdmin
from app.keyboards.admin import cancel_kb, channel_card_kb, channels_kb
from app.keyboards.callbacks import ChannelCB
from app.services.invites import InviteService
from app.states import NewChannel
from app.utils.ui import safe_edit

log = logging.getLogger(__name__)
router = Router(name="admin_channels")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def render_channels(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    channels = await ChannelRepo(session).list_all()
    covered: set[str] = set()
    for channel in channels:
        if channel.is_active:
            covered |= set(channel.segments or [])
    missing = [SEGMENT_TITLES[s] for s in Segment if s.value not in covered]

    text = ["📢 <b>Каналы (офферы)</b>", f"Всего: {len(channels)}"]
    if missing:
        text += ["", "⚠️ Без канала остались сегменты: " + ", ".join(missing)]
        text += ["Такой трафик уедет по фолбэку или увидит заглушку."]
    if not channels:
        text += [
            "",
            "Каналов нет. Добавьте бота админом в канал с правом "
            "«Добавление подписчиков» — он появится здесь автоматически.",
        ]
    return "\n".join(text), channels_kb(channels)


async def check_rights(bot: Bot, chat_id: int) -> str:
    """Спрашиваем у Telegram, что бот реально может в этом канале.

    Без этого нехватка права «Добавление подписчиков» всплывает только когда
    живой юзер дошёл до конца воронки и увидел заглушку вместо канала.
    """
    try:
        me = await bot.me()
        member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
    except TelegramAPIError as exc:
        log.warning("Не удалось проверить права в %s: %s", chat_id, exc)
        return texts.CHANNEL_RIGHTS_UNKNOWN

    if member.status != ChatMemberStatus.ADMINISTRATOR:
        return texts.CHANNEL_RIGHTS_NOT_ADMIN
    if not getattr(member, "can_invite_users", False):
        return texts.CHANNEL_RIGHTS_NO_INVITE
    return texts.CHANNEL_RIGHTS_OK


async def _card(bot: Bot, channel: Channel) -> tuple[str, InlineKeyboardMarkup]:
    return (
        texts.channel_card(
            title=channel.title,
            chat_id=channel.chat_id,
            segments=channel.segments or [],
            allow_minors=channel.allow_minors,
            is_active=channel.is_active,
            weight=channel.weight,
            has_invite=bool(channel.invite_link),
            rights=await check_rights(bot, channel.chat_id),
        ),
        channel_card_kb(channel),
    )


@router.callback_query(ChannelCB.filter())
async def on_channel(
    callback: CallbackQuery,
    callback_data: ChannelCB,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    channels = ChannelRepo(session)
    action = callback_data.action

    if action == "list":
        text, markup = await render_channels(session)
        await safe_edit(callback, text, markup)
        await callback.answer()
        return

    if action == "new":
        await state.set_state(NewChannel.source)
        await safe_edit(
            callback,
            "Добавление канала — любым из способов:\n\n"
            "1. Перешлите сюда любой пост из канала\n"
            "2. Пришлите <code>@username</code> канала\n"
            "3. Пришлите <code>chat_id</code> (например <code>-1001234567890</code>)\n\n"
            "Бот уже должен быть админом канала с правом «Добавление подписчиков».",
            cancel_kb("channels"),
        )
        await callback.answer()
        return

    channel = await channels.get(callback_data.channel_id)
    if channel is None:
        await callback.answer("Канал удалён", show_alert=True)
        return

    if action == "seg":
        await channels.toggle_segment(channel, Segment(callback_data.value))
    elif action == "minors":
        channel.allow_minors = not channel.allow_minors
        if not channel.allow_minors:
            # Снимаем детские сегменты, чтобы 18+ контент не ушёл к <18.
            channel.segments = sorted(
                set(channel.segments or [])
                - {Segment.MALE_MINOR.value, Segment.FEMALE_MINOR.value}
            )
        await session.flush()
    elif action == "toggle":
        channel.is_active = not channel.is_active
        if channel.is_active and not channel.segments:
            await callback.answer(
                "Сначала выберите хотя бы один сегмент", show_alert=True
            )
            channel.is_active = False
            await session.flush()
            return
        await session.flush()
    elif action == "refresh":
        invites = InviteService(bot, per_user_links=False)
        link = await invites.create_link(channel, name="filter-bot")
        if link is None:
            await callback.answer(
                "Не удалось создать ссылку. Проверьте права бота в канале.", show_alert=True
            )
            return
        await channels.set_invite_link(channel, link)
        await callback.answer("Новая инвайт-ссылка создана")
    elif action == "delete":
        await session.delete(channel)
        await session.flush()
        text, markup = await render_channels(session)
        await safe_edit(callback, text, markup)
        await callback.answer("Канал удалён")
        return

    text, markup = await _card(bot, channel)
    await safe_edit(callback, text, markup)
    await callback.answer()


@router.message(NewChannel.source, ~Command("start", "admin", "cancel"))
async def on_channel_source(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    chat_ref: int | str | None = None
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel | MessageOriginChat):
        chat_ref = origin.chat.id
    elif message.text:
        raw = message.text.strip()
        if raw.startswith("@"):
            chat_ref = raw
        else:
            try:
                chat_ref = int(raw)
            except ValueError:
                chat_ref = None

    if chat_ref is None:
        await message.answer(
            "Не понял. Перешлите пост из канала, либо пришлите @username или chat_id."
        )
        return

    try:
        chat = await bot.get_chat(chat_ref)
    except TelegramAPIError as exc:
        await message.answer(
            f"Telegram не отдал канал: <code>{texts.esc(exc)}</code>\n\n"
            "Чаще всего это значит, что бот не админ в этом канале."
        )
        return

    channel = await ChannelRepo(session).upsert(
        chat_id=chat.id,
        title=chat.title or str(chat.id),
        username=chat.username,
    )
    await state.clear()
    text, markup = await _card(bot, channel)
    await message.answer("✅ Канал добавлен. Выберите сегменты:\n\n" + text, reply_markup=markup)

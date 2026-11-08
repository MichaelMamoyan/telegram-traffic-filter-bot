from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.config import Settings
from app.db.models import User
from app.db.repo import ChannelRepo, LinkRepo, OfferRepo, UserRepo, log_event
from app.enums import AgeStatus, EventType, Gender, Segment, resolve_segment
from app.filters import IsAdmin
from app.keyboards.callbacks import AgeCB, GenderCB
from app.keyboards.funnel import age_kb, gender_kb, offer_kb
from app.services.commands import apply_admin_commands
from app.services.invites import InviteService
from app.services.routing import pick_channel
from app.utils.parsing import parse_start_payload

log = logging.getLogger(__name__)
router = Router(name="funnel")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
    state: FSMContext,
) -> None:
    if message.from_user is None:
        return

    payload = parse_start_payload(command.args)
    links = LinkRepo(session)
    link = await links.get_by_code(payload) if payload else None
    if link is None and payload and settings.auto_create_unknown_links:
        link = await links.create(title=payload, code=payload)
        log.info("Автоматически создана реф-ссылка по неизвестному payload: %s", payload)

    users = UserRepo(session)
    user, is_new = await users.register_start(
        tg_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        language_code=message.from_user.language_code,
        ref_link=link,
        ref_payload=payload,
    )
    await log_event(
        session,
        user.tg_id,
        EventType.START,
        user.ref_link_id,
        {"payload": payload, "new_user": is_new},
    )

    if settings.is_admin(user.tg_id):
        # До первого /start у Telegram нет чата с этим человеком, и команды
        # для него задать нельзя. Проставляем здесь, иначе админ увидит
        # /admin в меню только после перезапуска бота.
        await apply_admin_commands(bot, user.tg_id)

    # Юзер уже прошёл фильтр — не гоняем по опросу второй раз, показываем его оффер.
    if user.segment and user.assigned_channel_id and await _resend_offer(
        message, user, session, bot, settings
    ):
        return

    await state.clear()
    await message.answer(texts.ASK_AGE, reply_markup=age_kb())


# Только админам: иначе юзер может перевыбрать ответы и получить другой
# оффер, и трафик начнёт перетекать между каналами мимо сегментации.
@router.message(Command("reset"), IsAdmin())
async def cmd_reset(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if message.from_user is None:
        return
    user = await UserRepo(session).get(message.from_user.id)
    if user:
        # Сбрасываем только ответы. Источник (реф-ссылка), факт прохождения
        # фильтра и подписка — это история воронки: если их стирать, в отчёте
        # выйдет «прошли фильтр 0, подписались 1», и ломается очевидное
        # неравенство Start >= фильтр >= подписка.
        await log_event(
            session,
            user.tg_id,
            EventType.FILTER_RESET,
            user.ref_link_id,
            {"segment_before": user.segment, "channel_id_before": user.assigned_channel_id},
        )
        user.age_status = None
        user.gender = None
        user.segment = None
        user.assigned_channel_id = None
        user.needs_sheet_sync = True
    await state.clear()
    await message.answer(texts.RESET_DONE)


@router.callback_query(AgeCB.filter())
async def on_age(
    callback: CallbackQuery, callback_data: AgeCB, session: AsyncSession
) -> None:
    if callback.from_user is None:
        return
    users = UserRepo(session)
    user = await users.get(callback.from_user.id)
    if user is None:
        await callback.answer("Нажмите /start", show_alert=True)
        return

    age = AgeStatus.ADULT if callback_data.adult else AgeStatus.MINOR
    await users.set_age(user, age)
    await log_event(session, user.tg_id, EventType.AGE_ANSWER, user.ref_link_id, {"age": age.value})

    await _edit(callback, texts.ASK_GENDER, gender_kb())
    await callback.answer()


@router.callback_query(GenderCB.filter())
async def on_gender(
    callback: CallbackQuery,
    callback_data: GenderCB,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
) -> None:
    if callback.from_user is None:
        return
    users = UserRepo(session)
    user = await users.get(callback.from_user.id)
    if user is None:
        await callback.answer("Нажмите /start", show_alert=True)
        return
    if user.age_status is None:
        # Старая кнопка после рестарта — возвращаем на первый шаг.
        await _edit(callback, texts.ASK_AGE, age_kb())
        await callback.answer()
        return

    gender = Gender.MALE if callback_data.male else Gender.FEMALE
    segment = resolve_segment(AgeStatus(user.age_status), gender)
    await users.set_gender_and_segment(user, gender, segment)
    await log_event(
        session, user.tg_id, EventType.GENDER_ANSWER, user.ref_link_id, {"gender": gender.value}
    )
    await log_event(
        session, user.tg_id, EventType.FILTER_PASSED, user.ref_link_id, {"segment": segment.value}
    )

    await _deliver_offer(callback, user, segment, session, bot, settings)
    await callback.answer()


async def _deliver_offer(
    callback: CallbackQuery,
    user: User,
    segment: Segment,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    channels = ChannelRepo(session)
    channel = pick_channel(await channels.list_all(only_active=True), segment)
    users = UserRepo(session)

    if channel is None:
        await users.set_assigned_channel(user, None)
        await log_event(
            session, user.tg_id, EventType.NO_OFFER, user.ref_link_id, {"segment": segment.value}
        )
        await _edit(callback, texts.NO_OFFER, None)
        return

    invites = InviteService(bot, per_user_links=settings.per_user_invite_links)
    url = await invites.invite_for(channel, user, channels)
    if url is None:
        await users.set_assigned_channel(user, None)
        await log_event(
            session,
            user.tg_id,
            EventType.NO_OFFER,
            user.ref_link_id,
            {"segment": segment.value, "reason": "no_invite_link"},
        )
        await _edit(callback, texts.NO_OFFER, None)
        return

    await users.set_assigned_channel(user, channel)
    await OfferRepo(session).issue(user, channel, url)
    await log_event(
        session,
        user.tg_id,
        EventType.OFFER_ISSUED,
        user.ref_link_id,
        {"segment": segment.value, "channel_id": channel.id, "channel": channel.title},
    )

    # Текст оффера пишет админ, и .format() на произвольной строке падает от
    # любой фигурной скобки. Подставляем единственный плейсхолдер вручную.
    template = channel.offer_text or texts.OFFER_DEFAULT
    text = template.replace("{channel}", texts.esc(channel.title))
    await _edit(callback, text, offer_kb(url, channel.button_text))


async def _resend_offer(
    message: Message, user: User, session: AsyncSession, bot: Bot, settings: Settings
) -> bool:
    """Повторная выдача уже назначенного канала. False — если канал больше недоступен."""
    channels = ChannelRepo(session)
    channel = await channels.get(user.assigned_channel_id) if user.assigned_channel_id else None
    if channel is None or not channel.is_active:
        return False

    invites = InviteService(bot, per_user_links=settings.per_user_invite_links)
    url = await invites.invite_for(channel, user, channels)
    if url is None:
        return False

    text = texts.ALREADY_DONE.format(channel=texts.esc(channel.title))
    if settings.is_admin(user.tg_id):
        text += "\n\n" + texts.RESTART_HINT
    await message.answer(text, reply_markup=offer_kb(url, channel.button_text))
    return True


async def _edit(callback: CallbackQuery, text: str, markup) -> None:  # type: ignore[no-untyped-def]
    """Редактируем текущее сообщение, чтобы не засорять чат (п.2 ТЗ)."""
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()

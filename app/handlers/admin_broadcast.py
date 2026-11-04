from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.repo import BroadcastRepo, LinkRepo, UserRepo
from app.enums import BroadcastStatus
from app.filters import IsAdmin
from app.keyboards.admin import (
    broadcast_kb,
    broadcast_progress_kb,
    broadcast_ref_kb,
    cancel_kb,
    nobuttons_kb,
)
from app.keyboards.callbacks import BroadcastCB
from app.services.broadcast import BroadcastService, buttons_markup
from app.states import BroadcastFlow
from app.utils.parsing import parse_buttons
from app.utils.ui import safe_edit

log = logging.getLogger(__name__)
router = Router(name="admin_broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

STATUS_TITLES = {
    BroadcastStatus.QUEUED.value: "в очереди",
    BroadcastStatus.RUNNING.value: "идёт",
    BroadcastStatus.DONE.value: "завершена",
    BroadcastStatus.CANCELLED.value: "остановлена",
    BroadcastStatus.FAILED.value: "ошибка",
    BroadcastStatus.DRAFT.value: "черновик",
}


async def render_broadcast(
    session: AsyncSession, state: FSMContext
) -> tuple[str, InlineKeyboardMarkup]:
    data = await state.get_data()
    filters = data.get("filters") or {"gender": "all", "age": "all", "status": "all"}
    await state.set_state(BroadcastFlow.editing)
    await state.update_data(filters=filters)

    total = await UserRepo(session).count_targets(filters)
    has_message = bool(data.get("message_id"))
    buttons = len((data.get("buttons") or {}).get("items") or [])
    text = texts.broadcast_draft(filters, total, has_message, buttons)
    return text, broadcast_kb(filters, has_message)


@router.callback_query(BroadcastCB.filter())
async def on_broadcast(
    callback: CallbackQuery,
    callback_data: BroadcastCB,
    session: AsyncSession,
    state: FSMContext,
    broadcasts: BroadcastService,
    bot: Bot,
) -> None:
    action = callback_data.action
    data = await state.get_data()
    filters = data.get("filters") or {"gender": "all", "age": "all", "status": "all"}

    if action == "set":
        if callback_data.field == "ref_link_id":
            link_id = int(callback_data.value or 0)
            if link_id:
                link = await LinkRepo(session).get(link_id)
                filters["ref_link_id"] = link_id
                filters["ref_title"] = link.title if link else None
            else:
                filters.pop("ref_link_id", None)
                filters.pop("ref_title", None)
        else:
            filters[callback_data.field] = callback_data.value
        await state.update_data(filters=filters)
        text, markup = await render_broadcast(session, state)
        await safe_edit(callback, text, markup)

    elif action == "pick_ref":
        links = await LinkRepo(session).list_all()
        await safe_edit(callback, "Выберите реф-ссылку для выборки:", broadcast_ref_kb(links))

    elif action == "msg":
        await state.set_state(BroadcastFlow.message)
        await safe_edit(callback, texts.BROADCAST_ASK_MESSAGE, cancel_kb("broadcast"))

    elif action == "buttons":
        await state.set_state(BroadcastFlow.buttons)
        await safe_edit(callback, texts.BROADCAST_ASK_BUTTONS, nobuttons_kb())

    elif action == "nobuttons":
        await state.update_data(buttons=None)
        text, markup = await render_broadcast(session, state)
        await safe_edit(callback, text, markup)

    elif action == "preview":
        if not data.get("message_id"):
            await callback.answer("Сначала загрузите сообщение", show_alert=True)
            return
        try:
            await bot.copy_message(
                chat_id=callback.from_user.id,
                from_chat_id=data["from_chat_id"],
                message_id=data["message_id"],
                reply_markup=buttons_markup(data.get("buttons")),
            )
        except TelegramAPIError as exc:
            await callback.answer(f"Не скопировалось: {exc}", show_alert=True)
            return
        await callback.answer("Так увидят получатели")
        return

    elif action == "run":
        if not data.get("message_id"):
            await callback.answer("Сначала загрузите сообщение", show_alert=True)
            return
        total = await UserRepo(session).count_targets(filters)
        if total == 0:
            await callback.answer("В выборке нет получателей", show_alert=True)
            return
        broadcast = await BroadcastRepo(session).create(
            admin_id=callback.from_user.id,
            filters=filters,
            from_chat_id=data["from_chat_id"],
            message_id=data["message_id"],
            buttons=data.get("buttons"),
            total=total,
        )
        await session.commit()  # воркер работает в своей сессии — фиксируем до запуска
        broadcasts.start(broadcast.id)
        await state.clear()
        await safe_edit(
            callback,
            texts.BROADCAST_STARTED.format(id=broadcast.id, total=total),
            broadcast_progress_kb(broadcast),
        )

    elif action in ("status", "cancel"):
        if action == "cancel":
            broadcasts.cancel(callback_data.broadcast_id)
            await callback.answer("Останавливаю…")
        broadcast = await BroadcastRepo(session).get(callback_data.broadcast_id)
        if broadcast is None:
            await callback.answer("Рассылка не найдена", show_alert=True)
            return
        await session.refresh(broadcast)
        await safe_edit(
            callback,
            texts.BROADCAST_PROGRESS.format(
                id=broadcast.id,
                status=STATUS_TITLES.get(broadcast.status, broadcast.status),
                sent=broadcast.sent,
                blocked=broadcast.blocked,
                failed=broadcast.failed,
                total=broadcast.total,
            ),
            broadcast_progress_kb(broadcast),
        )

    elif action == "list":
        recent = await BroadcastRepo(session).list_recent()
        if not recent:
            await callback.answer("Рассылок ещё не было", show_alert=True)
            return
        lines = ["📜 <b>Последние рассылки</b>", ""]
        for broadcast in recent:
            lines.append(
                f"#{broadcast.id} · {STATUS_TITLES.get(broadcast.status, broadcast.status)} · "
                f"{broadcast.sent}/{broadcast.total} доставлено"
                + (f" · блок {broadcast.blocked}" if broadcast.blocked else "")
            )
        text, markup = await render_broadcast(session, state)
        await safe_edit(callback, "\n".join(lines) + "\n\n" + text, markup)

    await callback.answer()


@router.message(BroadcastFlow.message, ~Command("start", "admin", "cancel"))
async def on_broadcast_message(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Сохраняем ссылку на исходное сообщение — его будем копировать получателям."""
    await state.update_data(from_chat_id=message.chat.id, message_id=message.message_id)
    await state.set_state(BroadcastFlow.editing)
    text, markup = await render_broadcast(session, state)
    await message.answer("✅ Сообщение сохранено\n\n" + text, reply_markup=markup)


@router.message(BroadcastFlow.buttons)
async def on_broadcast_buttons(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    try:
        items = parse_buttons(message.text or "")
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}\n\n{texts.BROADCAST_ASK_BUTTONS}")
        return
    await state.update_data(buttons={"items": items})
    await state.set_state(BroadcastFlow.editing)
    text, markup = await render_broadcast(session, state)
    await message.answer(f"✅ Кнопок добавлено: {len(items)}\n\n" + text, reply_markup=markup)

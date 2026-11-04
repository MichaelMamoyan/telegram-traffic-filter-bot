from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.db.repo import BuyerRepo, LinkRepo, StatsRepo
from app.filters import IsAdmin
from app.keyboards.admin import (
    buyer_pick_kb,
    buyers_kb,
    cancel_kb,
    deep_link,
    link_card_kb,
    links_kb,
)
from app.keyboards.callbacks import BuyerCB, LinkCB
from app.states import NewBuyer, NewLink
from app.utils.ui import safe_edit

router = Router(name="admin_links")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def render_links(
    session: AsyncSession, bot: Bot, page: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    links = await LinkRepo(session).list_all()
    me = await bot.me()
    text = [
        "🔗 <b>Реферальные ссылки</b>",
        f"Всего: {len(links)}",
        "",
        f"Формат: <code>https://t.me/{me.username}?start=КОД</code>",
    ]
    if not links:
        text.append("\nПока ни одной ссылки — создайте первую.")
    return "\n".join(text), links_kb(links, page)


async def render_buyers(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    buyers = await BuyerRepo(session).list_all()
    text = [
        "👥 <b>Байеры</b>",
        "",
        "Если указать Telegram ID байера, он сможет смотреть свою статистику "
        "командой /stats прямо в боте.",
    ]
    if not buyers:
        text.append("\nПока ни одного байера.")
    return "\n".join(text), buyers_kb(buyers)


@router.callback_query(LinkCB.filter())
async def on_link(
    callback: CallbackQuery,
    callback_data: LinkCB,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    action = callback_data.action

    if action == "list":
        text, markup = await render_links(session, bot, callback_data.page)
        await safe_edit(callback, text, markup)

    elif action == "new":
        await state.set_state(NewLink.title)
        await safe_edit(
            callback,
            "Пришлите название ссылки — например <code>vasya_tiktok_01</code>.\n\n"
            "Название видно только вам, код ссылки сгенерируется из него автоматически.",
            cancel_kb("links"),
        )

    elif action == "open":
        link = await LinkRepo(session).get(callback_data.link_id)
        if link is None:
            await callback.answer("Ссылка удалена", show_alert=True)
            return
        me = await bot.me()
        rows = await StatsRepo(session).link_funnel()
        row = next((r for r in rows if r.link_id == link.id), None)
        await safe_edit(
            callback,
            texts.link_card(link.code, link.title, me.username or "", row),
            link_card_kb(link, deep_link(me.username or "", link.code)),
        )

    elif action == "toggle":
        link = await LinkRepo(session).toggle(callback_data.link_id)
        if link is None:
            await callback.answer("Ссылка удалена", show_alert=True)
            return
        me = await bot.me()
        rows = await StatsRepo(session).link_funnel()
        row = next((r for r in rows if r.link_id == link.id), None)
        await safe_edit(
            callback,
            texts.link_card(link.code, link.title, me.username or "", row),
            link_card_kb(link, deep_link(me.username or "", link.code)),
        )
        await callback.answer("Включена" if link.is_active else "Выключена")
        return

    await callback.answer()


@router.message(NewLink.title, ~Command("start", "admin", "cancel"))
async def on_link_title(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("Нужен текст. Пришлите название ссылки.")
        return
    await state.update_data(title=title)
    buyers = await BuyerRepo(session).list_all()
    if not buyers:
        await _create_link(message, session, state, buyer_id=None)
        return
    await state.set_state(NewLink.buyer)
    await message.answer("Кому принадлежит ссылка?", reply_markup=buyer_pick_kb(buyers))


@router.callback_query(BuyerCB.filter(), NewLink.buyer)
async def on_link_buyer(
    callback: CallbackQuery,
    callback_data: BuyerCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if callback_data.action != "pick":
        await callback.answer()
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await _create_link(
        callback.message, session, state, buyer_id=callback_data.buyer_id or None
    )
    await callback.answer()


async def _create_link(
    message: Message, session: AsyncSession, state: FSMContext, buyer_id: int | None
) -> None:
    data = await state.get_data()
    link = await LinkRepo(session).create(title=data["title"], buyer_id=buyer_id)
    await state.clear()
    me = await message.bot.me() if message.bot else None
    username = me.username if me and me.username else ""
    await message.answer(
        "✅ Ссылка создана\n\n"
        + texts.link_card(link.code, link.title, username)
        + "\n\nНажмите «Скопировать ссылку» — она попадёт в буфер обмена целиком.",
        reply_markup=link_card_kb(link, deep_link(username, link.code)),
    )


@router.callback_query(BuyerCB.filter())
async def on_buyer(
    callback: CallbackQuery,
    callback_data: BuyerCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    action = callback_data.action

    if action == "list":
        text, markup = await render_buyers(session)
        await safe_edit(callback, text, markup)

    elif action == "new":
        await state.set_state(NewBuyer.name)
        await safe_edit(
            callback,
            "Пришлите имя байера. Можно сразу с Telegram ID через пробел:\n\n"
            "<code>Вася 123456789</code>",
            cancel_kb("buyers"),
        )

    elif action == "open":
        buyer = await BuyerRepo(session).get(callback_data.buyer_id)
        if buyer is None:
            await callback.answer("Байер удалён", show_alert=True)
            return
        rows = await StatsRepo(session).link_funnel(buyer_id=buyer.id)
        totals = await StatsRepo(session).totals()
        text = texts.stats_report(f"Байер {buyer.name}", "all", totals, rows)
        _, markup = await render_buyers(session)
        await safe_edit(callback, text, markup)

    await callback.answer()


@router.message(NewBuyer.name, ~Command("start", "admin", "cancel"))
async def on_buyer_name(message: Message, session: AsyncSession, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Нужен текст. Пришлите имя байера.")
        return
    parts = raw.split()
    tg_id: int | None = None
    if parts[-1].isdigit():
        tg_id = int(parts[-1])
        parts = parts[:-1]
    name = " ".join(parts) or f"buyer_{tg_id}"

    buyers = BuyerRepo(session)
    if tg_id is not None and await buyers.get_by_tg_id(tg_id):
        await message.answer("Байер с таким Telegram ID уже есть.")
        return
    buyer = await buyers.create(name=name[:64], tg_id=tg_id)
    await state.clear()
    text, markup = await render_buyers(session)
    await message.answer(
        f"✅ Байер <b>{texts.esc(buyer.name)}</b> добавлен"
        + (f" (ID {buyer.tg_id})" if buyer.tg_id else "")
        + "\n\n"
        + text,
        reply_markup=markup,
    )

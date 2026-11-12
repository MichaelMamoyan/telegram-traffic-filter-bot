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
    confirm_kb,
    deep_link,
    link_card_kb,
    pack_card_kb,
    packs_kb,
)
from app.keyboards.callbacks import BuyerCB, LinkCB, PackCB
from app.services.commands import apply_buyer_commands
from app.states import NewBuyer, NewLink, NewLinkPack
from app.utils.packs import find_pack, pack_key
from app.utils.ui import safe_edit

router = Router(name="admin_links")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def render_links(
    session: AsyncSession, bot: Bot, page: int = 0, arch: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    packs = await LinkRepo(session).list_packs(only_archived=arch)
    total_links = sum(pack.size for pack in packs)
    text = texts.packs_overview(total_links, len(packs), arch)
    return text, packs_kb(packs, page, arch)


async def _open_link_card(
    callback: CallbackQuery, session: AsyncSession, bot: Bot, link_id: int
) -> None:
    link = await LinkRepo(session).get(link_id)
    if link is None:
        await callback.answer("Ссылка удалена", show_alert=True)
        return
    me = await bot.me()
    rows = await StatsRepo(session).link_funnel(include_archived=True)
    row = next((r for r in rows if r.link_id == link.id), None)
    await safe_edit(
        callback,
        texts.link_card(link.code, link.title, me.username or "", row, link.is_archived),
        link_card_kb(link, deep_link(me.username or "", link.code)),
    )


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

    elif action == "pack":
        await state.set_state(NewLinkPack.title)
        await safe_edit(callback, texts.PACK_ASK_TITLE, cancel_kb("links"))

    elif action == "new":
        await state.set_state(NewLink.title)
        await safe_edit(
            callback,
            "Пришлите название ссылки — например <code>vasya_tiktok_01</code>.\n\n"
            "Название видно только вам, код ссылки сгенерируется из него автоматически.",
            cancel_kb("links"),
        )

    elif action == "open":
        await _open_link_card(callback, session, bot, callback_data.link_id)

    elif action == "toggle":
        link = await LinkRepo(session).toggle(callback_data.link_id)
        if link is None:
            await callback.answer("Ссылка удалена", show_alert=True)
            return
        await _open_link_card(callback, session, bot, link.id)
        await callback.answer("Включена" if link.is_active else "Выключена")
        return

    elif action == "arch":
        links = LinkRepo(session)
        link = await links.get(callback_data.link_id)
        if link is None:
            await callback.answer("Ссылка удалена", show_alert=True)
            return
        await links.set_archived([link], not link.is_archived)
        await _open_link_card(callback, session, bot, link.id)
        await callback.answer("В архиве" if link.is_archived else "Возвращена")
        return

    elif action == "askdel":
        links = LinkRepo(session)
        link = await links.get(callback_data.link_id)
        if link is None:
            await callback.answer("Ссылка удалена", show_alert=True)
            return
        _, starts, converted = await links.impact([link])
        await safe_edit(
            callback,
            texts.CONFIRM_DELETE_LINK.format(
                code=texts.esc(link.code), starts=starts, converted=converted
            ),
            confirm_kb(
                LinkCB(action="del", link_id=link.id),
                LinkCB(action="open", link_id=link.id),
            ),
        )

    elif action == "del":
        links = LinkRepo(session)
        link = await links.get(callback_data.link_id)
        if link is not None:
            await links.delete_links([link])
        text, markup = await render_links(session, bot)
        await safe_edit(callback, text, markup)
        await callback.answer("Ссылка удалена")
        return

    await callback.answer()


@router.callback_query(PackCB.filter())
async def on_pack(
    callback: CallbackQuery,
    callback_data: PackCB,
    session: AsyncSession,
    bot: Bot,
) -> None:
    links = LinkRepo(session)
    action = callback_data.action

    if action == "list":
        text, markup = await render_links(session, bot, callback_data.page, callback_data.arch)
        await safe_edit(callback, text, markup)
        await callback.answer()
        return

    if action in ("clean", "askclean"):
        unused = await links.unused_links()
        if action == "askclean":
            if not unused:
                await callback.answer("Неиспользованных ссылок нет", show_alert=True)
                return
            await safe_edit(
                callback,
                texts.CONFIRM_CLEAN.format(count=len(unused)),
                confirm_kb(PackCB(action="clean"), PackCB(action="list")),
            )
            await callback.answer()
            return
        deleted = await links.delete_links(unused)
        text, markup = await render_links(session, bot)
        await safe_edit(callback, text, markup)
        await callback.answer(f"Удалено: {deleted}")
        return

    if action in ("delall", "askdelall"):
        all_links = await links.list_all(include_archived=True)
        if action == "askdelall":
            if not all_links:
                await callback.answer("Ссылок нет", show_alert=True)
                return
            _, starts, converted = await links.impact(all_links)
            await safe_edit(
                callback,
                texts.CONFIRM_DELETE_ALL.format(
                    links=len(all_links), starts=starts, converted=converted
                ),
                confirm_kb(
                    PackCB(action="delall"), PackCB(action="list"), "⚠️ Да, удалить всё"
                ),
            )
            await callback.answer()
            return
        deleted = await links.delete_links(all_links)
        text, markup = await render_links(session, bot)
        await safe_edit(callback, text, markup)
        await callback.answer(f"Удалено: {deleted}")
        return

    # действия над конкретным паком
    packs = await links.list_packs(include_archived=True)
    pack = find_pack(packs, callback_data.pid or "")
    if pack is None:
        await callback.answer("Пак не найден", show_alert=True)
        text, markup = await render_links(session, bot)
        await safe_edit(callback, text, markup)
        return

    if action == "open":
        await _open_pack_card(callback, bot, pack, callback_data.page)

    elif action in ("arch", "unarch"):
        await links.set_archived(pack.links, action == "arch")
        # пересобираем пак с обновлённым флагом
        packs = await links.list_packs(include_archived=True)
        pack = find_pack(packs, callback_data.pid or "")
        if pack:
            await _open_pack_card(callback, bot, pack)
        await callback.answer("Пак в архиве" if action == "arch" else "Возвращён из архива")
        return

    elif action == "resend":
        me = await bot.me()
        username = me.username or ""
        numbered = [(pack_key(link.code)[1] or i, link) for i, link in enumerate(pack.links, 1)]
        await callback.message.answer(  # type: ignore[union-attr]
            texts.PACK_RESEND_HEADER.format(title=texts.esc(pack.title))
        )
        for part in texts.link_pack_messages(pack.title, numbered, username):
            await callback.message.answer(part)  # type: ignore[union-attr]
        await callback.answer("Ссылки отправлены")
        return

    elif action == "askdel":
        _, starts, converted = await links.impact(pack.links)
        await safe_edit(
            callback,
            texts.CONFIRM_DELETE_PACK.format(
                title=texts.esc(pack.title), links=pack.size,
                starts=starts, converted=converted,
            ),
            confirm_kb(
                PackCB(action="del", pid=pack.id),
                PackCB(action="open", pid=pack.id),
            ),
        )

    elif action == "del":
        await links.delete_links(pack.links)
        text, markup = await render_links(session, bot)
        await safe_edit(callback, text, markup)
        await callback.answer("Пак удалён")
        return

    await callback.answer()


async def _open_pack_card(
    callback: CallbackQuery, bot: Bot, pack, page: int = 0
) -> None:
    import datetime as dt

    me = await bot.me()
    now = dt.datetime.now(dt.UTC)
    await safe_edit(
        callback,
        texts.pack_card(pack, pack.age_days(now), me.username or ""),
        pack_card_kb(pack, page),
    )


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
    if buyer.tg_id and message.bot:
        # Чтобы /stats и /mylinks появились у него в меню сразу, а не после
        # перезапуска бота.
        await apply_buyer_commands(message.bot, buyer.tg_id)
    text, markup = await render_buyers(session)
    await message.answer(
        f"✅ Байер <b>{texts.esc(buyer.name)}</b> добавлен"
        + (f" (ID {buyer.tg_id})" if buyer.tg_id else "")
        + "\n\n"
        + text,
        reply_markup=markup,
    )


# --------------------------------------------------------------- пак ссылок


@router.message(NewLinkPack.title, ~Command("start", "admin", "cancel"))
async def on_pack_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("Нужен текст. Пришлите название пака.")
        return
    await state.update_data(title=title)
    await state.set_state(NewLinkPack.count)
    await message.answer(texts.PACK_ASK_COUNT.format(max=texts.PACK_MAX))


@router.message(NewLinkPack.count, ~Command("start", "admin", "cancel"))
async def on_pack_count(message: Message, session: AsyncSession, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= texts.PACK_MAX:
        await message.answer(texts.PACK_ASK_COUNT.format(max=texts.PACK_MAX))
        return

    await state.update_data(count=int(raw))
    buyers = await BuyerRepo(session).list_all()
    if not buyers:
        await _create_pack(message, session, state, buyer_id=None)
        return
    await state.set_state(NewLinkPack.buyer)
    await message.answer("Кому принадлежат ссылки?", reply_markup=buyer_pick_kb(buyers))


@router.callback_query(BuyerCB.filter(), NewLinkPack.buyer)
async def on_pack_buyer(
    callback: CallbackQuery,
    callback_data: BuyerCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if callback_data.action != "pick" or not isinstance(callback.message, Message):
        await callback.answer()
        return
    await _create_pack(callback.message, session, state, buyer_id=callback_data.buyer_id or None)
    await callback.answer()


async def _create_pack(
    message: Message, session: AsyncSession, state: FSMContext, buyer_id: int | None
) -> None:
    data = await state.get_data()
    pack = await LinkRepo(session).create_pack(
        title=data["title"], count=data["count"], buyer_id=buyer_id
    )
    await state.clear()

    me = await message.bot.me() if message.bot else None
    username = me.username if me and me.username else ""
    # Длинный пак не влезает в одно сообщение, поэтому отправляем частями.
    for part in texts.link_pack_messages(data["title"], pack, username):
        await message.answer(part)

    text, markup = await render_links(session, message.bot)  # type: ignore[arg-type]
    await message.answer(text, reply_markup=markup)

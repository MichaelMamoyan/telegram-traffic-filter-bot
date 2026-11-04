from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.config import Settings
from app.db.repo import ChannelRepo, LinkRepo, StatsRepo
from app.filters import IsAdmin
from app.keyboards.admin import export_kb, main_menu_kb, stats_kb
from app.keyboards.callbacks import MenuCB, StatsCB
from app.utils.periods import period_since
from app.utils.ui import safe_edit

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def render_menu(session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    totals = await StatsRepo(session).totals()
    channels = await ChannelRepo(session).list_all(only_active=True)
    links = await LinkRepo(session).list_all()
    text = texts.ADMIN_MENU.format(
        starts=totals.starts,
        passed=totals.passed,
        converted=totals.converted,
        channels=len(channels),
        links=len(links),
    )
    return text, main_menu_kb()


@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    text, markup = await render_menu(session)
    await message.answer(text, reply_markup=markup)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    text, markup = await render_menu(session)
    await message.answer("Действие отменено.\n\n" + text, reply_markup=markup)


@router.callback_query(MenuCB.filter())
async def on_menu(
    callback: CallbackQuery,
    callback_data: MenuCB,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
    bot: Bot,
) -> None:
    await state.clear()
    section = callback_data.section

    if section == "root":
        text, markup = await render_menu(session)
    elif section == "stats":
        text, markup = await _render_stats(session, view="links", period="today")
    elif section == "links":
        from app.handlers.admin_links import render_links

        text, markup = await render_links(session, bot)
    elif section == "buyers":
        from app.handlers.admin_links import render_buyers

        text, markup = await render_buyers(session)
    elif section == "channels":
        from app.handlers.admin_channels import render_channels

        text, markup = await render_channels(session)
    elif section == "broadcast":
        from app.handlers.admin_broadcast import render_broadcast

        text, markup = await render_broadcast(session, state)
    elif section == "export":
        text, markup = "📤 <b>Экспорт базы</b>", export_kb(settings.sheets_enabled)
    else:
        await callback.answer()
        return

    await safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(StatsCB.filter())
async def on_stats(
    callback: CallbackQuery, callback_data: StatsCB, session: AsyncSession
) -> None:
    text, markup = await _render_stats(session, callback_data.view, callback_data.period)
    await safe_edit(callback, text, markup)
    await callback.answer()


async def _render_stats(
    session: AsyncSession, view: str, period: str
) -> tuple[str, InlineKeyboardMarkup]:
    stats = StatsRepo(session)
    since = period_since(period)
    totals = await stats.totals(since)

    if view == "buyers":
        rows = await stats.buyer_funnel(since)
        # Непривязанный трафик показываем отдельной строкой, иначе сумма
        # по байерам не сойдётся с «Нажали Start».
        unattributed = await stats.no_buyer_funnel(since)
        if unattributed.starts:
            rows = [*rows, unattributed]
        text = texts.stats_report("Статистика по байерам", period, totals, rows)
    elif view == "full":
        segments = await stats.segment_breakdown(since)
        channels = await stats.channel_breakdown(since)
        text = texts.stats_report(
            "Сегменты и каналы", period, totals, [], segments=segments, channels=channels
        )
    else:
        rows = await stats.link_funnel(since)
        organic = await stats.organic_funnel(since)
        if organic.starts:
            rows = [*rows, organic]
        text = texts.stats_report("Статистика по ссылкам", period, totals, rows)

    return text, stats_kb(view if view in ("links", "buyers", "full") else "links", period)

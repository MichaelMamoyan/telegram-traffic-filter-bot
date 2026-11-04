from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts
from app.config import Settings
from app.db.repo import BuyerRepo, FunnelRow, LinkRepo, StatsRepo
from app.keyboards.admin import my_links_kb
from app.utils.periods import period_since

router = Router(name="buyer")


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession, settings: Settings) -> None:
    """Личная статистика байера: только его ссылки (п.1 ТЗ)."""
    if message.from_user is None:
        return

    buyer = await BuyerRepo(session).get_by_tg_id(message.from_user.id)
    if buyer is None:
        if settings.is_admin(message.from_user.id):
            await message.answer("Вы админ — полная статистика в /admin")
        return

    stats = StatsRepo(session)
    rows = await stats.link_funnel(buyer_id=buyer.id)
    totals = FunnelRow(
        link_id=None,
        code="all",
        title="Всего",
        buyer_name=buyer.name,
        starts=sum(row.starts for row in rows),
        passed=sum(row.passed for row in rows),
        converted=sum(row.converted for row in rows),
    )
    await message.answer(texts.stats_report(f"Ваша статистика · {buyer.name}", "all", totals, rows))


@router.message(Command("today"))
async def cmd_today(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    buyer = await BuyerRepo(session).get_by_tg_id(message.from_user.id)
    if buyer is None:
        return
    stats = StatsRepo(session)
    since = period_since("today")
    rows = await stats.link_funnel(since, buyer_id=buyer.id)
    totals = FunnelRow(
        link_id=None,
        code="all",
        title="Всего",
        buyer_name=buyer.name,
        starts=sum(row.starts for row in rows),
        passed=sum(row.passed for row in rows),
        converted=sum(row.converted for row in rows),
    )
    await message.answer(texts.stats_report(f"Сегодня · {buyer.name}", "today", totals, rows))


@router.message(Command("mylinks"))
async def cmd_mylinks(message: Message, session: AsyncSession, bot: Bot) -> None:
    if message.from_user is None:
        return
    buyer = await BuyerRepo(session).get_by_tg_id(message.from_user.id)
    if buyer is None:
        return
    links = await LinkRepo(session).list_all(buyer_id=buyer.id)
    if not links:
        await message.answer("У вас пока нет ссылок — попросите админа создать.")
        return
    me = await bot.me()
    rows = await StatsRepo(session).link_funnel(buyer_id=buyer.id)
    by_id = {row.link_id: row for row in rows}
    parts = ["🔗 <b>Ваши ссылки</b>", ""]
    for link in links:
        parts.append(texts.link_card(link.code, link.title, me.username or "", by_id.get(link.id)))
        parts.append("")
    parts.append("Кнопки ниже копируют ссылку в буфер обмена.")
    await message.answer(
        "\n".join(parts), reply_markup=my_links_kb(links, me.username or "")
    )

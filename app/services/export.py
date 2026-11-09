from __future__ import annotations

import asyncio
import datetime as dt
from io import BytesIO

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repo import StatsRepo, UserRepo
from app.services.rows import (
    SHORT_HEADERS,
    SUMMARY_HEADERS,
    TAB_SHORT,
    TAB_SUMMARY,
    USER_HEADERS,
    USER_TABS,
    short_rows,
    summary_rows,
    tab_for,
    user_row,
)


async def build_xlsx(session: AsyncSession, tz: str = "UTC") -> tuple[bytes, str]:
    """Выгрузка базы в Excel той же структурой, что и в Google Sheets."""
    users = await UserRepo(session).list_for_export()
    funnel = await StatsRepo(session).link_funnel()

    by_tab: dict[str, list[list[str]]] = {tab: [] for tab in USER_TABS}
    for user in users:
        by_tab[tab_for(user)].append(user_row(user, tz))

    data = await asyncio.to_thread(
        _render, by_tab, summary_rows(funnel), short_rows(funnel)
    )
    name = f"filter_bot_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return data, name


def _render(
    by_tab: dict[str, list[list[str]]],
    summary: list[list[str]],
    short: list[list[str]],
) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = TAB_SUMMARY
    _fill(sheet, SUMMARY_HEADERS, summary)

    # Тот же набор листов, что и в Google Sheets: выгрузки не должны расходиться.
    _fill(workbook.create_sheet(TAB_SHORT), SHORT_HEADERS, short)

    for tab in USER_TABS:
        _fill(workbook.create_sheet(tab), USER_HEADERS, by_tab.get(tab, []))

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _fill(sheet, headers: list[str], rows: list[list[str]]) -> None:  # type: ignore[no-untyped-def]
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    for index, header in enumerate(headers, start=1):
        widths = [len(str(header)), *(len(str(row[index - 1])) for row in rows)]
        sheet.column_dimensions[get_column_letter(index)].width = min(
            max(max(widths) + 2, 10), 40
        )

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.db.repo import SheetStateRepo, StatsRepo, UserRepo
from app.services.rows import (
    SUMMARY_HEADERS,
    TAB_SUMMARY,
    USER_HEADERS,
    USER_TABS,
    summary_rows,
    tab_for,
    user_row,
)

log = logging.getLogger(__name__)

# Только доступ к таблицам: открываем конкретную таблицу по её ID
# (open_by_key), поиск по Диску не используем — значит и права на Drive
# запрашивать незачем, и включать Drive API в Google Cloud не нужно.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LAST_USER_COL = chr(ord("A") + len(USER_HEADERS) - 1)


class SheetsSyncer:
    """Асинхронная синхронизация базы в Google Sheets.

    Запись идёт в фоне, пачками, по флагу users.needs_sheet_sync — чтобы ответ
    бота юзеру никогда не ждал Google API (требование п.4 ТЗ).

    Номер строки для каждого юзера бронируется в таблице sheet_state, поэтому
    повторная синхронизация обновляет его строку, а не плодит дубликаты.
    """

    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self._spreadsheet: Any = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._tabs_ready = False

    # ------------------------------------------------------------------ lifecycle

    @property
    def enabled(self) -> bool:
        return self.settings.sheets_enabled

    def start(self) -> None:
        if not self.enabled:
            log.info("Google Sheets не настроен — синхронизация выключена")
            return
        self._task = asyncio.create_task(self._loop(), name="sheets-sync")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.settings.sheets_sync_interval
                )
            self._wake.clear()
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Цикл синхронизации Sheets упал, продолжаю через интервал")

    # ------------------------------------------------------------------ gspread

    def _open_sync(self) -> Any:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(
            str(self.settings.google_credentials_file), scopes=SCOPES
        )
        client = gspread.authorize(creds)
        return client.open_by_key(self.settings.google_spreadsheet_id)

    async def _spreadsheet_handle(self) -> Any:
        if self._spreadsheet is None:
            self._spreadsheet = await asyncio.to_thread(self._open_sync)
        return self._spreadsheet

    @staticmethod
    def _ensure_tab(spreadsheet: Any, title: str, headers: list[str]) -> Any:
        import gspread

        try:
            worksheet = spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=title, rows=500, cols=max(len(headers), 12))
            worksheet.batch_update([{"range": "A1", "values": [headers]}])
            worksheet.freeze(rows=1)
        return worksheet

    def _ensure_all_tabs_sync(self) -> None:
        """Создаёт все листы сразу, даже пустые.

        Иначе лист с аудиторией появляется только когда в неё попал первый
        человек, и свежеподключённая таблица выглядит так, будто выгрузка
        не реализована.
        """
        spreadsheet = self._spreadsheet
        existing = {worksheet.title for worksheet in spreadsheet.worksheets()}
        for tab in (TAB_SUMMARY, *USER_TABS):
            if tab in existing:
                continue
            headers = SUMMARY_HEADERS if tab == TAB_SUMMARY else USER_HEADERS
            worksheet = spreadsheet.add_worksheet(
                title=tab, rows=500, cols=max(len(headers), 12)
            )
            worksheet.batch_update([{"range": "A1", "values": [headers]}])
            worksheet.freeze(rows=1)
            log.info("Sheets: создан лист «%s»", tab)

    async def _ensure_tabs(self) -> None:
        if self._tabs_ready:
            return
        await asyncio.to_thread(self._ensure_all_tabs_sync)
        self._tabs_ready = True

    def _write_sync(
        self, payload: dict[str, list[tuple[int, list[str]]]], summary: list[list[str]]
    ) -> None:
        spreadsheet = self._spreadsheet
        for tab, rows in payload.items():
            if not rows:
                continue
            worksheet = self._ensure_tab(spreadsheet, tab, USER_HEADERS)
            max_row = max(row for row, _ in rows)
            if worksheet.row_count < max_row:
                worksheet.add_rows(max_row - worksheet.row_count + 200)
            worksheet.batch_update(
                [
                    {"range": f"A{row}:{LAST_USER_COL}{row}", "values": [values]}
                    for row, values in sorted(rows)
                ],
                value_input_option="USER_ENTERED",
            )

        if summary is not None:
            worksheet = self._ensure_tab(spreadsheet, TAB_SUMMARY, SUMMARY_HEADERS)
            worksheet.clear()
            worksheet.batch_update(
                [{"range": "A1", "values": [SUMMARY_HEADERS, *summary]}],
                value_input_option="USER_ENTERED",
            )
            worksheet.freeze(rows=1)

    # ------------------------------------------------------------------ sync

    async def sync_once(self) -> int:
        if not self.enabled:
            return 0
        await self._spreadsheet_handle()
        await self._ensure_tabs()

        async with self.sessionmaker() as session:
            users_repo = UserRepo(session)
            state_repo = SheetStateRepo(session)
            pending = await users_repo.pending_sheet_sync(self.settings.sheets_sync_batch)
            if not pending:
                summary = summary_rows(await StatsRepo(session).link_funnel())
                await asyncio.to_thread(self._write_sync, {}, summary)
                return 0

            payload: dict[str, list[tuple[int, list[str]]]] = {tab: [] for tab in USER_TABS}
            assignments: list[tuple[int, str, int]] = []

            for user in pending:
                tab = tab_for(user)
                row = user.sheet_row
                if user.sheet_tab != tab or row is None:
                    # Юзер сменил лист (например, достроил фильтр) — старую строку гасим.
                    if user.sheet_tab in payload and user.sheet_row:
                        payload[user.sheet_tab].append(
                            (user.sheet_row, [""] * len(USER_HEADERS))
                        )
                    row = await state_repo.reserve_rows(tab, 1)
                payload[tab].append((row, user_row(user, self.settings.tz_name)))
                assignments.append((user.tg_id, tab, row))

            summary = summary_rows(await StatsRepo(session).link_funnel())
            await asyncio.to_thread(self._write_sync, payload, summary)

            for tg_id, tab, row in assignments:
                await users_repo.mark_synced(tg_id, tab, row)
            await session.commit()

        log.info("Sheets: синхронизировано %s записей", len(assignments))
        return len(assignments)

    async def full_resync(self) -> int:
        """Полная перезаливка: чистим листы и раскладываем базу заново."""
        if not self.enabled:
            return 0
        spreadsheet = await self._spreadsheet_handle()
        await self._ensure_tabs()

        def _clear() -> None:
            import gspread

            for tab in USER_TABS:
                try:
                    worksheet = spreadsheet.worksheet(tab)
                except gspread.WorksheetNotFound:
                    continue
                worksheet.clear()
                worksheet.batch_update([{"range": "A1", "values": [USER_HEADERS]}])

        await asyncio.to_thread(_clear)

        async with self.sessionmaker() as session:
            await SheetStateRepo(session).reset()
            total = await UserRepo(session).mark_all_dirty()
            await session.commit()

        synced = 0
        while True:
            batch = await self.sync_once()
            if batch == 0:
                break
            synced += batch
        log.info("Sheets: полная перезаливка, помечено %s, записано %s", total, synced)
        return synced

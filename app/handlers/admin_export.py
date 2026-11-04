from __future__ import annotations

from aiogram import Bot, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.filters import IsAdmin
from app.keyboards.admin import export_kb
from app.keyboards.callbacks import ExportCB
from app.services.export import build_xlsx
from app.services.sheets import SheetsSyncer

router = Router(name="admin_export")
router.callback_query.filter(IsAdmin())


@router.callback_query(ExportCB.filter())
async def on_export(
    callback: CallbackQuery,
    callback_data: ExportCB,
    session: AsyncSession,
    settings: Settings,
    sheets: SheetsSyncer,
    bot: Bot,
) -> None:
    # Пишем напрямую в приватный чат админа: callback.message может быть
    # недоступен (старое сообщение), и тогда answer_document упал бы.
    chat_id = callback.from_user.id
    action = callback_data.action

    if action == "xlsx":
        await callback.answer("Собираю файл…")
        data, name = await build_xlsx(session, settings.tz_name)
        await bot.send_document(
            chat_id,
            BufferedInputFile(data, filename=name),
            caption="📄 Выгрузка базы: сводка + аудитории по листам",
            reply_markup=export_kb(settings.sheets_enabled),
        )
        return

    if sheets is None or not sheets.enabled:
        await callback.answer("Google Sheets не настроен", show_alert=True)
        return

    url = f"https://docs.google.com/spreadsheets/d/{settings.google_spreadsheet_id}"
    if action == "sheets_sync":
        await callback.answer("Синхронизирую…")
        count = await sheets.sync_once()
        await bot.send_message(
            chat_id, f"✅ Записано строк: {count}\n{url}", reply_markup=export_kb(True)
        )
    elif action == "sheets_full":
        await callback.answer("Перезаливаю целиком, это может занять время…")
        count = await sheets.full_resync()
        await bot.send_message(
            chat_id, f"♻️ Перезалито строк: {count}\n{url}", reply_markup=export_kb(True)
        )

from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

# Жёсткий лимит Telegram — 4096 символов. Режем с запасом на хвост-предупреждение.
TELEGRAM_TEXT_LIMIT = 4096
TRUNCATED_NOTICE = "\n\n… список обрезан, полная выгрузка в 📤 Экспорт"


def clamp(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    """Обрезает текст по границе строки, чтобы не разорвать HTML-тег пополам."""
    if len(text) <= limit:
        return text
    head = text[: limit - len(TRUNCATED_NOTICE)]
    cut = head.rfind("\n")
    return (head[:cut] if cut > limit // 2 else head) + TRUNCATED_NOTICE


async def safe_edit(
    callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    """Правит текущее сообщение; «not modified» глушим, остальное — новым сообщением."""
    text = clamp(text)
    if not isinstance(callback.message, Message):
        if callback.from_user and callback.bot:
            await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)
        return
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        await callback.message.answer(text, reply_markup=markup)

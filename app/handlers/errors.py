from __future__ import annotations

import contextlib
import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent

log = logging.getLogger(__name__)
router = Router(name="errors")


@router.errors()
async def on_unexpected_error(event: ErrorEvent) -> bool:
    """Последний рубеж: без него упавший хендлер оставляет кнопку в вечной загрузке.

    Telegram крутит спиннер, пока на callback_query не ответили, поэтому любое
    необработанное исключение обязано закончиться answer() — иначе оператор
    видит «висящую» админку вместо ошибки.
    """
    log.exception(
        "Необработанная ошибка на апдейте %s", event.update.update_id, exc_info=event.exception
    )

    callback = event.update.callback_query
    if callback is not None:
        with contextlib.suppress(TelegramAPIError):
            await callback.answer("⚠️ Ошибка, детали в логах бота", show_alert=True)
        return True

    message = event.update.message
    if message is not None:
        with contextlib.suppress(TelegramAPIError):
            await message.answer("⚠️ Что-то сломалось. Детали в логах бота.")
    return True


@router.callback_query()
async def unhandled_callback(callback: CallbackQuery) -> None:
    """Страховка от «вечного спиннера».

    Telegram крутит загрузку на кнопке, пока на callback_query не ответили.
    Если ни один хендлер не подошёл (устаревшая кнопка, разъехавшаяся схема
    callback_data), оператор должен увидеть внятное сообщение, а не зависшую
    кнопку — и мы обязаны узнать об этом из логов.
    """
    log.warning("Необработанный callback_query: data=%r", callback.data)
    with contextlib.suppress(TelegramAPIError):
        await callback.answer("Кнопка устарела — откройте /admin заново", show_alert=True)

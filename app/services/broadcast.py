from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import Broadcast
from app.db.repo import BroadcastRepo, UserRepo
from app.enums import BroadcastStatus
from app.services.ratelimit import TokenBucket

log = logging.getLogger(__name__)

FLUSH_EVERY = 25  # как часто сбрасывать счётчики и курсор в БД


def buttons_markup(buttons: dict[str, Any] | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    items = buttons.get("items") or []
    if not items:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=item["text"], url=item["url"])] for item in items
        ]
    )


class BroadcastService:
    """Фоновая отправка push-уведомлений по сегментам базы.

    Сообщение рассылается через copy_message: так переносятся любые типы медиа
    (фото, видео, GIF, кружки, голосовые) без пометки «переслано».
    """

    def __init__(
        self,
        bot: Bot,
        sessionmaker: async_sessionmaker,
        rate: float = 20.0,
        batch: int = 500,
    ) -> None:
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.limiter = TokenBucket(rate)
        self.batch = batch
        self._tasks: dict[int, asyncio.Task] = {}
        self._cancelled: set[int] = set()

    # ------------------------------------------------------------------ управление

    def start(self, broadcast_id: int) -> None:
        if broadcast_id in self._tasks and not self._tasks[broadcast_id].done():
            return
        self._cancelled.discard(broadcast_id)
        task = asyncio.create_task(self._run(broadcast_id), name=f"broadcast-{broadcast_id}")
        self._tasks[broadcast_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(broadcast_id, None))

    def cancel(self, broadcast_id: int) -> None:
        self._cancelled.add(broadcast_id)

    async def resume_unfinished(self) -> int:
        """После рестарта контейнера продолжаем недоставленные рассылки с курсора."""
        async with self.sessionmaker() as session:
            pending = await BroadcastRepo(session).unfinished()
            ids = [broadcast.id for broadcast in pending]
        for broadcast_id in ids:
            log.info("Возобновляю рассылку #%s", broadcast_id)
            self.start(broadcast_id)
        return len(ids)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ------------------------------------------------------------------ отправка

    async def _run(self, broadcast_id: int) -> None:
        async with self.sessionmaker() as session:
            repo = BroadcastRepo(session)
            broadcast = await repo.get(broadcast_id)
            if broadcast is None:
                return
            if broadcast.status not in (
                BroadcastStatus.QUEUED.value,
                BroadcastStatus.RUNNING.value,
            ):
                return
            await repo.set_status(broadcast_id, BroadcastStatus.RUNNING)
            await session.commit()
            snapshot = (
                broadcast.filters,
                broadcast.from_chat_id,
                broadcast.message_id,
                broadcast.buttons,
                broadcast.cursor_tg_id,
            )

        filters, from_chat_id, message_id, buttons, cursor = snapshot
        markup = buttons_markup(buttons)
        sent = failed = blocked = 0
        pending = 0

        try:
            async with self.sessionmaker() as session:
                users = UserRepo(session)
                async for chunk in users.iter_targets(filters, cursor, self.batch):
                    for tg_id in chunk:
                        if broadcast_id in self._cancelled:
                            raise _StoppedByOperatorError
                        outcome = await self._send_one(
                            tg_id, from_chat_id, message_id, markup, session
                        )
                        if outcome == "sent":
                            sent += 1
                        elif outcome == "blocked":
                            blocked += 1
                        else:
                            failed += 1
                        cursor = tg_id
                        pending += 1

                        if pending >= FLUSH_EVERY:
                            await self._flush(
                                session, broadcast_id, cursor, sent, failed, blocked
                            )
                            sent = failed = blocked = 0
                            pending = 0

                    await self._flush(session, broadcast_id, cursor, sent, failed, blocked)
                    sent = failed = blocked = 0
                    pending = 0

            await self._finish(broadcast_id, BroadcastStatus.DONE)
            log.info("Рассылка #%s завершена", broadcast_id)

        except _StoppedByOperatorError:
            async with self.sessionmaker() as session:
                await self._flush(session, broadcast_id, cursor, sent, failed, blocked)
            await self._finish(broadcast_id, BroadcastStatus.CANCELLED)
            log.info("Рассылка #%s остановлена оператором", broadcast_id)

        except asyncio.CancelledError:
            async with self.sessionmaker() as session:
                await self._flush(session, broadcast_id, cursor, sent, failed, blocked)
            raise

        except Exception:
            log.exception("Рассылка #%s упала", broadcast_id)
            async with self.sessionmaker() as session:
                await self._flush(session, broadcast_id, cursor, sent, failed, blocked)
            await self._finish(broadcast_id, BroadcastStatus.FAILED)

    async def _send_one(
        self,
        tg_id: int,
        from_chat_id: int,
        message_id: int,
        markup: InlineKeyboardMarkup | None,
        session: Any,
    ) -> str:
        users = UserRepo(session)
        for attempt in range(3):
            await self.limiter.acquire()
            try:
                await self.bot.copy_message(
                    chat_id=tg_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                    reply_markup=markup,
                )
                return "sent"
            except TelegramRetryAfter as exc:
                log.warning("Flood control: пауза %s сек", exc.retry_after)
                await asyncio.sleep(exc.retry_after + 1)
            except TelegramForbiddenError:
                # Юзер заблокировал бота — помечаем и больше не трогаем.
                await users.mark_blocked(tg_id)
                return "blocked"
            except TelegramBadRequest as exc:
                if "chat not found" in str(exc) or "user is deactivated" in str(exc):
                    await users.mark_blocked(tg_id)
                    return "blocked"
                log.warning("Не отправилось %s: %s", tg_id, exc)
                return "failed"
            except Exception as exc:
                log.warning("Ошибка отправки %s (попытка %s): %s", tg_id, attempt + 1, exc)
                await asyncio.sleep(1)
        return "failed"

    async def _flush(
        self,
        session: Any,
        broadcast_id: int,
        cursor: int,
        sent: int,
        failed: int,
        blocked: int,
    ) -> None:
        if not (sent or failed or blocked):
            return
        await BroadcastRepo(session).bump_counters(
            broadcast_id, cursor, sent=sent, failed=failed, blocked=blocked
        )
        await session.commit()

    async def _finish(self, broadcast_id: int, status: BroadcastStatus) -> None:
        async with self.sessionmaker() as session:
            await BroadcastRepo(session).set_status(broadcast_id, status)
            await session.commit()

    async def snapshot(self, broadcast_id: int) -> Broadcast | None:
        async with self.sessionmaker() as session:
            return await BroadcastRepo(session).get(broadcast_id)


class _StoppedByOperatorError(Exception):
    """Внутренний сигнал: оператор нажал «Остановить»."""

"""Минимальная мок-сессия Telegram: гоняем реальные хендлеры без сети."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, ChatInviteLink, Message, MessageId
from aiogram.types import User as TgUser
from html_check import assert_telegram_html

BOT_USER = TgUser(id=42, is_bot=True, first_name="FilterBot", username="filter_test_bot")


class FakeSession(BaseSession):
    """Возвращает правдоподобные ответы и копит вызовы для проверок в тестах."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.fail_methods: set[str] = set()

    def called(self, name: str) -> list[TelegramMethod[Any]]:
        return [call for call in self.calls if type(call).__name__ == name]

    async def close(self) -> None:  # pragma: no cover - ничего не держим
        return None

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncIterator[bytes]:
        yield b""

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        name = type(method).__name__
        self.calls.append(method)
        # Telegram отклоняет сообщение целиком при невалидной разметке,
        # поэтому проверяем её на каждом исходящем сообщении во всех тестах.
        if name in ("SendMessage", "EditMessageText"):
            assert_telegram_html(getattr(method, "text", "") or "")
        elif name in ("SendDocument", "SendPhoto", "SendVideo"):
            assert_telegram_html(getattr(method, "caption", "") or "")
        if name in self.fail_methods:
            from aiogram.exceptions import TelegramBadRequest

            raise TelegramBadRequest(method=method, message="forced failure in test")
        return self._result(name, method)

    @staticmethod
    def _result(name: str, method: TelegramMethod[Any]) -> Any:
        now = dt.datetime.now(dt.UTC)
        if name == "GetMe":
            return BOT_USER
        if name in ("SendMessage", "EditMessageText", "SendDocument"):
            chat_id = getattr(method, "chat_id", 1)
            return Message(
                message_id=getattr(method, "message_id", None) or 999,
                date=now,
                chat=Chat(id=int(chat_id), type="private"),
                from_user=BOT_USER,
                text=getattr(method, "text", None) or getattr(method, "caption", None),
            )
        if name == "CopyMessage":
            return MessageId(message_id=1000)
        if name == "CreateChatInviteLink":
            chat_id = getattr(method, "chat_id", -100)
            return ChatInviteLink(
                invite_link=f"https://t.me/+invite{abs(int(chat_id))}",
                creator=BOT_USER,
                creates_join_request=True,
                is_primary=False,
                is_revoked=False,
                name=getattr(method, "name", None),
            )
        # ApproveChatJoinRequest, AnswerCallbackQuery, SetMyCommands, DeleteWebhook…
        return True


def make_bot() -> tuple[Bot, FakeSession]:
    session = FakeSession()
    bot = Bot(
        token="42:TEST",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    return bot, session

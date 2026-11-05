"""Проверка прав бота в канале.

Без неё нехватка права «Добавление подписчиков» всплывает только тогда, когда
живой юзер дошёл до конца воронки и увидел заглушку вместо канала.
"""

from __future__ import annotations

import pytest
from aiogram.types import ChatMemberAdministrator, ChatMemberLeft, ChatMemberMember
from fake_bot import BOT_USER, FakeSession, make_bot

from app import texts
from app.handlers.admin_channels import check_rights


def _admin(**rights: bool) -> ChatMemberAdministrator:
    base = {
        "user": BOT_USER, "can_be_edited": False, "is_anonymous": False,
        "can_manage_chat": True, "can_delete_messages": True,
        "can_manage_video_chats": False, "can_restrict_members": True,
        "can_promote_members": False, "can_change_info": True,
        "can_invite_users": True, "can_post_stories": False,
        "can_edit_stories": False, "can_delete_stories": False,
    }
    return ChatMemberAdministrator(**{**base, **rights})


@pytest.fixture
def patched_member(monkeypatch):
    """Подменяет ответ Telegram на GetChatMember."""
    original = FakeSession.make_request

    def _set(member):  # type: ignore[no-untyped-def]
        async def fake(self, bot, method, timeout=None):  # type: ignore[no-untyped-def]
            if type(method).__name__ == "GetChatMember":
                self.calls.append(method)
                return member
            return await original(self, bot, method, timeout)

        monkeypatch.setattr(FakeSession, "make_request", fake)

    return _set


async def test_full_rights(patched_member) -> None:
    patched_member(_admin(can_invite_users=True))
    bot, _ = make_bot()
    assert await check_rights(bot, -1001) == texts.CHANNEL_RIGHTS_OK
    await bot.session.close()


async def test_admin_without_invite_right_is_flagged(patched_member) -> None:
    """Самая частая ошибка настройки — админ есть, права приглашать нет."""
    patched_member(_admin(can_invite_users=False))
    bot, _ = make_bot()
    result = await check_rights(bot, -1001)
    assert result == texts.CHANNEL_RIGHTS_NO_INVITE
    assert "Добавление подписчиков" in result
    await bot.session.close()


async def test_plain_member_is_not_enough(patched_member) -> None:
    patched_member(ChatMemberMember(user=BOT_USER))
    bot, _ = make_bot()
    assert await check_rights(bot, -1001) == texts.CHANNEL_RIGHTS_NOT_ADMIN
    await bot.session.close()


async def test_bot_kicked_from_channel(patched_member) -> None:
    patched_member(ChatMemberLeft(user=BOT_USER))
    bot, _ = make_bot()
    assert await check_rights(bot, -1001) == texts.CHANNEL_RIGHTS_NOT_ADMIN
    await bot.session.close()


async def test_api_error_does_not_break_the_card() -> None:
    """Канал удалён или бота выгнали — карточка всё равно должна открыться."""
    bot, mock = make_bot()
    mock.fail_methods.add("GetChatMember")
    assert await check_rights(bot, -1001) == texts.CHANNEL_RIGHTS_UNKNOWN
    await bot.session.close()


def test_every_outcome_has_its_own_wording() -> None:
    wordings = {
        texts.CHANNEL_RIGHTS_OK,
        texts.CHANNEL_RIGHTS_NO_INVITE,
        texts.CHANNEL_RIGHTS_NOT_ADMIN,
        texts.CHANNEL_RIGHTS_UNKNOWN,
    }
    assert len(wordings) == 4
    for wording in wordings:
        assert wording[0] in "✅⚠️⛔️❔", f"нужен понятный значок: {wording}"

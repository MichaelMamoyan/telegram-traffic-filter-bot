from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup

from app.db.models import Broadcast, Buyer, Channel, RefLink
from app.enums import Segment
from app.handlers import register_routers
from app.keyboards.admin import (
    broadcast_kb,
    broadcast_progress_kb,
    broadcast_ref_kb,
    buyer_pick_kb,
    buyers_kb,
    cancel_kb,
    channel_card_kb,
    channels_kb,
    export_kb,
    link_card_kb,
    links_kb,
    main_menu_kb,
    nobuttons_kb,
    stats_kb,
)
from app.keyboards.funnel import age_kb, gender_kb, offer_kb


def test_routers_register_and_request_needed_updates() -> None:
    dp = Dispatcher(storage=MemoryStorage())
    register_routers(dp)
    used = set(dp.resolve_used_update_types())
    # Без этих типов апдейтов учёт подписок и воронка не работают.
    assert {"message", "callback_query", "chat_join_request", "chat_member", "my_chat_member"} <= used


def _all_keyboards() -> list[InlineKeyboardMarkup]:
    channel = Channel(
        id=1,
        chat_id=-100123,
        title="Derek",
        segments=[Segment.MALE_ADULT.value],
        allow_minors=False,
        is_active=True,
        weight=1.0,
        priority=100,
    )
    minors_channel = Channel(
        id=2, chat_id=-100124, title="Junior", segments=[Segment.FEMALE_MINOR.value],
        allow_minors=True, is_active=False, weight=1.0, priority=100,
    )
    link = RefLink(id=1, code="vasya_tiktok_01", title="vasya_tiktok_01", is_active=True)
    buyer = Buyer(id=1, name="Вася", tg_id=777, is_active=True)
    broadcast = Broadcast(
        id=1, admin_id=1, filters={}, from_chat_id=1, message_id=1, status="running", total=10
    )
    filters = {"gender": "male", "age": "18+", "status": "subscribed"}
    return [
        age_kb(),
        gender_kb(),
        offer_kb("https://t.me/+abc", "Поехали"),
        main_menu_kb(),
        stats_kb("links", "today"),
        stats_kb("full", "all"),
        links_kb([link] * 20, page=1),
        link_card_kb(link, "https://t.me/test_bot?start=vasya_tiktok_01"),
        buyers_kb([buyer]),
        buyer_pick_kb([buyer]),
        channels_kb([channel, minors_channel]),
        channel_card_kb(channel),
        channel_card_kb(minors_channel),
        broadcast_kb(filters, has_message=True),
        broadcast_kb({}, has_message=False),
        broadcast_ref_kb([link]),
        broadcast_progress_kb(broadcast),
        export_kb(True),
        export_kb(False),
        cancel_kb("broadcast"),
        nobuttons_kb(),
    ]


def test_every_keyboard_builds() -> None:
    for markup in _all_keyboards():
        assert isinstance(markup, InlineKeyboardMarkup)
        assert markup.inline_keyboard


def test_callback_data_fits_telegram_limit() -> None:
    """Telegram режет callback_data длиннее 64 байт — ловим это до прода."""
    for markup in _all_keyboards():
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data is None:
                    continue
                assert len(button.callback_data.encode()) <= 64, button.callback_data


def test_adult_only_channel_card_hides_minor_segments() -> None:
    channel = Channel(
        id=1, chat_id=-1, title="A", segments=[], allow_minors=False,
        is_active=True, weight=1.0, priority=100,
    )
    buttons = [b for row in channel_card_kb(channel).inline_keyboard for b in row]
    # Переключателей детских сегментов быть не должно — только предложение разрешить <18.
    minor_toggles = [
        b for b in buttons
        if b.callback_data and b.callback_data.startswith("chan:seg") and "minor" in b.callback_data
    ]
    assert minor_toggles == []
    assert any("Разрешить" in b.text for b in buttons)

"""Телеграм отклоняет сообщение целиком, если разметка невалидна.

Поймали на живом боте: «Парни <18» в тексте — парсер видит открывающий тег
«<18,» и возвращает "can't parse entities". Здесь проверяем, что ни наши
подписи, ни данные извне (названия каналов, ссылок, байеров) этого не делают.
"""

from __future__ import annotations

import pytest
from html_check import HOSTILE, assert_telegram_html

from app import texts
from app.db.models import Channel
from app.db.repo import ChannelRepo, FunnelRow, LinkRepo
from app.enums import SEGMENT_TITLES, Segment


def test_segment_titles_have_no_angle_brackets() -> None:
    """Именно это сломало кнопку «Каналы» на живом боте."""
    for title in SEGMENT_TITLES.values():
        assert "<" not in title and ">" not in title, title


def test_esc_neutralizes_markup() -> None:
    assert texts.esc("a <b> & c") == "a &lt;b&gt; &amp; c"
    assert texts.esc(None) == "None"


def _row(**kwargs) -> FunnelRow:
    base = {
        "link_id": 1, "code": "vasya_tiktok_01", "title": "vasya_tiktok_01",
        "buyer_name": "Вася", "starts": 10, "passed": 8, "converted": 4,
    }
    return FunnelRow(**{**base, **kwargs})


def test_stats_report_survives_hostile_names() -> None:
    text = texts.stats_report(
        title=HOSTILE,
        period="all",
        totals=_row(),
        rows=[_row(title=HOSTILE, code=HOSTILE, buyer_name=HOSTILE)],
        segments={segment.value: {"starts": 1, "passed": 1, "converted": 1} for segment in Segment},
        channels=[(HOSTILE, 5, 2)],
    )
    assert_telegram_html(text)
    assert "Парни до 18" in text  # подписи сегментов выводятся


def test_all_four_segment_titles_render_safely() -> None:
    segments = {segment.value: {"starts": 1, "passed": 1, "converted": 0} for segment in Segment}
    segments["no_filter"] = {"starts": 3, "passed": 0, "converted": 0}
    assert_telegram_html(
        texts.stats_report("Сегменты", "today", _row(), [], segments=segments)
    )


@pytest.mark.parametrize("title", [HOSTILE, "Derek", "<<<", "a & b"])
def test_channel_card_escapes_title(title) -> None:
    text = texts.channel_card(
        title=title,
        chat_id=-1001234567890,
        segments=[Segment.MALE_MINOR.value, Segment.FEMALE_MINOR.value],
        allow_minors=True,
        is_active=True,
        weight=1.0,
        has_invite=True,
    )
    assert_telegram_html(text)


def test_link_card_escapes_title() -> None:
    assert_telegram_html(texts.link_card("code_01", HOSTILE, "test_bot", _row()))


def test_offer_and_join_texts_escape_channel_name() -> None:
    assert_telegram_html(texts.OFFER_DEFAULT.format(channel=texts.esc(HOSTILE)))
    assert_telegram_html(texts.ALREADY_DONE.format(channel=texts.esc(HOSTILE)))
    assert_telegram_html(texts.JOIN_APPROVED.format(channel=texts.esc(HOSTILE)))


def test_broadcast_draft_escapes_ref_title() -> None:
    text = texts.broadcast_draft(
        {"gender": "male", "age": "<18", "status": "subscribed", "ref_title": HOSTILE},
        total=42, has_message=True, buttons=2,
    )
    assert_telegram_html(text)


def test_static_texts_are_valid_html() -> None:
    for name in dir(texts):
        value = getattr(texts, name)
        if name.isupper() and isinstance(value, str) and "{" not in value:
            assert_telegram_html(value)


async def test_render_channels_is_valid_html(session) -> None:
    """Регрессия на конкретный упавший экран: /admin → Каналы."""
    from app.handlers.admin_channels import render_channels

    text, _ = await render_channels(session)
    assert_telegram_html(text)          # пустой список: предупреждение о сегментах
    assert "Парни до 18" in text

    await ChannelRepo(session).upsert(
        chat_id=-1001, title=HOSTILE, segments=[Segment.MALE_ADULT.value], is_active=True
    )
    await session.commit()
    text, _ = await render_channels(session)
    assert_telegram_html(text)


async def test_admin_menu_text_is_valid_html(session) -> None:
    from app.handlers.admin_menu import render_menu

    await LinkRepo(session).create(title=HOSTILE)
    await session.commit()
    text, _ = await render_menu(session)
    assert_telegram_html(text)


def test_validator_actually_catches_the_original_bug() -> None:
    """Проверяем сам тест: на старом тексте он обязан падать."""
    with pytest.raises(AssertionError):
        assert_telegram_html("Сегменты: Парни <18, Девушки <18")


def test_channel_model_title_is_not_rendered_raw() -> None:
    channel = Channel(
        id=1, chat_id=-1, title=HOSTILE, segments=[], allow_minors=False,
        is_active=False, weight=1.0, priority=100,
    )
    from app.handlers.admin_channels import _card

    text, _ = _card(channel)
    assert_telegram_html(text)
    assert "<b>жирный</b>" not in text

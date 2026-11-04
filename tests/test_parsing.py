from __future__ import annotations

import pytest

from app.db.repo import slugify_code
from app.utils.parsing import parse_buttons, parse_start_payload


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("vasya_tiktok_01", "vasya_tiktok_01"),
        ("ref_vasya_tiktok_01", "vasya_tiktok_01"),
        ("REF_abc", "abc"),
        ("", None),
        (None, None),
        ("   ", None),
    ],
)
def test_parse_start_payload(raw, expected) -> None:
    assert parse_start_payload(raw) == expected


def test_payload_is_truncated_to_telegram_limit() -> None:
    assert len(parse_start_payload("x" * 200)) == 64


def test_slugify_handles_cyrillic_and_spaces() -> None:
    assert slugify_code("Вася ТикТок 01") == "vasya_tiktok_01"
    assert slugify_code("!!!") == "link"
    assert len(slugify_code("я" * 100)) <= 48


def test_parse_buttons() -> None:
    items = parse_buttons("Перейти - https://t.me/example\nСайт — https://example.com")
    assert items == [
        {"text": "Перейти", "url": "https://t.me/example"},
        {"text": "Сайт", "url": "https://example.com"},
    ]


def test_parse_buttons_adds_scheme() -> None:
    assert parse_buttons("Канал - t.me/foo")[0]["url"] == "https://t.me/foo"


def test_parse_buttons_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_buttons("просто текст без ссылки")

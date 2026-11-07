"""Пакетное создание реферальных ссылок."""

from __future__ import annotations

import pytest
from html_check import assert_telegram_html

from app import texts
from app.db.repo import BuyerRepo, LinkRepo


async def test_pack_creates_numbered_links(session) -> None:
    pack = await LinkRepo(session).create_pack(title="Kiril", count=10)
    await session.commit()

    assert len(pack) == 10
    assert [number for number, _ in pack] == list(range(1, 11))
    assert [link.code for _, link in pack] == [f"kiril_{i}" for i in range(1, 11)]
    assert [link.title for _, link in pack] == [f"Kiril {i}" for i in range(1, 11)]


async def test_second_pack_continues_numbering(session) -> None:
    """Повторный заказ не начинает нумерацию заново и не плодит kiril_1_2."""
    links = LinkRepo(session)
    await links.create_pack(title="Kiril", count=10)
    await session.commit()

    second = await links.create_pack(title="Kiril", count=5)
    await session.commit()

    assert [number for number, _ in second] == [11, 12, 13, 14, 15]
    assert [link.code for _, link in second] == [f"kiril_{i}" for i in range(11, 16)]
    assert len(await links.list_all()) == 15


async def test_pack_skips_codes_taken_by_single_links(session) -> None:
    links = LinkRepo(session)
    await links.create(title="Kiril 3")  # занимает kiril_3
    await session.commit()

    pack = await links.create_pack(title="Kiril", count=3)
    await session.commit()
    codes = [link.code for _, link in pack]
    assert "kiril_3" not in codes
    assert len(set(codes)) == 3


async def test_pack_is_attributed_to_buyer(session) -> None:
    buyer = await BuyerRepo(session).create(name="Кирилл", tg_id=777)
    pack = await LinkRepo(session).create_pack(title="Kiril", count=3, buyer_id=buyer.id)
    await session.commit()

    assert all(link.buyer_id == buyer.id for _, link in pack)
    assert len(await LinkRepo(session).list_all(buyer_id=buyer.id)) == 3


async def test_cyrillic_pack_name(session) -> None:
    pack = await LinkRepo(session).create_pack(title="Вася ТикТок", count=2)
    await session.commit()
    assert [link.code for _, link in pack] == ["vasya_tiktok_1", "vasya_tiktok_2"]
    assert [link.title for _, link in pack] == ["Вася ТикТок 1", "Вася ТикТок 2"]


async def test_packs_with_similar_names_do_not_collide(session) -> None:
    links = LinkRepo(session)
    await links.create_pack(title="Kiril", count=3)
    await session.commit()
    other = await links.create_pack(title="Kirill", count=3)
    await session.commit()

    assert [link.code for _, link in other] == ["kirill_1", "kirill_2", "kirill_3"]


# --------------------------------------------------------------- вывод в чат


class _Link:
    def __init__(self, code: str) -> None:
        self.code = code


def _pack(count: int, start: int = 1):  # type: ignore[no-untyped-def]
    return [(i, _Link(f"kiril_{i}")) for i in range(start, start + count)]


def test_message_format_matches_request() -> None:
    """Формат из переписки: «1) ссылка», каждая с новой строки."""
    messages = texts.link_pack_messages("Kiril", _pack(3), "trafficfiltero_bot")
    assert len(messages) == 1
    lines = messages[0].splitlines()
    assert "Kiril" in lines[0] and "3 шт." in lines[0]
    assert lines[2] == "1) https://t.me/trafficfiltero_bot?start=kiril_1"
    assert lines[3] == "2) https://t.me/trafficfiltero_bot?start=kiril_2"
    assert lines[4] == "3) https://t.me/trafficfiltero_bot?start=kiril_3"


def test_numbering_follows_link_codes() -> None:
    """Номер в списке совпадает с номером в ссылке, а не с позицией в пачке."""
    messages = texts.link_pack_messages("Kiril", _pack(3, start=11), "bot")
    assert "11) https://t.me/bot?start=kiril_11" in messages[0]
    assert "13) https://t.me/bot?start=kiril_13" in messages[0]


def test_long_pack_is_split_across_messages() -> None:
    """Лимит Telegram — 4096 символов, иначе отправка отклоняется целиком."""
    messages = texts.link_pack_messages("Kiril", _pack(100), "trafficfiltero_bot")
    assert len(messages) > 1
    for message in messages:
        assert len(message) <= 4096
        assert_telegram_html(message)

    # Ни одна ссылка не потеряна и не продублирована.
    joined = "\n".join(messages)
    for i in range(1, 101):
        assert f"{i}) https://t.me/trafficfiltero_bot?start=kiril_{i}\n" in joined + "\n"


def test_pack_output_escapes_hostile_name() -> None:
    assert_telegram_html(texts.link_pack_messages('A <b> & "x"', _pack(2), "bot")[0])


@pytest.mark.parametrize("count", [1, 2, 59, 60, 61])
def test_no_links_lost_at_chunk_boundary(count) -> None:
    messages = texts.link_pack_messages("Kiril", _pack(count), "trafficfiltero_bot")
    joined = "\n".join(messages)
    assert joined.count("https://t.me/") == count


async def test_buyer_links_message_fits_telegram_limit(session) -> None:
    """У байера может быть пак из сотни ссылок — сообщение не должно превысить лимит."""
    from app.db.repo import BuyerRepo
    from app.keyboards.admin import my_links_kb

    buyer = await BuyerRepo(session).create(name="Кирилл", tg_id=777)
    pack = await LinkRepo(session).create_pack(title="Kiril", count=100, buyer_id=buyer.id)
    await session.commit()

    links = [link for _, link in pack]
    parts = ["🔗 <b>Ваши ссылки</b>", ""]
    shown = links[: texts.STATS_MAX_ROWS]
    for link in shown:
        parts.append(texts.link_card(link.code, link.title, "trafficfiltero_bot"))
        parts.append("")
    parts.append(f"Показаны {len(shown)} из {len(links)}.")

    text = "\n".join(parts)
    assert len(text) <= 4096
    assert_telegram_html(text)
    # Кнопок копирования тоже не должно быть больше, чем строк в сообщении.
    assert len(my_links_kb(shown, "bot").inline_keyboard) == len(shown)

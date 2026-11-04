"""Выгрузка XLSX: структура листов и содержимое."""

from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from app.db.repo import BuyerRepo, ChannelRepo, LinkRepo, OfferRepo, UserRepo
from app.enums import AgeStatus, Gender, Segment, resolve_segment
from app.services.export import build_xlsx
from app.services.rows import (
    SUMMARY_HEADERS,
    TAB_FEMALE_ADULT,
    TAB_MALE_ADULT,
    TAB_MINORS,
    TAB_NO_FILTER,
    TAB_SUMMARY,
    USER_HEADERS,
)


async def _seed(session) -> None:
    buyer = await BuyerRepo(session).create(name="Вася", tg_id=777)
    link = await LinkRepo(session).create(title="vasya_tiktok_01", buyer_id=buyer.id)
    channel = await ChannelRepo(session).upsert(
        chat_id=-1001, title="Derek", segments=[Segment.MALE_ADULT.value]
    )
    users = UserRepo(session)
    plan = [
        (1, AgeStatus.ADULT, Gender.MALE, True),
        (2, AgeStatus.ADULT, Gender.FEMALE, False),
        (3, AgeStatus.MINOR, Gender.FEMALE, False),
        (4, None, None, False),
    ]
    for tg_id, age, gender, subscribed in plan:
        user, _ = await users.register_start(
            tg_id=tg_id, username=f"u{tg_id}", first_name="Тест", last_name=None,
            language_code="ru", ref_link=link, ref_payload=link.code,
        )
        if age is None:
            continue
        await users.set_age(user, age)
        await users.set_gender_and_segment(user, gender, resolve_segment(age, gender))
        await users.set_assigned_channel(user, channel)
        await OfferRepo(session).issue(user, channel, "https://t.me/+x")
        if subscribed:
            await users.mark_subscribed(user, channel)
    await session.commit()


async def test_xlsx_has_all_tabs_and_splits_audiences(session) -> None:
    await _seed(session)
    data, name = await build_xlsx(session, "Europe/Moscow")
    assert name.endswith(".xlsx")

    workbook = load_workbook(BytesIO(data))
    assert workbook.sheetnames == [
        TAB_SUMMARY, TAB_MALE_ADULT, TAB_FEMALE_ADULT, TAB_MINORS, TAB_NO_FILTER
    ]

    def rows(tab: str) -> list[list[str]]:
        return [list(row) for row in workbook[tab].iter_rows(values_only=True)]

    assert rows(TAB_MALE_ADULT)[0] == USER_HEADERS
    assert rows(TAB_SUMMARY)[0] == SUMMARY_HEADERS

    # По одному юзеру в каждой аудитории, четвёртый — без фильтра.
    assert [r[0] for r in rows(TAB_MALE_ADULT)[1:]] == ["1"]
    assert [r[0] for r in rows(TAB_FEMALE_ADULT)[1:]] == ["2"]
    assert [r[0] for r in rows(TAB_MINORS)[1:]] == ["3"]
    assert [r[0] for r in rows(TAB_NO_FILTER)[1:]] == ["4"]

    adult = rows(TAB_MALE_ADULT)[1]
    assert adult[1] == "@u1"
    assert adult[3] == "18+"
    assert adult[4] == "Парень"
    assert adult[5] == "vasya_tiktok_01"
    assert adult[6] == "Вася"
    assert adult[7] == "Derek"
    assert adult[8] == "да"
    assert adult[9] and adult[10]  # даты входа и подписки заполнены

    summary = rows(TAB_SUMMARY)[1]
    assert summary[0] == "vasya_tiktok_01"
    assert summary[2] == "Вася"
    assert summary[3:] == ["4", "3", "75.0%", "1", "25.0%", "33.3%"]


async def test_xlsx_on_empty_base_still_builds(session) -> None:
    data, _ = await build_xlsx(session)
    workbook = load_workbook(BytesIO(data))
    assert workbook[TAB_MALE_ADULT].max_row == 1  # только заголовок

from __future__ import annotations

import datetime as dt

from app.db.repo import (
    BuyerRepo,
    ChannelRepo,
    LinkRepo,
    OfferRepo,
    SheetStateRepo,
    StatsRepo,
    UserRepo,
)
from app.enums import AgeStatus, Gender, Segment, resolve_segment
from app.services.rows import TAB_MALE_ADULT, TAB_NO_FILTER, tab_for, user_row


async def _walk_funnel(
    session,
    tg_id: int,
    link,
    channel=None,
    age: AgeStatus | None = AgeStatus.ADULT,
    gender: Gender | None = Gender.MALE,
    subscribe: bool = False,
):
    """Прогоняет юзера по воронке ровно теми же вызовами, что и хендлеры."""
    users = UserRepo(session)
    user, _ = await users.register_start(
        tg_id=tg_id,
        username=f"user{tg_id}",
        first_name="Тест",
        last_name=None,
        language_code="ru",
        ref_link=link,
        ref_payload=link.code if link else None,
    )
    if age is None:
        return user
    await users.set_age(user, age)
    if gender is None:
        return user
    segment = resolve_segment(age, gender)
    await users.set_gender_and_segment(user, gender, segment)
    if channel is not None:
        await users.set_assigned_channel(user, channel)
        await OfferRepo(session).issue(user, channel, "https://t.me/+test")
        if subscribe:
            await users.mark_subscribed(user, channel)
            await OfferRepo(session).mark_joined(user.tg_id, channel)
    return user


async def test_full_funnel_counts_match_tz_example(session) -> None:
    """Пример из ТЗ: Start 100 → фильтр 80 → подписка 45."""
    buyer = await BuyerRepo(session).create(name="Вася", tg_id=777)
    link = await LinkRepo(session).create(title="vasya_tiktok_01", buyer_id=buyer.id)
    channel = await ChannelRepo(session).upsert(
        chat_id=-100123, title="Derek", segments=[Segment.MALE_ADULT.value]
    )

    for tg_id in range(1, 46):  # прошли фильтр и подписались
        await _walk_funnel(session, tg_id, link, channel, subscribe=True)
    for tg_id in range(46, 81):  # прошли фильтр, не подписались
        await _walk_funnel(session, tg_id, link, channel)
    for tg_id in range(81, 101):  # нажали Start и отвалились
        await _walk_funnel(session, tg_id, link, gender=None, age=None)
    await session.commit()

    rows = await StatsRepo(session).link_funnel()
    row = next(r for r in rows if r.link_id == link.id)
    assert (row.starts, row.passed, row.converted) == (100, 80, 45)
    assert round(row.pass_rate) == 80
    assert round(row.conv_rate) == 45
    assert round(row.conv_of_passed, 1) == 56.2
    assert row.buyer_name == "Вася"


async def test_stats_split_by_link_and_buyer(session) -> None:
    buyers = BuyerRepo(session)
    vasya = await buyers.create(name="Вася", tg_id=1)
    petya = await buyers.create(name="Петя", tg_id=2)
    links = LinkRepo(session)
    l1 = await links.create(title="vasya_tt", buyer_id=vasya.id)
    l2 = await links.create(title="vasya_fb", buyer_id=vasya.id)
    l3 = await links.create(title="petya_tt", buyer_id=petya.id)
    channel = await ChannelRepo(session).upsert(
        chat_id=-1, title="A", segments=[Segment.MALE_ADULT.value]
    )

    await _walk_funnel(session, 10, l1, channel, subscribe=True)
    await _walk_funnel(session, 11, l2, channel)
    await _walk_funnel(session, 12, l3, channel, subscribe=True)
    await _walk_funnel(session, 13, l3, channel, subscribe=True)
    await session.commit()

    by_link = {r.link_id: r for r in await StatsRepo(session).link_funnel()}
    assert (by_link[l1.id].starts, by_link[l1.id].converted) == (1, 1)
    assert (by_link[l2.id].starts, by_link[l2.id].converted) == (1, 0)
    assert (by_link[l3.id].starts, by_link[l3.id].converted) == (2, 2)

    by_buyer = {r.title: r for r in await StatsRepo(session).buyer_funnel()}
    assert (by_buyer["Вася"].starts, by_buyer["Вася"].converted) == (2, 1)
    assert (by_buyer["Петя"].starts, by_buyer["Петя"].converted) == (2, 2)

    only_vasya = await StatsRepo(session).link_funnel(buyer_id=vasya.id)
    assert {r.link_id for r in only_vasya} == {l1.id, l2.id}


async def test_repeated_start_does_not_resteal_attribution(session) -> None:
    """Второй /start по чужой ссылке не должен переписывать источник."""
    links = LinkRepo(session)
    first = await links.create(title="first")
    second = await links.create(title="second")
    users = UserRepo(session)

    user, is_new = await users.register_start(
        tg_id=5, username="u", first_name="A", last_name=None,
        language_code="ru", ref_link=first, ref_payload=first.code,
    )
    assert is_new
    user, is_new = await users.register_start(
        tg_id=5, username="u2", first_name="A", last_name=None,
        language_code="ru", ref_link=second, ref_payload=second.code,
    )
    assert not is_new
    assert user.ref_link_id == first.id
    assert user.username == "u2"


async def test_segment_breakdown(session) -> None:
    link = await LinkRepo(session).create(title="mix")
    channels = ChannelRepo(session)
    adult = await channels.upsert(chat_id=-1, title="A", segments=[Segment.MALE_ADULT.value])
    junior = await channels.upsert(
        chat_id=-2, title="J", segments=[Segment.FEMALE_MINOR.value], allow_minors=True
    )

    await _walk_funnel(session, 1, link, adult, AgeStatus.ADULT, Gender.MALE, subscribe=True)
    await _walk_funnel(session, 2, link, adult, AgeStatus.ADULT, Gender.MALE)
    await _walk_funnel(session, 3, link, junior, AgeStatus.MINOR, Gender.FEMALE, subscribe=True)
    await _walk_funnel(session, 4, link, gender=None, age=None)
    await session.commit()

    breakdown = await StatsRepo(session).segment_breakdown()
    assert breakdown[Segment.MALE_ADULT.value] == {"starts": 2, "passed": 2, "converted": 1}
    assert breakdown[Segment.FEMALE_MINOR.value] == {"starts": 1, "passed": 1, "converted": 1}
    assert breakdown["no_filter"]["starts"] == 1

    by_channel = {name: (issued, joined) for name, issued, joined in
                  await StatsRepo(session).channel_breakdown()}
    assert by_channel["A"] == (2, 1)
    assert by_channel["J"] == (1, 1)


async def test_period_filter(session) -> None:
    link = await LinkRepo(session).create(title="old")
    users = UserRepo(session)
    old_user, _ = await users.register_start(
        tg_id=1, username=None, first_name=None, last_name=None,
        language_code=None, ref_link=link, ref_payload=link.code,
    )
    old_user.started_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=10)
    await users.register_start(
        tg_id=2, username=None, first_name=None, last_name=None,
        language_code=None, ref_link=link, ref_payload=link.code,
    )
    await session.commit()

    stats = StatsRepo(session)
    assert (await stats.totals()).starts == 2
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=7)
    assert (await stats.totals(since)).starts == 1


async def test_broadcast_target_filters(session) -> None:
    link = await LinkRepo(session).create(title="bc")
    other = await LinkRepo(session).create(title="other")
    channel = await ChannelRepo(session).upsert(
        chat_id=-1, title="A", segments=[Segment.MALE_ADULT.value]
    )
    await _walk_funnel(session, 1, link, channel, AgeStatus.ADULT, Gender.MALE, subscribe=True)
    await _walk_funnel(session, 2, link, channel, AgeStatus.ADULT, Gender.FEMALE)
    await _walk_funnel(session, 3, link, channel, AgeStatus.MINOR, Gender.MALE)
    await _walk_funnel(session, 4, link, gender=None, age=None)
    await _walk_funnel(session, 5, other, channel, AgeStatus.ADULT, Gender.MALE, subscribe=True)
    await session.commit()

    users = UserRepo(session)
    assert await users.count_targets({}) == 5
    assert await users.count_targets({"gender": "male"}) == 3
    assert await users.count_targets({"gender": "female"}) == 1
    assert await users.count_targets({"age": "18+"}) == 3
    assert await users.count_targets({"age": "<18"}) == 1
    assert await users.count_targets({"status": "filter_passed"}) == 4
    assert await users.count_targets({"status": "no_filter"}) == 1
    assert await users.count_targets({"status": "subscribed"}) == 2
    assert await users.count_targets({"status": "not_subscribed"}) == 2
    assert await users.count_targets({"ref_link_id": link.id}) == 4
    # Пример из ТЗ: «парни 18+, подписавшиеся на канал Derek»
    assert await users.count_targets(
        {"gender": "male", "age": "18+", "status": "subscribed", "channel_id": channel.id}
    ) == 2

    await users.mark_blocked(1)
    await session.commit()
    assert await users.count_targets({"status": "subscribed"}) == 1


async def test_iter_targets_paginates_without_gaps(session) -> None:
    link = await LinkRepo(session).create(title="p")
    for tg_id in range(1, 26):
        await _walk_funnel(session, tg_id, link, gender=None, age=None)
    await session.commit()

    collected: list[int] = []
    async for chunk in UserRepo(session).iter_targets({}, 0, batch=7):
        collected.extend(chunk)
    assert collected == list(range(1, 26))

    # продолжение с курсора — как после рестарта контейнера
    resumed: list[int] = []
    async for chunk in UserRepo(session).iter_targets({}, 20, batch=7):
        resumed.extend(chunk)
    assert resumed == list(range(21, 26))


async def test_sheet_tabs_and_row_reservation(session) -> None:
    link = await LinkRepo(session).create(title="sheet")
    buyer = await BuyerRepo(session).create(name="Вася")
    link.buyer_id = buyer.id
    channel = await ChannelRepo(session).upsert(
        chat_id=-1, title="Derek", segments=[Segment.MALE_ADULT.value]
    )
    adult = await _walk_funnel(session, 1, link, channel, subscribe=True)
    dropped = await _walk_funnel(session, 2, link, gender=None, age=None)
    await session.commit()

    assert tab_for(adult) == TAB_MALE_ADULT
    assert tab_for(dropped) == TAB_NO_FILTER

    row = user_row(adult, "UTC")
    assert row[0] == "1"
    assert row[1] == "@user1"
    assert row[3] == "18+"
    assert row[4] == "Парень"
    assert row[5] == "sheet"
    assert row[6] == "Вася"
    assert row[7] == "Derek"
    assert row[8] == "да"

    state = SheetStateRepo(session)
    assert await state.reserve_rows(TAB_MALE_ADULT, 1) == 2
    assert await state.reserve_rows(TAB_MALE_ADULT, 3) == 3
    assert await state.reserve_rows(TAB_MALE_ADULT, 1) == 6
    assert await state.reserve_rows(TAB_NO_FILTER, 1) == 2


async def test_pending_sheet_sync_flow(session) -> None:
    link = await LinkRepo(session).create(title="s")
    await _walk_funnel(session, 1, link, gender=None, age=None)
    await session.commit()

    users = UserRepo(session)
    pending = await users.pending_sheet_sync(10)
    assert [u.tg_id for u in pending] == [1]
    await users.mark_synced(1, TAB_NO_FILTER, 2)
    await session.commit()
    assert not await users.pending_sheet_sync(10)

    # любое изменение снова помечает юзера на синхронизацию
    user = await users.get(1)
    await users.set_age(user, AgeStatus.ADULT)
    await session.commit()
    assert [u.tg_id for u in await users.pending_sheet_sync(10)] == [1]


async def test_link_code_collisions_get_suffix(session) -> None:
    links = LinkRepo(session)
    first = await links.create(title="Вася ТикТок")
    second = await links.create(title="Вася ТикТок")
    assert first.code == "vasya_tiktok"
    assert second.code == "vasya_tiktok_2"


async def test_organic_traffic_is_visible_and_totals_add_up(session) -> None:
    """Сумма строк отчёта обязана равняться итогу.

    link_funnel строится от таблицы ссылок, поэтому юзеры без ссылки в строки
    не попадали, а в «Нажали Start» попадали — отчёт не сходился сам с собой.
    """
    link = await LinkRepo(session).create(title="vasya_tiktok_01")
    channel = await ChannelRepo(session).upsert(
        chat_id=-1, title="Derek", segments=[Segment.MALE_ADULT.value]
    )
    # Двое по ссылке, один подписался.
    await _walk_funnel(session, 1, link, channel, subscribe=True)
    await _walk_funnel(session, 2, link, channel)
    # Двое пришли сами: один прошёл фильтр и подписался, другой отвалился.
    await _walk_funnel(session, 3, None, channel, subscribe=True)
    await _walk_funnel(session, 4, None, gender=None, age=None)
    await session.commit()

    stats = StatsRepo(session)
    totals = await stats.totals()
    links = await stats.link_funnel()
    organic = await stats.organic_funnel()

    assert (organic.starts, organic.passed, organic.converted) == (2, 1, 1)
    assert sum(row.starts for row in links) + organic.starts == totals.starts == 4
    assert sum(row.passed for row in links) + organic.passed == totals.passed == 3
    assert sum(row.converted for row in links) + organic.converted == totals.converted == 2


async def test_buyer_view_accounts_for_links_without_owner(session) -> None:
    """Ссылка без байера тоже не должна выпадать из отчёта по байерам."""
    buyer = await BuyerRepo(session).create(name="Вася")
    owned = await LinkRepo(session).create(title="vasya_tt", buyer_id=buyer.id)
    orphan = await LinkRepo(session).create(title="borya")  # ссылка без владельца
    channel = await ChannelRepo(session).upsert(
        chat_id=-1, title="Derek", segments=[Segment.MALE_ADULT.value]
    )
    await _walk_funnel(session, 1, owned, channel, subscribe=True)
    await _walk_funnel(session, 2, orphan, channel, subscribe=True)
    await _walk_funnel(session, 3, None, channel)  # вообще без ссылки
    await session.commit()

    stats = StatsRepo(session)
    totals = await stats.totals()
    buyers = await stats.buyer_funnel()
    unattributed = await stats.no_buyer_funnel()

    assert {row.title for row in buyers} == {"Вася"}
    assert (unattributed.starts, unattributed.converted) == (2, 1)
    assert sum(row.starts for row in buyers) + unattributed.starts == totals.starts == 3
    assert sum(row.converted for row in buyers) + unattributed.converted == totals.converted == 2


async def test_organic_row_is_empty_when_all_traffic_has_links(session) -> None:
    link = await LinkRepo(session).create(title="only_link")
    await _walk_funnel(session, 1, link, gender=None, age=None)
    await session.commit()
    organic = await StatsRepo(session).organic_funnel()
    assert organic.starts == 0  # строку в отчёт не добавляем


def test_engine_recycles_postgres_connections() -> None:
    """Управляемые базы рвут простаивающие соединения — нужен pool_recycle."""
    from app.db.base import make_engine

    engine = make_engine("postgresql+asyncpg://u:p@h:5432/db", pool_size=3, max_overflow=7)
    assert engine.pool.size() == 3
    assert engine.pool._max_overflow == 7
    assert engine.pool._recycle == 1800


def test_sqlite_engine_ignores_pool_settings() -> None:
    """У SQLite своя схема пула, параметры Postgres ей передавать нельзя."""
    from app.db.base import make_engine

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    assert engine is not None

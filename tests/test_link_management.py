"""Архив и удаление ссылок: репозиторий + сквозные прогоны через Dispatcher.

Недельный цикл арбитража: выдали пак → неделю льют → рассчитались → закрыли.
Закрытие (архив) прячет пак из списков и отчётов, но хранит историю выплат;
удаление стирает безвозвратно.
"""

from __future__ import annotations

import datetime as dt

import pytest_asyncio
from aiogram import Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from fake_bot import make_bot

from app.config import Settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.db.repo import ChannelRepo, LinkRepo, StatsRepo, UserRepo
from app.enums import AgeStatus, Gender, Segment, resolve_segment
from app.handlers import ROUTERS, register_routers
from app.keyboards.callbacks import LinkCB, PackCB
from app.middlewares import DbSessionMiddleware
from app.utils.packs import pack_id

ADMIN_ID = 1


# --------------------------------------------------------------- репозиторий


async def _mk_user(session, tg_id, link, subscribe=False):  # type: ignore[no-untyped-def]
    users = UserRepo(session)
    user, _ = await users.register_start(
        tg_id=tg_id, username=None, first_name=None, last_name=None,
        language_code=None, ref_link=link, ref_payload=link.code if link else None,
    )
    await users.set_age(user, AgeStatus.ADULT)
    seg = resolve_segment(AgeStatus.ADULT, Gender.MALE)
    await users.set_gender_and_segment(user, Gender.MALE, seg)
    if subscribe:
        ch = await ChannelRepo(session).upsert(
            chat_id=-1, title="A", segments=[Segment.MALE_ADULT.value]
        )
        await users.mark_subscribed(user, ch)
    return user


async def test_list_packs_groups_and_counts(session) -> None:
    links = LinkRepo(session)
    pack = await links.create_pack(title="ММ", count=5)
    await links.create(title="одиночка")
    await _mk_user(session, 1, pack[0][1], subscribe=True)
    await _mk_user(session, 2, pack[0][1])
    await _mk_user(session, 3, pack[1][1], subscribe=True)
    await session.commit()

    packs = await links.list_packs()
    by_title = {p.title: p for p in packs}
    assert by_title["ММ"].size == 5
    assert by_title["ММ"].starts == 3
    assert by_title["ММ"].converted == 2
    assert by_title["одиночка"].is_single


async def test_archive_hides_from_lists_and_stats_but_keeps_history(session) -> None:
    links = LinkRepo(session)
    pack = await links.create_pack(title="ММ", count=3)
    await _mk_user(session, 1, pack[0][1], subscribe=True)
    await session.commit()

    await links.set_archived([link for _, link in pack], True)
    await session.commit()

    # из обычного списка пропал
    assert await links.list_packs() == []
    # но виден в архиве
    assert len(await links.list_packs(only_archived=True)) == 1
    # из воронки по ссылкам ушёл
    assert await StatsRepo(session).link_funnel() == []
    # зато трафик виден отдельной строкой
    arch = await StatsRepo(session).archived_funnel()
    assert (arch.starts, arch.converted) == (1, 1)
    # история юзера цела
    user = await UserRepo(session).get(1)
    assert user.ref_link_id == pack[0][1].id


async def test_unused_links_are_exactly_those_without_traffic(session) -> None:
    links = LinkRepo(session)
    pack = await links.create_pack(title="ММ", count=5)
    await _mk_user(session, 1, pack[0][1])
    await _mk_user(session, 2, pack[2][1], subscribe=True)
    await session.commit()

    unused = await links.unused_links()
    codes = {link.code for link in unused}
    assert codes == {"mm_2", "mm_4", "mm_5"}


async def test_delete_links_keeps_user_records(session) -> None:
    links = LinkRepo(session)
    link = await links.create(title="vasya")
    await _mk_user(session, 1, link, subscribe=True)
    await session.commit()

    cnt, starts, converted = await links.impact([link])
    assert (cnt, starts, converted) == (1, 1, 1)

    await links.delete_links([link])
    await session.commit()

    user = await UserRepo(session).get(1)
    assert user is not None, "юзер остаётся"
    assert user.ref_link_id is None, "ссылка обнулилась"
    assert await links.get(link.id) is None


async def test_pack_links_matches_only_exact_base(session) -> None:
    links = LinkRepo(session)
    await links.create_pack(title="ММ", count=3)
    await links.create_pack(title="ММ Тикток", count=2)  # код mm_tiktok_N
    await session.commit()

    pack = await links.pack_links("mm")
    assert {link.code for link in pack} == {"mm_1", "mm_2", "mm_3"}


# --------------------------------------------------------------- через Dispatcher


@pytest_asyncio.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    settings = Settings(bot_token="42:TEST", admin_ids=[ADMIN_ID], _env_file=None)
    bot, mock = make_bot()
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.workflow_data.update(settings=settings, broadcasts=None, sheets=None)
    register_routers(dp)

    async with sessionmaker() as session:
        await ChannelRepo(session).upsert(
            chat_id=-1001, title="A", segments=[Segment.MALE_ADULT.value], is_active=True
        )
        pack = await LinkRepo(session).create_pack(title="ММ", count=4)
        await _mk_user(session, 100, pack[0][1], subscribe=True)
        await _mk_user(session, 101, pack[0][1])
        await session.commit()

    yield dp, bot, mock, sessionmaker
    for r in ROUTERS:
        r._parent_router = None
    await bot.session.close()
    await engine.dispose()


def _cb(data: str, uid: int) -> Update:
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid), from_user=TgUser(id=ADMIN_ID, is_bot=False, first_name="A"),
            chat_instance="ci", data=data,
            message=Message(
                message_id=uid, date=dt.datetime.now(dt.UTC),
                chat=Chat(id=ADMIN_ID, type="private"), text="x",
            ),
        ),
    )


async def _mm_id(sessionmaker) -> str:
    return pack_id("mm")


async def test_open_pack_card_shows_links_and_actions(env) -> None:
    dp, bot, mock, sessionmaker = env
    pid = await _mm_id(sessionmaker)

    res = await dp.feed_update(bot, _cb(PackCB(action="open", pid=pid).pack(), 1))
    assert res is not UNHANDLED
    text = mock.called("EditMessageText")[-1].text
    assert "ММ" in text and "Ссылок: <b>4</b>" in text


async def test_archive_pack_then_it_leaves_main_list(env) -> None:
    dp, bot, mock, sessionmaker = env
    pid = await _mm_id(sessionmaker)

    await dp.feed_update(bot, _cb(PackCB(action="arch", pid=pid).pack(), 1))

    async with sessionmaker() as session:
        assert await LinkRepo(session).list_packs() == []
        assert len(await LinkRepo(session).list_packs(only_archived=True)) == 1

    # вернуть из архива
    await dp.feed_update(bot, _cb(PackCB(action="unarch", pid=pid).pack(), 2))
    async with sessionmaker() as session:
        assert len(await LinkRepo(session).list_packs()) == 1


async def test_delete_pack_needs_confirmation(env) -> None:
    dp, bot, mock, sessionmaker = env
    pid = await _mm_id(sessionmaker)

    # запрос подтверждения ничего не удаляет
    await dp.feed_update(bot, _cb(PackCB(action="askdel", pid=pid).pack(), 1))
    assert "Удалить пак" in mock.called("EditMessageText")[-1].text
    async with sessionmaker() as session:
        assert len(await LinkRepo(session).list_all()) == 4

    # подтверждение удаляет
    await dp.feed_update(bot, _cb(PackCB(action="del", pid=pid).pack(), 2))
    async with sessionmaker() as session:
        assert await LinkRepo(session).list_all(include_archived=True) == []


async def test_clean_unused_removes_only_empty_links(env) -> None:
    dp, bot, mock, sessionmaker = env
    await dp.feed_update(bot, _cb(PackCB(action="clean").pack(), 1))

    async with sessionmaker() as session:
        codes = {link.code for link in await LinkRepo(session).list_all()}
    assert codes == {"mm_1"}, "осталась только ссылка с трафиком"


async def test_delete_all_wipes_everything(env) -> None:
    dp, bot, mock, sessionmaker = env
    await dp.feed_update(bot, _cb(PackCB(action="delall").pack(), 1))

    async with sessionmaker() as session:
        assert await LinkRepo(session).list_all(include_archived=True) == []
        # юзеры целы, ссылки обнулены
        user = await UserRepo(session).get(100)
        assert user is not None and user.ref_link_id is None


async def test_resend_pack_sends_all_links(env) -> None:
    dp, bot, mock, sessionmaker = env
    pid = await _mm_id(sessionmaker)

    await dp.feed_update(bot, _cb(PackCB(action="resend", pid=pid).pack(), 1))
    sent = "\n".join(c.text for c in mock.called("SendMessage"))
    for i in range(1, 5):
        assert f"start=mm_{i}" in sent


async def test_delete_one_link_from_card(env) -> None:
    dp, bot, mock, sessionmaker = env
    async with sessionmaker() as session:
        link_id = (await LinkRepo(session).get_by_code("mm_2")).id

    await dp.feed_update(bot, _cb(LinkCB(action="askdel", link_id=link_id).pack(), 1))
    assert "Удалить ссылку" in mock.called("EditMessageText")[-1].text
    await dp.feed_update(bot, _cb(LinkCB(action="del", link_id=link_id).pack(), 2))

    async with sessionmaker() as session:
        assert await LinkRepo(session).get_by_code("mm_2") is None
        assert len(await LinkRepo(session).list_all()) == 3


async def test_archived_link_does_not_count_traffic(env) -> None:
    dp, bot, mock, sessionmaker = env
    async with sessionmaker() as session:
        link_id = (await LinkRepo(session).get_by_code("mm_1")).id

    await dp.feed_update(bot, _cb(LinkCB(action="arch", link_id=link_id).pack(), 1))

    # новый переход по архивной ссылке уходит в органику
    await dp.feed_update(
        bot,
        Update(
            update_id=10,
            message=Message(
                message_id=10, date=dt.datetime.now(dt.UTC),
                chat=Chat(id=500, type="private"),
                from_user=TgUser(id=500, is_bot=False, first_name="N"),
                text="/start mm_1",
            ),
        ),
    )
    async with sessionmaker() as session:
        user = await UserRepo(session).get(500)
        assert user is not None and user.ref_link_id is None


async def test_every_pack_button_is_handled(env) -> None:
    """Прокликиваем все кнопки пак-экранов — ни одна не должна зависнуть."""
    from app.keyboards.admin import pack_card_kb, packs_kb
    from app.utils.packs import group_packs

    dp, bot, mock, sessionmaker = env
    async with sessionmaker() as session:
        links = list(await LinkRepo(session).list_all())
    packs = group_packs(links, {})

    markups = [packs_kb(packs, 0), pack_card_kb(packs[0])]
    unhandled = []
    for idx, markup in enumerate(markups, 2000):
        for row in markup.inline_keyboard:
            for btn in row:
                if not btn.callback_data:
                    continue
                res = await dp.feed_update(bot, _cb(btn.callback_data, idx))
                idx += 1
                if res is UNHANDLED:
                    unhandled.append(btn.callback_data)
    assert not unhandled, unhandled


async def test_archived_links_excluded_from_sheets_and_buyer(session) -> None:
    """Архивный пак не должен попадать ни в узкий лист, ни в /mylinks байера."""
    from app.db.repo import BuyerRepo

    buyer = await BuyerRepo(session).create(name="Вася", tg_id=777)
    live = await LinkRepo(session).create_pack(title="Живой", count=2, buyer_id=buyer.id)
    old = await LinkRepo(session).create_pack(title="Старый", count=2, buyer_id=buyer.id)
    await _mk_user(session, 1, live[0][1], subscribe=True)
    await _mk_user(session, 2, old[0][1], subscribe=True)
    await LinkRepo(session).set_archived([link for _, link in old], True)
    await session.commit()

    # узкий лист считает только живые
    funnel = await StatsRepo(session).link_funnel(segment=Segment.MALE_ADULT)
    titles = {row.title for row in funnel}
    assert any("Живой" in t for t in titles)
    assert not any("Старый" in t for t in titles)

    # /mylinks байера показывает только живые
    active = await LinkRepo(session).list_all(buyer_id=buyer.id)
    assert all(not link.is_archived for link in active)
    assert {link.title for link in active} == {"Живой 1", "Живой 2"}

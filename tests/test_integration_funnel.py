"""Сквозные прогоны через настоящий Dispatcher: /start → фильтр → оффер → подписка."""

from __future__ import annotations

import datetime as dt

import pytest
import pytest_asyncio
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatJoinRequest,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    Update,
)
from aiogram.types import User as TgUser
from fake_bot import make_bot

from app.config import Settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.db.repo import ChannelRepo, LinkRepo, StatsRepo, UserRepo
from app.enums import Segment
from app.handlers import ROUTERS, register_routers
from app.middlewares import DbSessionMiddleware

ADMIN_ID = 1


@pytest_asyncio.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)

    settings = Settings(
        bot_token="42:TEST",
        admin_ids=[ADMIN_ID],
        db_dsn="sqlite+aiosqlite:///:memory:",
        auto_create_unknown_links=True,
        _env_file=None,
    )
    bot, session_mock = make_bot()
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.workflow_data.update(settings=settings, broadcasts=None, sheets=None)
    register_routers(dp)

    # Три канала под четыре сегмента, как в ТЗ.
    async with sessionmaker() as session:
        channels = ChannelRepo(session)
        await channels.upsert(
            chat_id=-1001, title="Derek", segments=[Segment.MALE_ADULT.value]
        )
        await channels.upsert(
            chat_id=-1002, title="Женский оффер", segments=[Segment.FEMALE_ADULT.value]
        )
        await channels.upsert(
            chat_id=-1003,
            title="Junior",
            segments=[Segment.MALE_MINOR.value, Segment.FEMALE_MINOR.value],
            allow_minors=True,
        )
        await LinkRepo(session).create(title="vasya_tiktok_01")
        await session.commit()

    yield dp, bot, session_mock, sessionmaker

    # Роутеры aiogram — объекты уровня модуля; отцепляем, чтобы следующий
    # тест смог собрать свой Dispatcher.
    for router in ROUTERS:
        router._parent_router = None
    await bot.session.close()
    await engine.dispose()


def _user(tg_id: int) -> TgUser:
    return TgUser(id=tg_id, is_bot=False, first_name="Тест", username=f"user{tg_id}")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _send(dp: Dispatcher, bot, tg_id: int, text: str, update_id: int = 1) -> None:
    await dp.feed_update(
        bot,
        Update(
            update_id=update_id,
            message=Message(
                message_id=update_id,
                date=_now(),
                chat=Chat(id=tg_id, type="private"),
                from_user=_user(tg_id),
                text=text,
            ),
        ),
    )


async def _click(dp: Dispatcher, bot, tg_id: int, data: str, update_id: int = 2) -> None:
    await dp.feed_update(
        bot,
        Update(
            update_id=update_id,
            callback_query=CallbackQuery(
                id=str(update_id),
                from_user=_user(tg_id),
                chat_instance="ci",
                data=data,
                message=Message(
                    message_id=update_id,
                    date=_now(),
                    chat=Chat(id=tg_id, type="private"),
                    from_user=_user(42),
                    text="предыдущий шаг",
                ),
            ),
        ),
    )


async def _join(dp: Dispatcher, bot, tg_id: int, chat_id: int, update_id: int = 9) -> None:
    await dp.feed_update(
        bot,
        Update(
            update_id=update_id,
            chat_join_request=ChatJoinRequest(
                chat=Chat(id=chat_id, type="channel", title="Derek"),
                from_user=_user(tg_id),
                user_chat_id=tg_id,
                date=_now(),
            ),
        ),
    )


@pytest.mark.parametrize(
    "age_cb,gender_cb,expected_channel",
    [
        ("age:1", "gender:1", "Derek"),           # парень 18+
        ("age:1", "gender:0", "Женский оффер"),   # девушка 18+
        ("age:0", "gender:1", "Junior"),          # парень <18
        ("age:0", "gender:0", "Junior"),          # девушка <18
    ],
)
async def test_four_segments_end_to_end(env, age_cb, gender_cb, expected_channel) -> None:
    dp, bot, mock, sessionmaker = env
    tg_id = 100

    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 1)
    assert "сколько вам лет?" in mock.called("SendMessage")[-1].text.lower()

    await _click(dp, bot, tg_id, age_cb, 2)
    assert "пол" in mock.called("EditMessageText")[-1].text

    await _click(dp, bot, tg_id, gender_cb, 3)
    offer = mock.called("EditMessageText")[-1]
    assert expected_channel in offer.text
    assert offer.reply_markup.inline_keyboard[0][0].url.startswith("https://t.me/+invite")

    # Инвайт создан именно с заявкой на вступление — иначе подписки не посчитать.
    invite_call = mock.called("CreateChatInviteLink")[-1]
    assert invite_call.creates_join_request is True

    async with sessionmaker() as session:
        user = await UserRepo(session).get(tg_id)
        assert user.filter_passed_at is not None
        assert user.assigned_channel.title == expected_channel
        assert user.ref_link.code == "vasya_tiktok_01"


async def test_join_request_counts_conversion(env) -> None:
    dp, bot, mock, sessionmaker = env
    tg_id = 200

    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, tg_id, "age:1", 2)
    await _click(dp, bot, tg_id, "gender:1", 3)
    await _join(dp, bot, tg_id, -1001, 4)

    assert mock.called("ApproveChatJoinRequest"), "заявка должна одобряться автоматически"

    async with sessionmaker() as session:
        user = await UserRepo(session).get(tg_id)
        assert user.is_subscribed is True
        assert user.subscribed_channel.title == "Derek"

        rows = await StatsRepo(session).link_funnel()
        row = next(r for r in rows if r.code == "vasya_tiktok_01")
        assert (row.starts, row.passed, row.converted) == (1, 1, 1)


async def test_unsubscribe_rolls_back_conversion(env) -> None:
    dp, bot, mock, sessionmaker = env
    tg_id = 300

    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, tg_id, "age:1", 2)
    await _click(dp, bot, tg_id, "gender:1", 3)
    await _join(dp, bot, tg_id, -1001, 4)

    await dp.feed_update(
        bot,
        Update(
            update_id=5,
            chat_member=ChatMemberUpdated(
                chat=Chat(id=-1001, type="channel", title="Derek"),
                from_user=_user(tg_id),
                date=_now(),
                old_chat_member=ChatMemberMember(user=_user(tg_id)),
                new_chat_member=ChatMemberLeft(user=_user(tg_id)),
            ),
        ),
    )

    async with sessionmaker() as session:
        assert (await UserRepo(session).get(tg_id)).is_subscribed is False
        rows = await StatsRepo(session).link_funnel()
        row = next(r for r in rows if r.code == "vasya_tiktok_01")
        assert (row.starts, row.passed, row.converted) == (1, 1, 0)


async def test_unknown_payload_creates_link_and_attributes_start(env) -> None:
    dp, bot, mock, sessionmaker = env
    await _send(dp, bot,400, "/start ref_petya_fb_07", 1)

    async with sessionmaker() as session:
        user = await UserRepo(session).get(400)
        assert user.ref_link is not None
        assert user.ref_link.code == "petya_fb_07"  # префикс ref_ отброшен


async def test_start_without_payload_is_tracked_as_organic(env) -> None:
    dp, bot, mock, sessionmaker = env
    await _send(dp, bot, 500, "/start", 1)

    async with sessionmaker() as session:
        user = await UserRepo(session).get(500)
        assert user is not None
        assert user.ref_link_id is None


async def test_second_start_after_filter_resends_same_offer(env) -> None:
    dp, bot, mock, sessionmaker = env
    tg_id = 600

    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, tg_id, "age:1", 2)
    await _click(dp, bot, tg_id, "gender:1", 3)
    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 4)

    last = mock.called("SendMessage")[-1]
    assert "Derek" in last.text
    assert "/reset" in last.text

    async with sessionmaker() as session:
        rows = await StatsRepo(session).link_funnel()
        row = next(r for r in rows if r.code == "vasya_tiktok_01")
        assert row.starts == 1, "повторный /start не должен удваивать Start"


async def test_reset_lets_user_pass_filter_again(env) -> None:
    dp, bot, mock, sessionmaker = env
    tg_id = 700

    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, tg_id, "age:1", 2)
    await _click(dp, bot, tg_id, "gender:1", 3)
    await _send(dp, bot, tg_id, "/reset", 4)
    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 5)
    assert "сколько вам лет?" in mock.called("SendMessage")[-1].text.lower()

    await _click(dp, bot, tg_id, "age:0", 6)
    await _click(dp, bot, tg_id, "gender:0", 7)
    async with sessionmaker() as session:
        user = await UserRepo(session).get(tg_id)
        assert user.segment == Segment.FEMALE_MINOR.value
        assert user.assigned_channel.title == "Junior"


async def test_no_matching_channel_shows_stub(env) -> None:
    dp, bot, mock, sessionmaker = env
    tg_id = 800

    async with sessionmaker() as session:
        channels = ChannelRepo(session)
        for channel in await channels.list_all():
            channel.is_active = False
        await session.commit()

    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, tg_id, "age:1", 2)
    await _click(dp, bot, tg_id, "gender:1", 3)
    assert "нет доступных каналов" in mock.called("EditMessageText")[-1].text

    async with sessionmaker() as session:
        user = await UserRepo(session).get(tg_id)
        assert user.filter_passed_at is not None  # фильтр пройден
        assert user.assigned_channel_id is None   # но оффера нет


async def test_gender_click_without_age_returns_to_first_step(env) -> None:
    """Старая кнопка после рестарта не должна ломать воронку."""
    dp, bot, mock, sessionmaker = env
    await _send(dp, bot, 900, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, 900, "gender:1", 2)
    assert "сколько вам лет?" in mock.called("EditMessageText")[-1].text.lower()


async def test_admin_menu_opens_for_admin_only(env) -> None:
    dp, bot, mock, sessionmaker = env

    await _send(dp, bot, ADMIN_ID, "/admin", 1)
    assert "Админ-панель" in mock.called("SendMessage")[-1].text

    before = len(mock.called("SendMessage"))
    await _send(dp, bot, 12345, "/admin", 2)
    assert len(mock.called("SendMessage")) == before, "не-админ не должен получать панель"


async def test_admin_stats_navigation(env) -> None:
    dp, bot, mock, sessionmaker = env

    await _send(dp, bot, 1000, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, 1000, "age:1", 2)
    await _click(dp, bot, 1000, "gender:1", 3)
    await _join(dp, bot, 1000, -1001, 4)

    await _send(dp, bot, ADMIN_ID, "/admin", 5)
    await _click(dp, bot, ADMIN_ID, "menu:stats", 6)
    text = mock.called("EditMessageText")[-1].text
    assert "Нажали Start" in text and "vasya_tiktok_01" in text

    await _click(dp, bot, ADMIN_ID, "stats:full:all", 7)
    full = mock.called("EditMessageText")[-1].text
    assert "По сегментам" in full and "Парни 18+" in full
    assert "По каналам" in full and "Derek" in full


async def test_invite_failure_falls_back_to_stub(env) -> None:
    """Нет прав на создание инвайта — юзер получает заглушку, а не ошибку."""
    dp, bot, mock, sessionmaker = env
    mock.fail_methods.add("CreateChatInviteLink")

    await _send(dp, bot, 1100, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, 1100, "age:1", 2)
    await _click(dp, bot, 1100, "gender:1", 3)
    assert "нет доступных каналов" in mock.called("EditMessageText")[-1].text


async def test_reset_is_recorded_in_event_log(env) -> None:
    """Без записи о сбросе двойное прохождение фильтра выглядит необъяснимым."""
    from sqlalchemy import select

    from app.db.models import Event
    from app.enums import EventType

    dp, bot, mock, sessionmaker = env
    tg_id = 1200

    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, tg_id, "age:1", 2)
    await _click(dp, bot, tg_id, "gender:1", 3)
    await _send(dp, bot, tg_id, "/reset", 4)

    async with sessionmaker() as session:
        events = (
            await session.scalars(
                select(Event).where(Event.user_tg_id == tg_id).order_by(Event.id)
            )
        ).all()

    types = [event.type for event in events]
    assert EventType.FILTER_RESET.value in types
    reset = next(e for e in events if e.type == EventType.FILTER_RESET.value)
    # В журнале видно, что именно сбросили.
    assert reset.data["segment_before"] == Segment.MALE_ADULT.value
    assert reset.data["channel_id_before"] is not None
    assert types.index(EventType.FILTER_RESET.value) > types.index(EventType.FILTER_PASSED.value)


async def test_reset_keeps_attribution_and_conversion(env) -> None:
    """Сброс ответов не должен обнулять уже учтённую подписку байера."""
    dp, bot, mock, sessionmaker = env
    tg_id = 1300

    await _send(dp, bot, tg_id, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, tg_id, "age:1", 2)
    await _click(dp, bot, tg_id, "gender:1", 3)
    await _join(dp, bot, tg_id, -1001, 4)
    await _send(dp, bot, tg_id, "/reset", 5)

    async with sessionmaker() as session:
        user = await UserRepo(session).get(tg_id)
        assert user.segment is None, "ответы сброшены — опрос можно пройти заново"
        assert user.ref_link.code == "vasya_tiktok_01"   # источник сохранён
        assert user.filter_passed_at is not None          # история воронки сохранена
        assert user.is_subscribed is True                 # конверсия сохранена

        rows = await StatsRepo(session).link_funnel()
        row = next(r for r in rows if r.code == "vasya_tiktok_01")
        assert (row.starts, row.passed, row.converted) == (1, 1, 1)


async def test_funnel_numbers_never_contradict_each_other(env) -> None:
    """Start >= прошли фильтр >= подписались — инвариант любого отчёта.

    Нарушается, если /reset стирает историю: тогда «подписались» может
    оказаться больше «прошли фильтр», и заказчик решит, что статистика врёт.
    """
    dp, bot, mock, sessionmaker = env

    # Юзер 1: прошёл и подписался, потом сбросил ответы и прошёл заново.
    await _send(dp, bot, 1400, "/start vasya_tiktok_01", 1)
    await _click(dp, bot, 1400, "age:1", 2)
    await _click(dp, bot, 1400, "gender:1", 3)
    await _join(dp, bot, 1400, -1001, 4)
    await _send(dp, bot, 1400, "/reset", 5)
    await _send(dp, bot, 1400, "/start vasya_tiktok_01", 6)
    await _click(dp, bot, 1400, "age:0", 7)
    await _click(dp, bot, 1400, "gender:0", 8)
    await _join(dp, bot, 1400, -1003, 9)
    # Юзер 2: только нажал Start.
    await _send(dp, bot, 1401, "/start vasya_tiktok_01", 10)
    # Юзер 3: бросил на вопросе о поле.
    await _send(dp, bot, 1402, "/start vasya_tiktok_01", 11)
    await _click(dp, bot, 1402, "age:1", 12)

    async with sessionmaker() as session:
        stats = StatsRepo(session)
        totals = await stats.totals()
        assert totals.starts >= totals.passed >= totals.converted
        assert (totals.starts, totals.passed, totals.converted) == (3, 1, 1)

        for row in await stats.link_funnel():
            assert row.starts >= row.passed >= row.converted, row
        assert all(0 <= rate <= 100 for rate in (
            totals.pass_rate, totals.conv_rate, totals.conv_of_passed
        ))


@pytest_asyncio.fixture
async def strict_env(env):
    """То же окружение, но с выключенным автосозданием ссылок."""
    dp, bot, mock, sessionmaker = env
    dp.workflow_data["settings"].auto_create_unknown_links = False
    yield dp, bot, mock, sessionmaker


async def test_unknown_payload_does_not_create_link_when_disabled(strict_env) -> None:
    """Неизвестный код не должен плодить строку в отчёте.

    Ссылки выдаёт админ: иначе опечатка в креативе («O1» вместо «01») создаёт
    вторую «ссылку-призрак», и трафик байера размазывается по двум строкам.
    """
    dp, bot, mock, sessionmaker = strict_env
    await _send(dp, bot, 2000, "/start vasya_tiktok_O1", 1)

    async with sessionmaker() as session:
        links = await LinkRepo(session).list_all()
        assert [link.code for link in links] == ["vasya_tiktok_01"], "новых ссылок быть не должно"

        user = await UserRepo(session).get(2000)
        assert user is not None, "юзер всё равно регистрируется"
        assert user.ref_link_id is None
        # Код перехода сохранён — видно, откуда пришёл, даже без ссылки.
        assert user.ref_payload == "vasya_tiktok_O1"

        organic = await StatsRepo(session).organic_funnel()
        assert organic.starts == 1


async def test_known_payload_still_works_when_disabled(strict_env) -> None:
    """Существующие ссылки продолжают работать как обычно."""
    dp, bot, mock, sessionmaker = strict_env
    await _send(dp, bot, 2100, "/start vasya_tiktok_01", 1)

    async with sessionmaker() as session:
        user = await UserRepo(session).get(2100)
        assert user.ref_link.code == "vasya_tiktok_01"
        assert (await StatsRepo(session).organic_funnel()).starts == 0


async def test_unknown_payload_reaches_export_as_raw_code(strict_env) -> None:
    """В выгрузке вместо названия ссылки виден сам код — данные не теряются."""
    from app.services.rows import user_row

    dp, bot, mock, sessionmaker = strict_env
    await _send(dp, bot, 2200, "/start petya_fb_07", 1)

    async with sessionmaker() as session:
        user = await UserRepo(session).get(2200)
        assert user_row(user)[5] == "petya_fb_07"

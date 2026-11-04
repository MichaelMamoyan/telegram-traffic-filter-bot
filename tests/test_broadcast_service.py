"""Проверка рассылщика: выборка, copy_message, блокировки, курсор, остановка."""

from __future__ import annotations

import asyncio

import pytest_asyncio
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import TelegramMethod
from fake_bot import FakeSession, make_bot

from app.db.base import Base, make_engine, make_sessionmaker
from app.db.repo import BroadcastRepo, ChannelRepo, LinkRepo, OfferRepo, UserRepo
from app.enums import AgeStatus, BroadcastStatus, Gender, Segment, resolve_segment
from app.services.broadcast import BroadcastService, buttons_markup


@pytest_asyncio.fixture
async def setup():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    bot, mock = make_bot()

    async with sessionmaker() as session:
        link = await LinkRepo(session).create(title="bc")
        channel = await ChannelRepo(session).upsert(
            chat_id=-1, title="Derek", segments=[Segment.MALE_ADULT.value]
        )
        users = UserRepo(session)
        # 1,2 — парни 18+ подписались; 3 — девушка 18+; 4 — парень <18; 5 — бросил фильтр
        plan = [
            (1, AgeStatus.ADULT, Gender.MALE, True),
            (2, AgeStatus.ADULT, Gender.MALE, True),
            (3, AgeStatus.ADULT, Gender.FEMALE, False),
            (4, AgeStatus.MINOR, Gender.MALE, False),
            (5, None, None, False),
        ]
        for tg_id, age, gender, subscribed in plan:
            user, _ = await users.register_start(
                tg_id=tg_id, username=f"u{tg_id}", first_name="T", last_name=None,
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

    yield sessionmaker, bot, mock, channel.id
    await bot.session.close()
    await engine.dispose()


async def _make_broadcast(sessionmaker, filters: dict, buttons=None) -> int:
    async with sessionmaker() as session:
        total = await UserRepo(session).count_targets(filters)
        broadcast = await BroadcastRepo(session).create(
            admin_id=1, filters=filters, from_chat_id=1, message_id=10,
            buttons=buttons, total=total,
        )
        await session.commit()
        return broadcast.id


async def _wait(service: BroadcastService, broadcast_id: int, limit: float = 5.0) -> None:
    task = service._tasks.get(broadcast_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), timeout=limit)


async def test_broadcast_reaches_only_the_selected_segment(setup) -> None:
    sessionmaker, bot, mock, _ = setup
    filters = {"gender": "male", "age": "18+", "status": "subscribed"}
    broadcast_id = await _make_broadcast(sessionmaker, filters)

    service = BroadcastService(bot, sessionmaker, rate=1000, batch=2)
    service.start(broadcast_id)
    await _wait(service, broadcast_id)

    recipients = sorted(call.chat_id for call in mock.called("CopyMessage"))
    assert recipients == [1, 2]

    async with sessionmaker() as session:
        broadcast = await BroadcastRepo(session).get(broadcast_id)
        assert broadcast.status == BroadcastStatus.DONE.value
        assert (broadcast.sent, broadcast.failed, broadcast.blocked) == (2, 0, 0)
        assert broadcast.cursor_tg_id == 2


async def test_broadcast_to_everyone_who_pressed_start(setup) -> None:
    sessionmaker, bot, mock, _ = setup
    broadcast_id = await _make_broadcast(sessionmaker, {})

    service = BroadcastService(bot, sessionmaker, rate=1000, batch=2)
    service.start(broadcast_id)
    await _wait(service, broadcast_id)

    assert sorted(call.chat_id for call in mock.called("CopyMessage")) == [1, 2, 3, 4, 5]


async def test_blocked_users_are_marked_and_counted(setup) -> None:
    sessionmaker, bot, mock, _ = setup
    broadcast_id = await _make_broadcast(sessionmaker, {"status": "filter_passed"})

    original = FakeSession.make_request

    async def failing(self, bot_, method: TelegramMethod, timeout=None):  # type: ignore[no-untyped-def]
        if type(method).__name__ == "CopyMessage" and method.chat_id == 3:
            self.calls.append(method)
            raise TelegramForbiddenError(method=method, message="bot was blocked by the user")
        return await original(self, bot_, method, timeout)

    FakeSession.make_request = failing  # type: ignore[assignment]
    try:
        service = BroadcastService(bot, sessionmaker, rate=1000, batch=10)
        service.start(broadcast_id)
        await _wait(service, broadcast_id)
    finally:
        FakeSession.make_request = original  # type: ignore[assignment]

    async with sessionmaker() as session:
        broadcast = await BroadcastRepo(session).get(broadcast_id)
        assert (broadcast.sent, broadcast.blocked) == (3, 1)
        assert (await UserRepo(session).get(3)).is_blocked is True
        # Заблокировавший больше не попадает в выборки.
        assert await UserRepo(session).count_targets({"status": "filter_passed"}) == 3


async def test_cancel_stops_broadcast_midway(setup) -> None:
    """Оператор жмёт «Остановить» — отправка прекращается, прогресс сохраняется."""
    sessionmaker, bot, mock, _ = setup
    broadcast_id = await _make_broadcast(sessionmaker, {})

    # Низкий rate, чтобы успеть отменить в середине выборки.
    service = BroadcastService(bot, sessionmaker, rate=2, batch=10)
    service.start(broadcast_id)
    await asyncio.sleep(0.05)
    service.cancel(broadcast_id)
    await _wait(service, broadcast_id)

    sent_to = [call.chat_id for call in mock.called("CopyMessage")]
    assert 0 < len(sent_to) < 5, f"должна оборваться на середине, а ушло {sent_to}"

    async with sessionmaker() as session:
        broadcast = await BroadcastRepo(session).get(broadcast_id)
        assert broadcast.status == BroadcastStatus.CANCELLED.value
        assert broadcast.sent == len(sent_to)
        # Курсор указывает на последнего получателя — можно продолжить с него.
        assert broadcast.cursor_tg_id == sent_to[-1]


async def test_restart_after_cancel_does_not_resend(setup) -> None:
    """Отменённая рассылка не подхватывается при рестарте."""
    sessionmaker, bot, mock, _ = setup
    broadcast_id = await _make_broadcast(sessionmaker, {})
    async with sessionmaker() as session:
        await BroadcastRepo(session).set_status(broadcast_id, BroadcastStatus.CANCELLED)
        await session.commit()

    service = BroadcastService(bot, sessionmaker, rate=1000, batch=10)
    assert await service.resume_unfinished() == 0
    assert mock.called("CopyMessage") == []


async def test_resume_unfinished_picks_up_queued_broadcasts(setup) -> None:
    sessionmaker, bot, mock, _ = setup
    broadcast_id = await _make_broadcast(sessionmaker, {"status": "subscribed"})

    service = BroadcastService(bot, sessionmaker, rate=1000, batch=10)
    assert await service.resume_unfinished() == 1
    await _wait(service, broadcast_id)
    assert sorted(call.chat_id for call in mock.called("CopyMessage")) == [1, 2]


async def test_resume_continues_from_cursor(setup) -> None:
    """После рестарта контейнера уже отправленные не получают сообщение дважды."""
    sessionmaker, bot, mock, _ = setup
    broadcast_id = await _make_broadcast(sessionmaker, {})
    async with sessionmaker() as session:
        await BroadcastRepo(session).bump_counters(broadcast_id, cursor=3, sent=3)
        await session.commit()

    service = BroadcastService(bot, sessionmaker, rate=1000, batch=10)
    assert await service.resume_unfinished() == 1
    await _wait(service, broadcast_id)

    assert sorted(call.chat_id for call in mock.called("CopyMessage")) == [4, 5]
    async with sessionmaker() as session:
        broadcast = await BroadcastRepo(session).get(broadcast_id)
        assert broadcast.sent == 5


async def test_buttons_are_attached_to_broadcast(setup) -> None:
    sessionmaker, bot, mock, _ = setup
    buttons = {"items": [{"text": "Перейти", "url": "https://t.me/example"}]}
    broadcast_id = await _make_broadcast(sessionmaker, {"status": "subscribed"}, buttons)

    service = BroadcastService(bot, sessionmaker, rate=1000, batch=10)
    service.start(broadcast_id)
    await _wait(service, broadcast_id)

    call = mock.called("CopyMessage")[0]
    assert call.reply_markup.inline_keyboard[0][0].text == "Перейти"
    assert call.reply_markup.inline_keyboard[0][0].url == "https://t.me/example"


def test_buttons_markup_handles_empty() -> None:
    assert buttons_markup(None) is None
    assert buttons_markup({}) is None
    assert buttons_markup({"items": []}) is None


async def test_empty_selection_finishes_immediately(setup) -> None:
    sessionmaker, bot, mock, _ = setup
    broadcast_id = await _make_broadcast(sessionmaker, {"gender": "female", "age": "<18"})

    service = BroadcastService(bot, sessionmaker, rate=1000, batch=10)
    service.start(broadcast_id)
    await _wait(service, broadcast_id)

    assert mock.called("CopyMessage") == []
    async with sessionmaker() as session:
        assert (await BroadcastRepo(session).get(broadcast_id)).status == BroadcastStatus.DONE.value

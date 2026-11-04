"""Каждая кнопка админки должна обрабатываться.

Поймали на живом боте: aiogram распаковывает пустой хвостовой сегмент
callback_data как None, поэтому поле `value: str = ""` роняло валидацию.
Фильтр молча не матчился, апдейт оставался необработанным — и кнопка висела
в бесконечной загрузке без единой строки в логах.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest
import pytest_asyncio
from aiogram import Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update
from aiogram.types import User as TgUser
from fake_bot import make_bot

from app.config import Settings
from app.db.base import Base, make_engine, make_sessionmaker
from app.db.repo import BuyerRepo, ChannelRepo, LinkRepo
from app.enums import Segment
from app.handlers import ROUTERS, register_routers
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
from app.keyboards.callbacks import (
    AgeCB,
    BroadcastCB,
    BuyerCB,
    ChannelCB,
    ExportCB,
    GenderCB,
    LinkCB,
    MenuCB,
    StatsCB,
)
from app.keyboards.funnel import age_kb, gender_kb
from app.middlewares import DbSessionMiddleware
from app.services.broadcast import BroadcastService
from app.services.sheets import SheetsSyncer

ADMIN_ID = 1

CALLBACK_CLASSES = (
    AgeCB, GenderCB, MenuCB, StatsCB, LinkCB, BuyerCB, ChannelCB, BroadcastCB, ExportCB,
)


def _callback_class(data: str):  # type: ignore[no-untyped-def]
    prefix = data.split(":", 1)[0]
    for cls in CALLBACK_CLASSES:
        if cls.__prefix__ == prefix:
            return cls
    raise AssertionError(f"нет класса CallbackData с префиксом {prefix!r} (data={data!r})")


# --------------------------------------------------------------- round-trip


def _every_keyboard() -> list[tuple[str, InlineKeyboardMarkup]]:
    """Все клавиатуры с заполненными данными — как их видит оператор."""
    from app.db.models import Broadcast, Buyer, Channel, RefLink

    channel = Channel(
        id=1, chat_id=-1001, title="Derek", segments=[Segment.MALE_ADULT.value],
        allow_minors=False, is_active=True, weight=1.0, priority=100,
    )
    junior = Channel(
        id=2, chat_id=-1002, title="Junior",
        segments=[Segment.MALE_MINOR.value, Segment.FEMALE_MINOR.value],
        allow_minors=True, is_active=False, weight=1.0, priority=100,
    )
    link = RefLink(id=1, code="vasya_tiktok_01", title="vasya_tiktok_01", is_active=True)
    buyer = Buyer(id=1, name="Вася", tg_id=777, is_active=True)
    broadcast = Broadcast(
        id=1, admin_id=ADMIN_ID, filters={}, from_chat_id=1, message_id=1,
        status="running", total=5,
    )
    return [
        ("age_kb", age_kb()),
        ("gender_kb", gender_kb()),
        ("main_menu", main_menu_kb()),
        ("stats_links", stats_kb("links", "today")),
        ("stats_buyers", stats_kb("buyers", "7d")),
        ("stats_full", stats_kb("full", "all")),
        ("links_list", links_kb([link], 0)),
        ("links_paged", links_kb([link] * 20, 1)),
        ("link_card", link_card_kb(link, "https://t.me/test_bot?start=vasya_tiktok_01")),
        ("buyers", buyers_kb([buyer])),
        ("buyer_pick", buyer_pick_kb([buyer])),
        ("channels", channels_kb([channel, junior])),
        ("channel_card_adult", channel_card_kb(channel)),
        ("channel_card_junior", channel_card_kb(junior)),
        ("broadcast_empty", broadcast_kb({}, has_message=False)),
        ("broadcast_ready", broadcast_kb(
            {"gender": "male", "age": "18+", "status": "subscribed"}, has_message=True)),
        ("broadcast_ref", broadcast_ref_kb([link])),
        ("broadcast_progress", broadcast_progress_kb(broadcast)),
        ("export_on", export_kb(True)),
        ("export_off", export_kb(False)),
        ("cancel", cancel_kb("broadcast")),
        ("nobuttons", nobuttons_kb()),
    ]


def test_every_callback_data_unpacks_back() -> None:
    """pack() → unpack() должен работать для каждой кнопки, включая пустые поля."""
    checked = 0
    for name, markup in _every_keyboard():
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data is None:
                    continue
                cls = _callback_class(button.callback_data)
                try:
                    cls.unpack(button.callback_data)
                except Exception as exc:
                    pytest.fail(
                        f"{name}: кнопка {button.text!r} с data={button.callback_data!r} "
                        f"не распаковывается обратно в {cls.__name__}: {exc}"
                    )
                checked += 1
    assert checked > 50, f"проверено подозрительно мало кнопок: {checked}"


# --------------------------------------------------------------- через диспетчер


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
    dp.workflow_data.update(
        settings=settings,
        broadcasts=BroadcastService(bot, sessionmaker, rate=1000, batch=10),
        sheets=SheetsSyncer(settings, sessionmaker),
    )
    register_routers(dp)

    async with sessionmaker() as session:
        buyer = await BuyerRepo(session).create(name="Вася", tg_id=777)
        await LinkRepo(session).create(title="vasya_tiktok_01", buyer_id=buyer.id)
        await ChannelRepo(session).upsert(
            chat_id=-1001, title="Derek", segments=[Segment.MALE_ADULT.value], is_active=True
        )
        await ChannelRepo(session).upsert(
            chat_id=-1002, title="Junior",
            segments=[Segment.MALE_MINOR.value, Segment.FEMALE_MINOR.value],
            allow_minors=True,
        )
        await session.commit()

    yield dp, bot, mock
    for router in ROUTERS:
        router._parent_router = None
    await bot.session.close()
    await engine.dispose()


def _click(data: str, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=TgUser(id=ADMIN_ID, is_bot=False, first_name="Админ"),
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=update_id,
                date=dt.datetime.now(dt.UTC),
                chat=Chat(id=ADMIN_ID, type="private"),
                text="предыдущий экран",
            ),
        ),
    )


# Кнопки, которые удаляют данные — жмём последними, чтобы не ломать остальные.
DESTRUCTIVE = ("chan:delete", "link:toggle", "chan:toggle")


async def test_every_admin_button_is_handled(env, caplog) -> None:
    dp, bot, mock = env

    buttons: list[tuple[str, str, str]] = []
    for screen, markup in _every_keyboard():
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data:
                    buttons.append((screen, button.text, button.callback_data))

    deferred = [b for b in buttons if b[2].startswith(DESTRUCTIVE)]
    ordered = [b for b in buttons if not b[2].startswith(DESTRUCTIVE)] + deferred

    unhandled: list[str] = []
    with caplog.at_level(logging.WARNING):
        for index, (screen, text, data) in enumerate(ordered, start=1000):
            result = await dp.feed_update(bot, _click(data, index))
            if result is UNHANDLED:
                unhandled.append(f"{screen}: {text!r} → {data!r}")

    assert not unhandled, "кнопки не обработаны (в Telegram это вечный спиннер):\n" + "\n".join(
        unhandled
    )

    crashed = [
        record.getMessage()
        for record in caplog.records
        if "Необработанная ошибка" in record.getMessage()
        or "Необработанный callback_query" in record.getMessage()
    ]
    assert not crashed, "хендлеры упали:\n" + "\n".join(crashed)


async def test_catch_all_answers_stale_button(env, caplog) -> None:
    """Неизвестная кнопка не должна висеть: отвечаем и пишем в лог."""
    dp, bot, mock = env
    with caplog.at_level(logging.WARNING):
        result = await dp.feed_update(bot, _click("chan:unknown_action:0:", 5555))

    assert result is not UNHANDLED
    answers = [c for c in mock.calls if type(c).__name__ == "AnswerCallbackQuery"]
    assert answers, "на устаревшую кнопку обязан прийти answer()"


# --------------------------------------------------------------- раскладка

# Сколько символов подписи Telegram показывает, не обрезая, при N кнопках в строке.
# Эмодзи считаем за символ — оценка намеренно консервативная.
MAX_LABEL_BY_WIDTH = {1: 32, 2: 20, 3: 12}


def test_no_row_crams_buttons() -> None:
    """Регрессия на «кнопки втиснуты в 1–2 строки и подписей не видно».

    builder.row() без аргументов новую строку не открывает, поэтому кнопки
    склеивались в один ряд, и Telegram обрезал подписи многоточием.
    """
    problems: list[str] = []
    for screen, markup in _every_keyboard():
        for index, row in enumerate(markup.inline_keyboard):
            width = len(row)
            if width > 3:
                problems.append(f"{screen}, строка {index}: {width} кнопок в ряд")
                continue
            limit = MAX_LABEL_BY_WIDTH[width]
            for button in row:
                if len(button.text) > limit:
                    problems.append(
                        f"{screen}, строка {index}: при {width} кнопках в ряд подпись "
                        f"{button.text!r} ({len(button.text)} симв.) не влезет (лимит {limit})"
                    )
    assert not problems, "подписи обрежутся:\n" + "\n".join(problems)


def test_segment_toggles_are_one_per_row() -> None:
    """Оператор должен видеть, какой именно сегмент включает."""
    from app.db.models import Channel

    for allow_minors in (False, True):
        channel = Channel(
            id=1, chat_id=-1, title="Derek", segments=[], allow_minors=allow_minors,
            is_active=True, weight=1.0, priority=100,
        )
        rows = channel_card_kb(channel).inline_keyboard
        segment_rows = [
            row for row in rows
            if any(b.callback_data and b.callback_data.startswith("chan:seg") for b in row)
        ]
        assert len(segment_rows) == (4 if allow_minors else 2)
        for row in segment_rows:
            assert len(row) == 1, f"сегменты должны идти по одному в строке, а не {len(row)}"
        for row in segment_rows:
            assert row[0].text.startswith(("✅", "⬜️")), row[0].text


def test_every_screen_has_a_way_back() -> None:
    """Из любого экрана должен быть выход, иначе оператор застревает."""
    # main_menu — корневой экран, из него выходить некуда.
    skip = {"main_menu", "age_kb", "gender_kb", "buyer_pick", "cancel", "nobuttons"}
    for screen, markup in _every_keyboard():
        if screen in skip:
            continue
        labels = [b.text for row in markup.inline_keyboard for b in row]
        assert any("⬅️" in label for label in labels), f"{screen}: нет кнопки назад"


# --------------------------------------------------------------- копирование ссылки


def test_link_card_has_native_copy_button() -> None:
    """Ссылку нужно забирать одним тапом, а не чистить от текста сообщения."""
    from app.db.models import RefLink
    from app.keyboards.admin import deep_link, link_card_kb

    link = RefLink(id=1, code="vasya_tiktok_01", title="vasya_tiktok_01", is_active=True)
    url = deep_link("trafficfiltero_bot", link.code)
    assert url == "https://t.me/trafficfiltero_bot?start=vasya_tiktok_01"

    buttons = [b for row in link_card_kb(link, url).inline_keyboard for b in row]
    copy_buttons = [b for b in buttons if b.copy_text is not None]
    assert len(copy_buttons) == 1
    # В буфер должна попасть ровно ссылка, без подписей и разметки.
    assert copy_buttons[0].copy_text.text == url
    assert copy_buttons[0].callback_data is None


def test_my_links_kb_gives_a_copy_button_per_active_link() -> None:
    from app.db.models import RefLink
    from app.keyboards.admin import my_links_kb

    links = [
        RefLink(id=1, code="a", title="Вася ТикТок", is_active=True),
        RefLink(id=2, code="b", title="Вася ФБ", is_active=True),
        RefLink(id=3, code="c", title="Выключенная", is_active=False),
    ]
    rows = my_links_kb(links, "trafficfiltero_bot").inline_keyboard
    assert len(rows) == 2, "выключенные ссылки байеру копировать незачем"
    assert [row[0].copy_text.text for row in rows] == [
        "https://t.me/trafficfiltero_bot?start=a",
        "https://t.me/trafficfiltero_bot?start=b",
    ]
    assert rows[0][0].text == "📋 Вася ТикТок"


def test_copy_payload_stays_within_telegram_limit() -> None:
    from app.db.models import RefLink
    from app.keyboards.admin import deep_link, link_card_kb

    link = RefLink(id=1, code="x" * 48, title="t", is_active=True)
    url = deep_link("a" * 32, link.code)
    button = link_card_kb(link, url).inline_keyboard[0][0]
    assert len(button.copy_text.text) <= 256

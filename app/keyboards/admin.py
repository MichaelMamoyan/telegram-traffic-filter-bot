from __future__ import annotations

from collections.abc import Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import Broadcast, Buyer, Channel, RefLink
from app.enums import SEGMENT_TITLES, Segment
from app.keyboards.callbacks import (
    BroadcastCB,
    BuyerCB,
    ChannelCB,
    ExportCB,
    LinkCB,
    MenuCB,
    StatsCB,
)
from app.utils.periods import PERIODS

PAGE_SIZE = 8

Row = list[InlineKeyboardButton]

# Раскладка задаётся явными строками, а не InlineKeyboardBuilder.adjust():
# builder.row() без аргументов новую строку НЕ открывает, из-за чего кнопки
# склеивались в один ряд и Telegram обрезал подписи многоточием.


def _btn(text: str, callback: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback.pack())


def _kb(*rows: Row) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])


def _copy_btn(text: str, payload: str) -> InlineKeyboardButton:
    """Родная кнопка копирования (Bot API 7.11): один тап — ссылка в буфере.

    Текст сообщения всё равно содержит URL в <code>, так что на старых
    клиентах остаётся привычный способ скопировать вручную.
    """
    return InlineKeyboardButton(text=text, copy_text=CopyTextButton(text=payload[:256]))


def deep_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start={code}"


def _menu_row(section: str = "root", text: str = "⬅️ Меню") -> Row:
    return [_btn(text, MenuCB(section=section))]


def _chunk(buttons: Row, per_row: int) -> list[Row]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def main_menu_kb() -> InlineKeyboardMarkup:
    return _kb(
        [
            _btn("📊 Статистика", MenuCB(section="stats")),
            _btn("🔗 Ссылки", MenuCB(section="links")),
        ],
        [
            _btn("📢 Каналы", MenuCB(section="channels")),
            _btn("👥 Байеры", MenuCB(section="buyers")),
        ],
        [
            _btn("✉️ Рассылка", MenuCB(section="broadcast")),
            _btn("📤 Экспорт", MenuCB(section="export")),
        ],
    )


def stats_kb(view: str, period: str) -> InlineKeyboardMarkup:
    titles = {"today": "Сегодня", "7d": "7 дней", "30d": "30 дней", "all": "Всё время"}
    periods = [
        _btn(f"{'🔘 ' if value == period else ''}{titles[value]}", StatsCB(view=view, period=value))
        for value in PERIODS
    ]
    views = {"links": "По ссылкам", "buyers": "По байерам", "full": "Сегменты и каналы"}
    return _kb(
        *_chunk(periods, 2),
        [
            _btn(
                f"{'▪️ ' if value == view else ''}{title}",
                StatsCB(view=value, period=period),
            )
            for value, title in list(views.items())[:2]
        ],
        [
            _btn(
                f"{'▪️ ' if view == 'full' else ''}{views['full']}",
                StatsCB(view="full", period=period),
            )
        ],
        _menu_row(),
    )


def links_kb(links: Sequence[RefLink], page: int = 0) -> InlineKeyboardMarkup:
    chunk = links[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows: list[Row] = [
        [
            _btn(
                f"{'' if link.is_active else '⏸ '}{link.title}",
                LinkCB(action="open", link_id=link.id),
            )
        ]
        for link in chunk
    ]
    nav: Row = []
    if page > 0:
        nav.append(_btn("⬅️ Назад", LinkCB(action="list", page=page - 1)))
    if (page + 1) * PAGE_SIZE < len(links):
        nav.append(_btn("Вперёд ➡️", LinkCB(action="list", page=page + 1)))
    return _kb(
        *rows,
        nav,
        [_btn("➕ Новая ссылка", LinkCB(action="new"))],
        [_btn("📦 Создать пак ссылок", LinkCB(action="pack"))],
        _menu_row(),
    )


def link_card_kb(link: RefLink, url: str) -> InlineKeyboardMarkup:
    return _kb(
        [_copy_btn("📋 Скопировать ссылку", url)],
        [
            _btn(
                "⏸ Выключить ссылку" if link.is_active else "▶️ Включить ссылку",
                LinkCB(action="toggle", link_id=link.id),
            )
        ],
        [_btn("⬅️ К списку ссылок", LinkCB(action="list"))],
        _menu_row(),
    )


def my_links_kb(links: Sequence[RefLink], bot_username: str) -> InlineKeyboardMarkup:
    """Экран байера: по кнопке копирования на каждую его ссылку."""
    return _kb(
        *[
            [_copy_btn(f"📋 {link.title}", deep_link(bot_username, link.code))]
            for link in links
            if link.is_active
        ]
    )


def buyers_kb(buyers: Sequence[Buyer]) -> InlineKeyboardMarkup:
    rows: list[Row] = [
        [
            _btn(
                f"{buyer.name}{f' (id {buyer.tg_id})' if buyer.tg_id else ''}",
                BuyerCB(action="open", buyer_id=buyer.id),
            )
        ]
        for buyer in buyers
    ]
    return _kb(*rows, [_btn("➕ Новый байер", BuyerCB(action="new"))], _menu_row())


def buyer_pick_kb(buyers: Sequence[Buyer]) -> InlineKeyboardMarkup:
    rows: list[Row] = [
        [_btn(buyer.name, BuyerCB(action="pick", buyer_id=buyer.id))] for buyer in buyers
    ]
    return _kb(*rows, [_btn("— Без байера —", BuyerCB(action="pick", buyer_id=0))])


def channels_kb(channels: Sequence[Channel]) -> InlineKeyboardMarkup:
    rows: list[Row] = [
        [
            _btn(
                f"{'🟢' if channel.is_active and channel.segments else '🔴'} {channel.title}",
                ChannelCB(action="open", channel_id=channel.id),
            )
        ]
        for channel in channels
    ]
    return _kb(*rows, [_btn("➕ Добавить канал", ChannelCB(action="new"))], _menu_row())


def channel_card_kb(channel: Channel) -> InlineKeyboardMarkup:
    active = set(channel.segments or [])
    # По одной кнопке в строке: «⬜️ Девушки до 18» в двух столбцах не влезает.
    # Галочка против пустого квадрата читается лучше похожих ✅/☑️.
    segment_rows: list[Row] = [
        [
            _btn(
                f"{'✅' if segment.value in active else '⬜️'} {SEGMENT_TITLES[segment]}",
                ChannelCB(action="seg", channel_id=channel.id, value=segment.value),
            )
        ]
        for segment in Segment
        if channel.allow_minors or not segment.is_minor
    ]
    return _kb(
        *segment_rows,
        [
            _btn(
                "🔓 Разрешить до 18" if not channel.allow_minors else "🔒 Только 18+",
                ChannelCB(action="minors", channel_id=channel.id),
            )
        ],
        [
            _btn(
                "⏸ Выключить канал" if channel.is_active else "▶️ Включить канал",
                ChannelCB(action="toggle", channel_id=channel.id),
            )
        ],
        [_btn("🔄 Новая инвайт-ссылка", ChannelCB(action="refresh", channel_id=channel.id))],
        [_btn("🗑 Удалить канал", ChannelCB(action="delete", channel_id=channel.id))],
        [_btn("⬅️ К списку каналов", ChannelCB(action="list"))],
        _menu_row(),
    )


def broadcast_kb(filters: dict, has_message: bool) -> InlineKeyboardMarkup:
    rows: list[Row] = []
    for field, options in (
        ("gender", (("all", "Все"), ("male", "Парни"), ("female", "Девушки"))),
        ("age", (("all", "Любой"), ("18+", "18+"), ("<18", "до 18"))),
    ):
        current = filters.get(field, "all")
        rows.append(
            [
                _btn(
                    f"{'🔘 ' if value == current else ''}{title}",
                    BroadcastCB(action="set", field=field, value=value),
                )
                for value, title in options
            ]
        )

    current_status = filters.get("status", "all")
    for value, title in (
        ("all", "Все, кто нажал Start"),
        ("filter_passed", "Прошли фильтр"),
        ("not_subscribed", "Не подписались"),
        ("subscribed", "Подписались"),
        ("no_filter", "Бросили фильтр"),
    ):
        rows.append(
            [
                _btn(
                    f"{'🔘 ' if value == current_status else ''}{title}",
                    BroadcastCB(action="set", field="status", value=value),
                )
            ]
        )

    rows.append([_btn("🔗 Фильтр по ссылке", BroadcastCB(action="pick_ref"))])
    rows.append(
        [
            _btn(
                "📝 Загрузить сообщение" if not has_message else "📝 Заменить сообщение",
                BroadcastCB(action="msg"),
            )
        ]
    )
    if has_message:
        rows.append(
            [
                _btn("🔘 Кнопки", BroadcastCB(action="buttons")),
                _btn("👁 Предпросмотр", BroadcastCB(action="preview")),
            ]
        )
        rows.append([_btn("🚀 Запустить рассылку", BroadcastCB(action="run"))])
    rows.append([_btn("📜 История рассылок", BroadcastCB(action="list"))])
    rows.append(_menu_row())
    return _kb(*rows)


def broadcast_ref_kb(links: Sequence[RefLink]) -> InlineKeyboardMarkup:
    rows: list[Row] = [
        [_btn("— Все ссылки —", BroadcastCB(action="set", field="ref_link_id", value="0"))]
    ]
    rows += [
        [_btn(link.title, BroadcastCB(action="set", field="ref_link_id", value=str(link.id)))]
        for link in links[:40]
    ]
    rows.append(_menu_row("broadcast", "⬅️ К рассылке"))
    return _kb(*rows)


def broadcast_progress_kb(broadcast: Broadcast) -> InlineKeyboardMarkup:
    return _kb(
        [
            _btn("🔄 Обновить", BroadcastCB(action="status", broadcast_id=broadcast.id)),
            _btn("🛑 Остановить", BroadcastCB(action="cancel", broadcast_id=broadcast.id)),
        ],
        _menu_row(),
    )


def export_kb(sheets_enabled: bool) -> InlineKeyboardMarkup:
    rows: list[Row] = [[_btn("📄 Выгрузить XLSX", ExportCB(action="xlsx"))]]
    if sheets_enabled:
        rows.append([_btn("🔄 Синхронизировать Sheets", ExportCB(action="sheets_sync"))])
        rows.append([_btn("♻️ Перезалить Sheets целиком", ExportCB(action="sheets_full"))])
    rows.append(_menu_row())
    return _kb(*rows)


def cancel_kb(section: str = "root") -> InlineKeyboardMarkup:
    return _kb([_btn("✖️ Отмена", MenuCB(section=section))])


def nobuttons_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("Без кнопок", BroadcastCB(action="nobuttons"))],
        [_btn("✖️ Отмена", MenuCB(section="broadcast"))],
    )

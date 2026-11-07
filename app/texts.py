from __future__ import annotations

from html import escape

from app.db.repo import FunnelRow
from app.enums import SEGMENT_TITLES, Segment

# ---------------------------------------------------------------- воронка (юзер)

ASK_AGE = "👋 Привет!\n\nПрежде чем продолжить — <b>сколько вам лет?</b>"
ASK_GENDER = "Отлично! Теперь укажите <b>ваш пол</b>:"

OFFER_DEFAULT = (
    "✅ Готово! Мы подобрали для вас канал:\n\n"
    "<b>{channel}</b>\n\n"
    "Нажмите кнопку ниже и подтвердите вступление."
)
OFFER_BUTTON_DEFAULT = "🚀 Перейти в канал"

NO_OFFER = (
    "😔 К сожалению, сейчас для вас нет доступных каналов.\n"
    "Попробуйте зайти позже — мы постоянно добавляем новые."
)

ALREADY_DONE = "Вы уже прошли опрос. Ваш канал:\n\n<b>{channel}</b>"
JOIN_APPROVED = "🎉 Заявка одобрена! Канал <b>{channel}</b> уже у вас в списке чатов."
RESTART_HINT = "Чтобы пройти опрос заново, напишите /reset"
RESET_DONE = "Ответы сброшены. Нажмите /start, чтобы пройти опрос заново."

BTN_AGE_ADULT = "Мне есть 18"
BTN_AGE_MINOR = "Мне нет 18"
BTN_GENDER_MALE = "Я парень"
BTN_GENDER_FEMALE = "Я девушка"

# ---------------------------------------------------------------- админка

ADMIN_MENU = (
    "🛠 <b>Админ-панель</b>\n\n"
    "Старты: <b>{starts}</b> · Прошли фильтр: <b>{passed}</b> · Подписались: <b>{converted}</b>\n"
    "Активных каналов: <b>{channels}</b> · Ссылок: <b>{links}</b>"
)

NOT_ALLOWED = "⛔️ Нет доступа."

PERIOD_TITLES = {
    "today": "сегодня",
    "7d": "7 дней",
    "30d": "30 дней",
    "all": "всё время",
}


def esc(value: object) -> str:
    """Экранирование для parse_mode=HTML.

    Названия каналов, ссылок и байеров приходят от Telegram и от админа —
    любой `<` в них иначе ломает отправку сообщения целиком.
    """
    return escape(str(value), quote=False)


def funnel_line(row: FunnelRow) -> str:
    flag = "" if row.is_active else " ⏸"
    owner = f" · {esc(row.buyer_name)}" if row.buyer_name else ""
    return (
        f"<b>{esc(row.title)}</b>{flag}{owner}\n"
        f"<code>{esc(row.code)}</code>\n"
        f"Start: <b>{row.starts}</b> → фильтр: <b>{row.passed}</b> ({row.pass_rate:.0f}%) "
        f"→ подписка: <b>{row.converted}</b> ({row.conv_rate:.0f}% от Start / "
        f"{row.conv_of_passed:.0f}% от фильтра)"
    )


# В одно сообщение Telegram влезает 4096 символов, а строка отчёта по ссылке
# занимает около 150. Паками создают сотни ссылок, поэтому список режем:
# показываем самые нагруженные, остальное — в выгрузке.
STATS_MAX_ROWS = 12


def stats_report(
    title: str,
    period: str,
    totals: FunnelRow,
    rows: list[FunnelRow],
    segments: dict[str, dict[str, int]] | None = None,
    channels: list[tuple[str, int, int]] | None = None,
    max_rows: int = STATS_MAX_ROWS,
) -> str:
    parts = [
        f"📊 <b>{esc(title)}</b> · {PERIOD_TITLES.get(period, period)}",
        "",
        f"Нажали Start: <b>{totals.starts}</b>",
        f"Прошли фильтр: <b>{totals.passed}</b> ({totals.pass_rate:.1f}%)",
        f"Подписались: <b>{totals.converted}</b> ({totals.conv_rate:.1f}% от Start / "
        f"{totals.conv_of_passed:.1f}% от фильтра)",
    ]
    if rows:
        with_traffic = [row for row in rows if row.starts]
        idle = len(rows) - len(with_traffic)
        shown = with_traffic[:max_rows]

        if shown:
            parts += ["", "— — —", ""]
            parts += [funnel_line(row) for row in shown]

        if not with_traffic:
            parts += ["", f"Переходов по ссылкам пока нет (создано: {len(rows)})."]
        else:
            tail = []
            if len(with_traffic) > len(shown):
                tail.append(f"ещё {len(with_traffic) - len(shown)} с трафиком")
            if idle:
                tail.append(f"{idle} без переходов")
            if tail:
                parts += ["", f"Скрыто: {', '.join(tail)}. Полный список — 📤 Экспорт → XLSX."]
    if segments:
        parts += ["", "<b>По сегментам:</b>"]
        for segment in Segment:
            data = segments.get(segment.value)
            if not data:
                continue
            parts.append(
                f"· {SEGMENT_TITLES[segment]}: {data['starts']} → подписка {data['converted']}"
            )
        if no_filter := segments.get("no_filter"):
            parts.append(f"· Не прошли фильтр: {no_filter['starts']}")
    if channels:
        parts += ["", "<b>По каналам:</b>"]
        for name, issued, joined in channels:
            rate = joined / issued * 100 if issued else 0
            parts.append(f"· {esc(name)}: выдано {issued} → вступило {joined} ({rate:.0f}%)")
    return "\n".join(parts)


def link_card(code: str, title: str, bot_username: str, row: FunnelRow | None = None) -> str:
    url = f"https://t.me/{bot_username}?start={code}"
    text = [f"🔗 <b>{esc(title)}</b>", f"<code>{esc(url)}</code>"]
    if row:
        text += [
            "",
            f"Start: <b>{row.starts}</b> → фильтр: <b>{row.passed}</b> ({row.pass_rate:.0f}%) "
            f"→ подписка: <b>{row.converted}</b> ({row.conv_rate:.0f}%)",
        ]
    return "\n".join(text)


CHANNEL_RIGHTS_OK = "✅ бот админ, может выдавать инвайты"
CHANNEL_RIGHTS_NO_INVITE = (
    "⚠️ нет права «Добавление подписчиков» — инвайты не выдать, подписки не посчитать"
)
CHANNEL_RIGHTS_NOT_ADMIN = "⛔️ бот не администратор канала"
CHANNEL_RIGHTS_UNKNOWN = "❔ не удалось проверить (канал удалён или бота выгнали)"


PACK_MAX = 100
PACK_ASK_TITLE = (
    "Пришлите название пака — по нему будут названы ссылки.\n\n"
    "Например <code>Kiril</code> → ссылки <code>Kiril 1</code>, <code>Kiril 2</code> и так далее."
)
PACK_ASK_COUNT = "Сколько ссылок создать? Пришлите число от 1 до {max}."


def link_pack_messages(
    title: str, pack: list[tuple[int, object]], bot_username: str, limit: int = 3500
) -> list[str]:
    """Нумерованный список ссылок, разбитый по лимиту сообщения Telegram.

    В одно сообщение влезает около 60 ссылок, поэтому длинные паки уходят
    несколькими — иначе Telegram отклонит отправку целиком.
    """
    header = f"🔗 <b>{esc(title)}</b> — {len(pack)} шт.\n\n"
    messages: list[str] = []
    chunk = header

    for number, link in pack:
        line = f"{number}) https://t.me/{bot_username}?start={link.code}\n"  # type: ignore[attr-defined]
        if len(chunk) + len(line) > limit:
            messages.append(chunk.rstrip())
            chunk = ""
        chunk += line

    if chunk.strip():
        messages.append(chunk.rstrip())
    return messages


def channel_card(
    title: str,
    chat_id: int,
    segments: list[str],
    allow_minors: bool,
    is_active: bool,
    weight: float,
    has_invite: bool,
    rights: str = CHANNEL_RIGHTS_UNKNOWN,
) -> str:
    names = ", ".join(SEGMENT_TITLES[Segment(s)] for s in segments) or "— не выбраны —"
    return (
        f"📢 <b>{esc(title)}</b>\n"
        f"chat_id: <code>{chat_id}</code>\n"
        f"Сегменты: {names}\n"
        f"18+ контент: {'нет (можно до 18)' if allow_minors else 'да (младше 18 отсеиваются)'}\n"
        f"Вес в ротации: {weight:g}\n"
        f"Права бота: {rights}\n"
        f"Статус: {'🟢 активен' if is_active else '🔴 выключен'}"
    )


def export_screen(spreadsheet_id: str, interval: int) -> str:
    """Экран экспорта должен сам отвечать на вопрос «а куда оно уходит»."""
    parts = [
        "📤 <b>Экспорт базы</b>",
        "",
        "<b>📄 XLSX</b> — пришлю файлом сюда, в этот чат.",
        "Листы: Сводка, Парни 18+, Девушки 18+, Младше 18, Без фильтра.",
        "",
    ]
    if spreadsheet_id:
        url = f"https://docs.google.com/spreadsheets/d/{esc(spreadsheet_id)}"
        parts += [
            "<b>🔄 Google Sheets</b> — пишу в подключённую таблицу:",
            f"{url}",
            "",
            f"Синхронизация идёт сама каждые {interval} сек — кнопка нужна, "
            "только чтобы не ждать.",
            "«Перезалить целиком» очищает листы и раскладывает базу заново.",
        ]
    else:
        parts += [
            "<b>🔄 Google Sheets</b> — не подключён.",
            "Нужны GOOGLE_SPREADSHEET_ID и ключ сервисного аккаунта в secrets/.",
            "Без них бот работает как обычно, выгрузка идёт только в XLSX.",
        ]
    return "\n".join(parts)


BROADCAST_FILTER_TITLES = {
    "gender": {"all": "все", "male": "парни", "female": "девушки"},
    "age": {"all": "любой", "18+": "18+", "<18": "младше 18"},
    "status": {
        "all": "все, кто нажал Start",
        "no_filter": "не прошли фильтр",
        "filter_passed": "прошли фильтр",
        "not_subscribed": "прошли фильтр, но не подписались",
        "subscribed": "подписались",
    },
}


def broadcast_draft(filters: dict, total: int, has_message: bool, buttons: int) -> str:
    gender = BROADCAST_FILTER_TITLES["gender"][filters.get("gender", "all")]
    age = BROADCAST_FILTER_TITLES["age"][filters.get("age", "all")]
    status = BROADCAST_FILTER_TITLES["status"][filters.get("status", "all")]
    ref = esc(filters.get("ref_title") or "все ссылки")
    return (
        "✉️ <b>Новая рассылка</b>\n\n"
        f"Пол: <b>{gender}</b>\n"
        f"Возраст: <b>{age}</b>\n"
        f"Статус: <b>{status}</b>\n"
        f"Источник: <b>{ref}</b>\n\n"
        f"👥 Получателей: <b>{total}</b>\n"
        f"Сообщение: <b>{'загружено' if has_message else 'не загружено'}</b>"
        + (f" · кнопок: {buttons}" if buttons else "")
    )


BROADCAST_ASK_MESSAGE = (
    "Пришлите сообщение для рассылки — текст, фото, видео, GIF, кружок или голосовое.\n\n"
    "Оно будет скопировано получателям <b>как есть</b> (через copy_message), "
    "без пометки «переслано»."
)

BROADCAST_ASK_BUTTONS = (
    "Пришлите кнопки по одной в строке в формате:\n\n"
    "<code>Текст кнопки - https://t.me/example</code>\n\n"
    "Или нажмите «Без кнопок»."
)

BROADCAST_STARTED = "🚀 Рассылка #{id} запущена на {total} получателей."
BROADCAST_PROGRESS = (
    "📨 <b>Рассылка #{id}</b> — {status}\n\n"
    "Доставлено: <b>{sent}</b>\n"
    "Заблокировали бота: <b>{blocked}</b>\n"
    "Ошибок: <b>{failed}</b>\n"
    "Всего в выборке: <b>{total}</b>"
)

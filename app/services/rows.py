from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

from app.db.models import User
from app.enums import Segment

# Листы выгрузки (п.4 ТЗ): сводка + отдельная база на каждую аудиторию.
TAB_SUMMARY = "Сводка"
TAB_MALE_ADULT = "Парни 18+"
TAB_FEMALE_ADULT = "Девушки 18+"
TAB_MINORS = "Младше 18"
TAB_NO_FILTER = "Без фильтра"
# Узкий лист под быстрый просмотр: какая ссылка сколько привела.
# Полная сводка остаётся на своём листе — этот нужен, чтобы цифры можно
# было читать с телефона и не выдёргивать их из девяти колонок.
TAB_SHORT = "Коротко"

USER_TABS = (TAB_MALE_ADULT, TAB_FEMALE_ADULT, TAB_MINORS, TAB_NO_FILTER)

USER_HEADERS = [
    "ID аккаунта",
    "Username",
    "Имя",
    "Возраст",
    "Пол",
    "Ссылка входа",
    "Байер",
    "Канал",
    "Подписался",
    "Дата входа",
    "Дата подписки",
]

SHORT_HEADERS = ["Ссылка", "Подписалось"]

SUMMARY_HEADERS = [
    "Ссылка",
    "Название",
    "Байер",
    "Нажали Start",
    "Прошли фильтр",
    "% фильтра",
    "Подписались",
    "% от Start",
    "% от фильтра",
]

GENDER_TITLES = {"male": "Парень", "female": "Девушка"}


def tab_for(user: User) -> str:
    if not user.segment:
        return TAB_NO_FILTER
    segment = Segment(user.segment)
    if segment.is_minor:
        return TAB_MINORS
    return TAB_MALE_ADULT if segment is Segment.MALE_ADULT else TAB_FEMALE_ADULT


def _fmt_dt(value: dt.datetime | None, tz: str = "UTC") -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S")


def user_row(user: User, tz: str = "UTC") -> list[str]:
    """Одна строка выгрузки. Порядок обязан совпадать с USER_HEADERS."""
    return [
        str(user.tg_id),
        f"@{user.username}" if user.username else "",
        " ".join(filter(None, (user.first_name, user.last_name))),
        user.age_status or "",
        GENDER_TITLES.get(user.gender or "", ""),
        user.ref_link.title if user.ref_link else (user.ref_payload or "без ссылки"),
        user.ref_link.buyer.name if user.ref_link and user.ref_link.buyer else "",
        user.subscribed_channel.title
        if user.subscribed_channel
        else (user.assigned_channel.title if user.assigned_channel else ""),
        "да" if user.is_subscribed else "нет",
        _fmt_dt(user.started_at, tz),
        _fmt_dt(user.subscribed_at, tz),
    ]


_PACK_SUFFIX = re.compile(r"^(?P<base>.+)_(?P<number>\d+)$")


def pack_key(code: str) -> tuple[str, int]:
    """Разбирает код `kiril_7` на пак и номер внутри пака.

    Нужно, чтобы ссылки одного пака шли в таблице подряд и по порядку
    номеров, а не вперемешку по объёму трафика.
    """
    match = _PACK_SUFFIX.match(code)
    if not match:
        return code, 0
    return match.group("base"), int(match.group("number"))


def short_rows(funnel: list) -> list[list[str]]:
    """Ссылка и число подписок, сгруппировано по пакам.

    Показываем все ссылки, включая нулевые: пак раздаётся байерам целиком,
    и строка с нулём отвечает на вопрос «эта ссылка ещё не отработала».
    Ограничения на длину тут нет — это лист таблицы, а не сообщение.

    Порядок: паки по убыванию подписок, внутри пака — по номеру. Так строки
    одного названия стоят рядом, а сильные паки видно сверху.
    """
    packs: dict[str, list] = {}
    for row in funnel:
        packs.setdefault(pack_key(row.code)[0], []).append(row)

    ordered = sorted(
        packs.items(),
        key=lambda item: (-sum(r.converted for r in item[1]), item[0]),
    )
    return [
        [row.title or row.code, str(row.converted)]
        for _, rows in ordered
        for row in sorted(rows, key=lambda r: pack_key(r.code)[1])
    ]


def summary_rows(funnel: list) -> list[list[str]]:
    rows = []
    for row in funnel:
        rows.append(
            [
                row.code,
                row.title,
                row.buyer_name or "",
                str(row.starts),
                str(row.passed),
                f"{row.pass_rate:.1f}%",
                str(row.converted),
                f"{row.conv_rate:.1f}%",
                f"{row.conv_of_passed:.1f}%",
            ]
        )
    return rows

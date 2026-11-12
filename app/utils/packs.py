"""Паки реферальных ссылок: `kiril_1 … kiril_10` — один пак `kiril`.

Пак не хранится в базе отдельной сущностью: он выводится из кодов ссылок.
Так паки появляются и у ссылок, созданных поштучно с номерами в названии,
и ничего не ломается при удалении отдельных ссылок из середины.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models import RefLink

_PACK_SUFFIX = re.compile(r"^(?P<base>.+)_(?P<number>\d+)$")


def pack_key(code: str) -> tuple[str, int]:
    """Разбирает код `kiril_7` на пак и номер внутри пака."""
    match = _PACK_SUFFIX.match(code)
    if not match:
        return code, 0
    return match.group("base"), int(match.group("number"))


def pack_id(base: str) -> str:
    """Короткий стабильный идентификатор пака для callback_data.

    Сам код пака может быть длиной до 48 символов и не влез бы в лимит
    Telegram на callback_data (64 байта) вместе с префиксом и действием.
    """
    return hashlib.sha1(base.encode()).hexdigest()[:10]


def title_without_number(title: str, code: str) -> str:
    """Имя пака из названия ссылки: «ММ 7» → «ММ». Без номера — как есть."""
    head = title.rsplit(" ", 1)
    if len(head) == 2 and head[1].isdigit():
        return head[0]
    return title or pack_key(code)[0]


@dataclass(slots=True)
class PackSummary:
    base: str
    title: str
    links: list[RefLink] = field(default_factory=list)
    starts: int = 0
    converted: int = 0

    @property
    def id(self) -> str:
        return pack_id(self.base)

    @property
    def size(self) -> int:
        return len(self.links)

    @property
    def is_single(self) -> bool:
        return self.size == 1 and pack_key(self.links[0].code)[1] == 0

    @property
    def is_archived(self) -> bool:
        return bool(self.links) and all(link.is_archived for link in self.links)

    @property
    def created_at(self) -> dt.datetime | None:
        stamps = [link.created_at for link in self.links if link.created_at]
        return min(stamps) if stamps else None

    def age_days(self, now: dt.datetime | None = None) -> int:
        created = self.created_at
        if created is None:
            return 0
        now = now or dt.datetime.now(dt.UTC)
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.UTC)
        return max((now - created).days, 0)

    @property
    def link_ids(self) -> list[int]:
        return [link.id for link in self.links]


def group_packs(
    links: list[RefLink],
    counts: dict[int, tuple[int, int]] | None = None,
) -> list[PackSummary]:
    """Собирает ссылки в паки. `counts` — {link_id: (переходы, подписки)}.

    Порядок: по убыванию подписок, затем переходов, затем по имени —
    сильные паки сверху. Внутри пака ссылки по номеру.
    """
    counts = counts or {}
    by_base: dict[str, PackSummary] = {}
    for link in links:
        base, _ = pack_key(link.code)
        pack = by_base.get(base)
        if pack is None:
            pack = by_base[base] = PackSummary(
                base=base, title=title_without_number(link.title, link.code)
            )
        pack.links.append(link)
        starts, converted = counts.get(link.id, (0, 0))
        pack.starts += starts
        pack.converted += converted

    for pack in by_base.values():
        pack.links.sort(key=lambda link: (pack_key(link.code)[1], link.id))

    return sorted(
        by_base.values(),
        key=lambda pack: (-pack.converted, -pack.starts, pack.title.lower()),
    )


def find_pack(packs: list[PackSummary], wanted_id: str) -> PackSummary | None:
    return next((pack for pack in packs if pack.id == wanted_id), None)

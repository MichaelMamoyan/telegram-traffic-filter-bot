from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import (
    Broadcast,
    Buyer,
    Channel,
    Event,
    Offer,
    RefLink,
    SheetState,
    User,
)
from app.enums import AgeStatus, BroadcastStatus, EventType, Gender, OfferStatus, Segment
from app.utils.packs import PackSummary, group_packs, pack_key

_CODE_RE = re.compile(r"[^A-Za-z0-9_-]+")

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def slugify_code(raw: str) -> str:
    """Telegram разрешает в start-payload только [A-Za-z0-9_-], максимум 64 символа."""
    lowered = "".join(TRANSLIT.get(ch, ch) for ch in raw.strip().lower())
    slug = _CODE_RE.sub("_", lowered).strip("_")
    return slug[:48] or "link"


# --------------------------------------------------------------------------- events


async def log_event(
    session: AsyncSession,
    user_tg_id: int,
    event_type: EventType,
    ref_link_id: int | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    session.add(
        Event(user_tg_id=user_tg_id, type=event_type.value, ref_link_id=ref_link_id, data=data)
    )


# --------------------------------------------------------------------------- buyers


class BuyerRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, buyer_id: int) -> Buyer | None:
        return await self.session.get(Buyer, buyer_id)

    async def get_by_tg_id(self, tg_id: int) -> Buyer | None:
        return await self.session.scalar(select(Buyer).where(Buyer.tg_id == tg_id))

    async def list_all(self) -> Sequence[Buyer]:
        return (await self.session.scalars(select(Buyer).order_by(Buyer.id))).all()

    async def create(self, name: str, tg_id: int | None = None, note: str | None = None) -> Buyer:
        buyer = Buyer(name=name, tg_id=tg_id, note=note)
        self.session.add(buyer)
        await self.session.flush()
        return buyer


# --------------------------------------------------------------------------- ref links


class LinkRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, link_id: int) -> RefLink | None:
        return await self.session.get(RefLink, link_id)

    async def get_by_code(self, code: str) -> RefLink | None:
        return await self.session.scalar(select(RefLink).where(RefLink.code == code))

    async def list_all(
        self,
        buyer_id: int | None = None,
        include_archived: bool = False,
        only_archived: bool = False,
    ) -> Sequence[RefLink]:
        stmt = select(RefLink).order_by(RefLink.id)
        if buyer_id is not None:
            stmt = stmt.where(RefLink.buyer_id == buyer_id)
        if only_archived:
            stmt = stmt.where(RefLink.is_archived.is_(True))
        elif not include_archived:
            stmt = stmt.where(RefLink.is_archived.is_(False))
        return (await self.session.scalars(stmt)).all()

    async def create(
        self, title: str, buyer_id: int | None = None, code: str | None = None
    ) -> RefLink:
        base = slugify_code(code or title)
        candidate, suffix = base, 1
        while await self.get_by_code(candidate):
            suffix += 1
            candidate = f"{base[:44]}_{suffix}"
        link = RefLink(code=candidate, title=title.strip()[:128], buyer_id=buyer_id)
        self.session.add(link)
        await self.session.flush()
        return link

    async def next_pack_index(self, base: str) -> int:
        """С какого номера продолжать пак.

        Повторный заказ пака с тем же именем не начинает нумерацию заново:
        иначе коды столкнулись бы и получили бы суффиксы вида kiril_1_2.
        """
        pattern = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        codes = (
            await self.session.scalars(
                select(RefLink.code).where(RefLink.code.like(f"{pattern}\\_%", escape="\\"))
            )
        ).all()
        highest = 0
        for code in codes:
            suffix = code[len(base) + 1 :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return highest + 1

    async def create_pack(
        self, title: str, count: int, buyer_id: int | None = None
    ) -> list[tuple[int, RefLink]]:
        """Создаёт пачку ссылок `Название N` и возвращает пары (номер, ссылка)."""
        base = slugify_code(title)
        clean_title = title.strip()[:120]
        number = await self.next_pack_index(base)
        created: list[tuple[int, RefLink]] = []

        while len(created) < count:
            code = f"{base}_{number}"
            if await self.get_by_code(code):
                number += 1
                continue
            link = RefLink(code=code, title=f"{clean_title} {number}", buyer_id=buyer_id)
            self.session.add(link)
            created.append((number, link))
            number += 1

        await self.session.flush()
        return created

    async def toggle(self, link_id: int) -> RefLink | None:
        link = await self.get(link_id)
        if link:
            link.is_active = not link.is_active
            await self.session.flush()
        return link

    # --- паки ---

    async def counts_by_link(self) -> dict[int, tuple[int, int]]:
        """{id ссылки: (переходы, подписки)} — одним запросом по всем ссылкам."""
        stmt = (
            select(
                User.ref_link_id,
                func.count(User.tg_id),
                func.coalesce(func.sum(case((User.is_subscribed.is_(True), 1), else_=0)), 0),
            )
            .where(User.ref_link_id.is_not(None))
            .group_by(User.ref_link_id)
        )
        return {row[0]: (row[1], row[2]) for row in (await self.session.execute(stmt)).all()}

    async def list_packs(
        self, include_archived: bool = False, only_archived: bool = False
    ) -> list[PackSummary]:
        links = await self.list_all(
            include_archived=include_archived, only_archived=only_archived
        )
        return group_packs(list(links), await self.counts_by_link())

    async def pack_links(self, base: str) -> list[RefLink]:
        r"""Все ссылки пака, включая архивные.

        LIKE по префиксу не точен: `kiril\_%` зацепил бы и `kiril_tiktok_1`.
        Поэтому кандидатов берём широко, а отсеиваем по разбору кода.
        """
        pattern = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = select(RefLink).where(
            or_(RefLink.code == base, RefLink.code.like(f"{pattern}\\_%", escape="\\"))
        )
        candidates = (await self.session.scalars(stmt)).all()
        links = [link for link in candidates if pack_key(link.code)[0] == base]
        links.sort(key=lambda link: (pack_key(link.code)[1], link.id))
        return links

    async def unused_links(self) -> list[RefLink]:
        """Ссылки, по которым не было ни одного перехода — терять там нечего."""
        used = select(User.ref_link_id).where(User.ref_link_id.is_not(None))
        stmt = select(RefLink).where(RefLink.id.not_in(used)).order_by(RefLink.id)
        return list((await self.session.scalars(stmt)).all())

    # --- архив и удаление ---

    async def set_archived(self, links: Sequence[RefLink], archived: bool) -> int:
        stamp = utcnow() if archived else None
        for link in links:
            link.is_archived = archived
            link.archived_at = stamp
        await self.session.flush()
        return len(links)

    async def impact(self, links: Sequence[RefLink]) -> tuple[int, int, int]:
        """Что потеряется при удалении: (ссылок, переходов, подписок).

        Юзеры не удаляются — у них обнулится ссылка, и они уйдут в «органику».
        Теряется именно привязка к байеру, то есть доказательство его работы.
        """
        ids = [link.id for link in links]
        if not ids:
            return 0, 0, 0
        stmt = select(
            func.count(User.tg_id),
            func.coalesce(func.sum(case((User.is_subscribed.is_(True), 1), else_=0)), 0),
        ).where(User.ref_link_id.in_(ids))
        starts, converted = (await self.session.execute(stmt)).one()
        return len(ids), starts, converted

    async def delete_links(self, links: Sequence[RefLink]) -> int:
        """Безвозвратно удаляет ссылки. Записи юзеров остаются — у них лишь
        обнуляется привязка, и в отчётах они уходят в «органику».

        Привязки гасим явно, а не полагаемся на FK ON DELETE SET NULL:
        SQLite его без pragma не применяет, и поведение разъехалось бы
        между тестами и Postgres.
        """
        ids = [link.id for link in links]
        if not ids:
            return 0
        await self.session.execute(
            update(User).where(User.ref_link_id.in_(ids)).values(ref_link_id=None)
        )
        await self.session.execute(
            update(Offer).where(Offer.ref_link_id.in_(ids)).values(ref_link_id=None)
        )
        result = await self.session.execute(delete(RefLink).where(RefLink.id.in_(ids)))
        await self.session.flush()
        return result.rowcount or 0


# --------------------------------------------------------------------------- channels


class ChannelRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, channel_id: int) -> Channel | None:
        return await self.session.get(Channel, channel_id)

    async def get_by_chat_id(self, chat_id: int) -> Channel | None:
        return await self.session.scalar(select(Channel).where(Channel.chat_id == chat_id))

    async def list_all(self, only_active: bool = False) -> Sequence[Channel]:
        stmt = select(Channel).order_by(Channel.priority, Channel.id)
        if only_active:
            stmt = stmt.where(Channel.is_active.is_(True))
        return (await self.session.scalars(stmt)).all()

    async def upsert(
        self,
        chat_id: int,
        title: str,
        username: str | None = None,
        segments: list[str] | None = None,
        allow_minors: bool | None = None,
        **fields: Any,
    ) -> Channel:
        channel = await self.get_by_chat_id(chat_id)
        if channel is None:
            channel = Channel(chat_id=chat_id, title=title, segments=segments or [])
            self.session.add(channel)
        else:
            channel.title = title
            if segments is not None:
                channel.segments = segments
        if username is not None:
            channel.username = username
        if allow_minors is not None:
            channel.allow_minors = allow_minors
        for key, value in fields.items():
            if value is not None:
                setattr(channel, key, value)
        await self.session.flush()
        return channel

    async def toggle_segment(self, channel: Channel, segment: Segment) -> Channel:
        current = set(channel.segments or [])
        current.symmetric_difference_update({segment.value})
        # Предохранитель: нельзя привязать «взрослый» канал к детскому сегменту.
        if not channel.allow_minors:
            current -= {Segment.MALE_MINOR.value, Segment.FEMALE_MINOR.value}
        channel.segments = sorted(current)
        await self.session.flush()
        return channel

    async def set_invite_link(self, channel: Channel, link: str) -> None:
        channel.invite_link = link
        await self.session.flush()

    async def set_title(self, channel: Channel, title: str) -> None:
        channel.title = title
        await self.session.flush()


# --------------------------------------------------------------------------- users


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tg_id: int) -> User | None:
        return await self.session.get(User, tg_id)

    async def register_start(
        self,
        tg_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
        ref_link: RefLink | None,
        ref_payload: str | None,
    ) -> tuple[User, bool]:
        """Создаёт юзера при первом /start. Возвращает (user, is_new)."""
        user = await self.get(tg_id)
        now = utcnow()
        if user is None:
            user = User(
                tg_id=tg_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
                ref_link_id=ref_link.id if ref_link else None,
                ref_payload=ref_payload,
                started_at=now,
                last_seen_at=now,
            )
            self.session.add(user)
            await self.session.flush()
            return user, True

        # Повторный /start: профиль обновляем, источник не перезаписываем —
        # иначе статистика первого байера «уедет» ко второму.
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.last_seen_at = now
        user.is_blocked = False
        if user.ref_link_id is None and ref_link is not None:
            user.ref_link_id = ref_link.id
            user.ref_payload = ref_payload
        user.needs_sheet_sync = True
        await self.session.flush()
        return user, False

    async def set_age(self, user: User, age: AgeStatus) -> User:
        user.age_status = age.value
        user.last_seen_at = utcnow()
        user.needs_sheet_sync = True
        await self.session.flush()
        return user

    async def set_gender_and_segment(self, user: User, gender: Gender, segment: Segment) -> User:
        user.gender = gender.value
        user.segment = segment.value
        user.last_seen_at = utcnow()
        if user.filter_passed_at is None:
            user.filter_passed_at = utcnow()
        user.needs_sheet_sync = True
        await self.session.flush()
        return user

    async def set_assigned_channel(self, user: User, channel: Channel | None) -> None:
        user.assigned_channel_id = channel.id if channel else None
        user.needs_sheet_sync = True
        await self.session.flush()

    async def mark_subscribed(self, user: User, channel: Channel) -> None:
        if not user.is_subscribed:
            user.is_subscribed = True
            user.subscribed_at = utcnow()
        user.subscribed_channel_id = channel.id
        user.needs_sheet_sync = True
        await self.session.flush()

    async def mark_unsubscribed(self, tg_id: int) -> None:
        await self.session.execute(
            update(User)
            .where(User.tg_id == tg_id)
            .values(is_subscribed=False, needs_sheet_sync=True)
        )

    async def mark_blocked(self, tg_id: int, blocked: bool = True) -> None:
        await self.session.execute(
            update(User).where(User.tg_id == tg_id).values(is_blocked=blocked)
        )

    # --- выборка получателей рассылки ---

    @staticmethod
    def targets_stmt(filters: dict[str, Any]) -> Select:
        stmt = select(User.tg_id).where(User.is_blocked.is_(False))
        gender = filters.get("gender")
        if gender in (Gender.MALE.value, Gender.FEMALE.value):
            stmt = stmt.where(User.gender == gender)
        age = filters.get("age")
        if age in (AgeStatus.ADULT.value, AgeStatus.MINOR.value):
            stmt = stmt.where(User.age_status == age)
        status = filters.get("status")
        if status == "filter_passed":
            stmt = stmt.where(User.filter_passed_at.is_not(None))
        elif status == "not_subscribed":
            stmt = stmt.where(User.filter_passed_at.is_not(None), User.is_subscribed.is_(False))
        elif status == "subscribed":
            stmt = stmt.where(User.is_subscribed.is_(True))
        elif status == "no_filter":
            stmt = stmt.where(User.filter_passed_at.is_(None))
        if ref_link_id := filters.get("ref_link_id"):
            stmt = stmt.where(User.ref_link_id == int(ref_link_id))
        if channel_id := filters.get("channel_id"):
            stmt = stmt.where(User.subscribed_channel_id == int(channel_id))
        return stmt

    async def count_targets(self, filters: dict[str, Any]) -> int:
        stmt = self.targets_stmt(filters).subquery()
        return await self.session.scalar(select(func.count()).select_from(stmt)) or 0

    async def iter_targets(
        self, filters: dict[str, Any], after_tg_id: int = 0, batch: int = 500
    ) -> AsyncIterator[list[int]]:
        """Keyset-пагинация по tg_id: выдерживает рестарт и большие базы."""
        cursor = after_tg_id
        while True:
            stmt = (
                self.targets_stmt(filters)
                .where(User.tg_id > cursor)
                .order_by(User.tg_id)
                .limit(batch)
            )
            chunk = list((await self.session.scalars(stmt)).all())
            if not chunk:
                return
            yield chunk
            cursor = chunk[-1]

    # --- Google Sheets ---

    async def pending_sheet_sync(self, limit: int) -> Sequence[User]:
        stmt = (
            select(User)
            .where(User.needs_sheet_sync.is_(True))
            .order_by(User.started_at)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()

    async def mark_synced(self, tg_id: int, tab: str, row: int) -> None:
        await self.session.execute(
            update(User)
            .where(User.tg_id == tg_id)
            .values(needs_sheet_sync=False, sheet_tab=tab, sheet_row=row)
        )

    async def mark_all_dirty(self) -> int:
        result = await self.session.execute(
            update(User).values(needs_sheet_sync=True, sheet_tab=None, sheet_row=None)
        )
        return result.rowcount or 0

    async def list_for_export(self, segment: Segment | None = None) -> Sequence[User]:
        stmt = select(User).order_by(User.started_at)
        if segment is not None:
            stmt = stmt.where(User.segment == segment.value)
        return (await self.session.scalars(stmt)).all()


# --------------------------------------------------------------------------- offers


class OfferRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(
        self, user: User, channel: Channel, invite_link: str | None
    ) -> Offer:
        offer = await self.session.scalar(
            select(Offer).where(Offer.user_tg_id == user.tg_id, Offer.channel_id == channel.id)
        )
        if offer is None:
            offer = Offer(
                user_tg_id=user.tg_id,
                channel_id=channel.id,
                ref_link_id=user.ref_link_id,
                invite_link=invite_link,
            )
            self.session.add(offer)
        else:
            offer.invite_link = invite_link or offer.invite_link
        await self.session.flush()
        return offer

    async def mark_joined(self, user_tg_id: int, channel: Channel) -> Offer:
        offer = await self.session.scalar(
            select(Offer).where(Offer.user_tg_id == user_tg_id, Offer.channel_id == channel.id)
        )
        if offer is None:
            offer = Offer(user_tg_id=user_tg_id, channel_id=channel.id)
            self.session.add(offer)
        offer.status = OfferStatus.JOINED.value
        offer.joined_at = utcnow()
        await self.session.flush()
        return offer

    async def mark_left(self, user_tg_id: int, channel_id: int) -> None:
        await self.session.execute(
            update(Offer)
            .where(Offer.user_tg_id == user_tg_id, Offer.channel_id == channel_id)
            .values(status=OfferStatus.LEFT.value)
        )


# --------------------------------------------------------------------------- broadcasts


class BroadcastRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, broadcast_id: int) -> Broadcast | None:
        return await self.session.get(Broadcast, broadcast_id)

    async def create(
        self,
        admin_id: int,
        filters: dict[str, Any],
        from_chat_id: int,
        message_id: int,
        buttons: dict[str, Any] | None,
        total: int,
    ) -> Broadcast:
        broadcast = Broadcast(
            admin_id=admin_id,
            filters=filters,
            from_chat_id=from_chat_id,
            message_id=message_id,
            buttons=buttons,
            total=total,
            status=BroadcastStatus.QUEUED.value,
        )
        self.session.add(broadcast)
        await self.session.flush()
        return broadcast

    async def list_recent(self, limit: int = 10) -> Sequence[Broadcast]:
        stmt = select(Broadcast).order_by(Broadcast.id.desc()).limit(limit)
        return (await self.session.scalars(stmt)).all()

    async def unfinished(self) -> Sequence[Broadcast]:
        stmt = select(Broadcast).where(
            Broadcast.status.in_([BroadcastStatus.QUEUED.value, BroadcastStatus.RUNNING.value])
        )
        return (await self.session.scalars(stmt)).all()

    async def set_status(self, broadcast_id: int, status: BroadcastStatus, **fields: Any) -> None:
        values: dict[str, Any] = {"status": status.value, **fields}
        if status is BroadcastStatus.RUNNING and "started_at" not in values:
            values["started_at"] = utcnow()
        if status in (BroadcastStatus.DONE, BroadcastStatus.FAILED, BroadcastStatus.CANCELLED):
            values["finished_at"] = utcnow()
        await self.session.execute(
            update(Broadcast).where(Broadcast.id == broadcast_id).values(**values)
        )

    async def bump_counters(
        self, broadcast_id: int, cursor: int, sent: int = 0, failed: int = 0, blocked: int = 0
    ) -> None:
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                sent=Broadcast.sent + sent,
                failed=Broadcast.failed + failed,
                blocked=Broadcast.blocked + blocked,
                cursor_tg_id=cursor,
            )
        )


# --------------------------------------------------------------------------- stats


@dataclass(slots=True)
class FunnelRow:
    link_id: int | None
    code: str
    title: str
    buyer_name: str | None
    starts: int
    passed: int
    converted: int
    is_active: bool = True

    @property
    def pass_rate(self) -> float:
        return self.passed / self.starts * 100 if self.starts else 0.0

    @property
    def conv_rate(self) -> float:
        return self.converted / self.starts * 100 if self.starts else 0.0

    @property
    def conv_of_passed(self) -> float:
        return self.converted / self.passed * 100 if self.passed else 0.0


class StatsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _funnel_columns() -> tuple[Any, Any, Any]:
        return (
            func.count(User.tg_id).label("starts"),
            func.count(User.filter_passed_at).label("passed"),
            func.coalesce(
                func.sum(case((User.is_subscribed.is_(True), 1), else_=0)), 0
            ).label("converted"),
        )

    async def link_funnel(
        self,
        since: dt.datetime | None = None,
        buyer_id: int | None = None,
        segment: Segment | None = None,
        include_archived: bool = False,
    ) -> list[FunnelRow]:
        """Воронка по ссылкам. `segment` сужает подсчёт до одной аудитории.

        Условие уходит в ON, а не в WHERE: ссылки без подходящих юзеров
        должны оставаться в списке с нулями, иначе пак выглядит обрезанным.

        Архивные ссылки по умолчанию не показываются — пак закрыт после
        расчёта, в текущем отчёте ему не место. Их трафик идёт отдельной
        строкой через archived_funnel, чтобы итоги сходились.
        """
        join_cond = [User.ref_link_id == RefLink.id]
        if since is not None:
            join_cond.append(User.started_at >= since)
        if segment is not None:
            join_cond.append(User.segment == segment.value)
        starts, passed, converted = self._funnel_columns()
        stmt = (
            select(
                RefLink.id,
                RefLink.code,
                RefLink.title,
                RefLink.is_active,
                Buyer.name,
                starts,
                passed,
                converted,
            )
            .select_from(RefLink)
            .outerjoin(User, and_(*join_cond))
            .outerjoin(Buyer, Buyer.id == RefLink.buyer_id)
            .group_by(RefLink.id, RefLink.code, RefLink.title, RefLink.is_active, Buyer.name)
            .order_by(func.count(User.tg_id).desc(), RefLink.id)
        )
        if buyer_id is not None:
            stmt = stmt.where(RefLink.buyer_id == buyer_id)
        if not include_archived:
            stmt = stmt.where(RefLink.is_archived.is_(False))
        rows = (await self.session.execute(stmt)).all()
        return [
            FunnelRow(
                link_id=row[0],
                code=row[1],
                title=row[2],
                is_active=row[3],
                buyer_name=row[4],
                starts=row[5],
                passed=row[6],
                converted=row[7],
            )
            for row in rows
        ]

    async def archived_funnel(self, since: dt.datetime | None = None) -> FunnelRow:
        """Трафик закрытых (архивных) ссылок — одной строкой, чтобы сумма сходилась."""
        starts, passed, converted = self._funnel_columns()
        stmt = (
            select(starts, passed, converted)
            .select_from(User)
            .join(RefLink, RefLink.id == User.ref_link_id)
            .where(RefLink.is_archived.is_(True))
        )
        if since is not None:
            stmt = stmt.where(User.started_at >= since)
        row = (await self.session.execute(stmt)).one()
        return FunnelRow(
            link_id=None,
            code="archived",
            title="В архиве (закрытые паки)",
            buyer_name=None,
            starts=row[0],
            passed=row[1],
            converted=row[2],
        )

    async def organic_funnel(self, since: dt.datetime | None = None) -> FunnelRow:
        """Трафик без реф-ссылки: по имени бота, из поиска, по пересылке.

        Без этой строки сумма по ссылкам не сходится с «Нажали Start», и
        заказчик решит, что статистика теряет людей.
        """
        starts, passed, converted = self._funnel_columns()
        stmt = select(starts, passed, converted).where(User.ref_link_id.is_(None))
        if since is not None:
            stmt = stmt.where(User.started_at >= since)
        row = (await self.session.execute(stmt)).one()
        return FunnelRow(
            link_id=None,
            code="organic",
            title="Без ссылки (органика)",
            buyer_name=None,
            starts=row[0],
            passed=row[1],
            converted=row[2],
        )

    async def no_buyer_funnel(self, since: dt.datetime | None = None) -> FunnelRow:
        """Трафик, не привязанный ни к одному байеру: без ссылки или по ссылке без владельца."""
        starts, passed, converted = self._funnel_columns()
        stmt = (
            select(starts, passed, converted)
            .select_from(User)
            .outerjoin(RefLink, RefLink.id == User.ref_link_id)
            .where(
                or_(
                    User.ref_link_id.is_(None),
                    and_(RefLink.buyer_id.is_(None), RefLink.is_archived.is_(False)),
                )
            )
        )
        if since is not None:
            stmt = stmt.where(User.started_at >= since)
        row = (await self.session.execute(stmt)).one()
        return FunnelRow(
            link_id=None,
            code="no_buyer",
            title="Без байера",
            buyer_name=None,
            starts=row[0],
            passed=row[1],
            converted=row[2],
        )

    async def buyer_funnel(self, since: dt.datetime | None = None) -> list[FunnelRow]:
        join_cond = [User.ref_link_id == RefLink.id]
        if since is not None:
            join_cond.append(User.started_at >= since)
        starts, passed, converted = self._funnel_columns()
        stmt = (
            select(Buyer.id, Buyer.name, starts, passed, converted)
            .select_from(Buyer)
            .outerjoin(
                RefLink, and_(RefLink.buyer_id == Buyer.id, RefLink.is_archived.is_(False))
            )
            .outerjoin(User, and_(*join_cond))
            .group_by(Buyer.id, Buyer.name)
            .order_by(func.count(User.tg_id).desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            FunnelRow(
                link_id=row[0],
                code=f"buyer_{row[0]}",
                title=row[1],
                buyer_name=row[1],
                starts=row[2],
                passed=row[3],
                converted=row[4],
            )
            for row in rows
        ]

    async def totals(self, since: dt.datetime | None = None) -> FunnelRow:
        starts, passed, converted = self._funnel_columns()
        stmt = select(starts, passed, converted)
        if since is not None:
            stmt = stmt.where(User.started_at >= since)
        row = (await self.session.execute(stmt)).one()
        return FunnelRow(
            link_id=None,
            code="all",
            title="Всего",
            buyer_name=None,
            starts=row[0],
            passed=row[1],
            converted=row[2],
        )

    async def segment_breakdown(
        self, since: dt.datetime | None = None
    ) -> dict[str, dict[str, int]]:
        starts, passed, converted = self._funnel_columns()
        stmt = select(User.segment, starts, passed, converted).group_by(User.segment)
        if since is not None:
            stmt = stmt.where(User.started_at >= since)
        result: dict[str, dict[str, int]] = {}
        for segment, s, p, c in (await self.session.execute(stmt)).all():
            result[segment or "no_filter"] = {"starts": s, "passed": p, "converted": c}
        return result

    async def channel_breakdown(
        self, since: dt.datetime | None = None
    ) -> list[tuple[str, int, int]]:
        """(название канала, выдано офферов, вступило)."""
        conds = [Offer.channel_id == Channel.id]
        if since is not None:
            conds.append(Offer.created_at >= since)
        stmt = (
            select(
                Channel.title,
                func.count(Offer.id),
                func.coalesce(
                    func.sum(case((Offer.status == OfferStatus.JOINED.value, 1), else_=0)), 0
                ),
            )
            .select_from(Channel)
            .outerjoin(Offer, and_(*conds))
            .group_by(Channel.id, Channel.title)
            .order_by(Channel.priority, Channel.id)
        )
        return [(row[0], row[1], row[2]) for row in (await self.session.execute(stmt)).all()]


# --------------------------------------------------------------------------- sheet state


class SheetStateRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reserve_rows(self, tab: str, count: int, header_rows: int = 1) -> int:
        """Атомарно бронирует `count` строк на листе, возвращает номер первой."""
        state = await self.session.get(SheetState, tab, with_for_update=True)
        if state is None:
            state = SheetState(tab=tab, last_row=header_rows)
            self.session.add(state)
            await self.session.flush()
        first = state.last_row + 1
        state.last_row += count
        await self.session.flush()
        return first

    async def reset(self) -> None:
        for state in (await self.session.scalars(select(SheetState))).all():
            await self.session.delete(state)

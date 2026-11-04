from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, TimestampTZ
from app.enums import BroadcastStatus, OfferStatus


class Buyer(Base, TimestampMixin):
    """Байер / заливщик. tg_id нужен, чтобы он сам смотрел свою статистику в боте."""

    __tablename__ = "buyers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    links: Mapped[list[RefLink]] = relationship(back_populates="buyer", lazy="selectin")

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"<Buyer {self.id} {self.name}>"


class RefLink(Base, TimestampMixin):
    """Реферальная ссылка. code попадает в deep link: t.me/<bot>?start=<code>."""

    __tablename__ = "ref_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    buyer_id: Mapped[int | None] = mapped_column(
        ForeignKey("buyers.id", ondelete="SET NULL"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    buyer: Mapped[Buyer | None] = relationship(back_populates="links", lazy="selectin")

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"<RefLink {self.code}>"


class Channel(Base, TimestampMixin):
    """Целевой канал (оффер). segments — список сегментов, которым он выдаётся."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    segments: Mapped[list[str]] = mapped_column(default=list, nullable=False)
    # Жёсткий предохранитель: канал не попадёт к <18, даже если его по ошибке
    # привязали к детскому сегменту (требование «отсеивать аудиторию ниже 18»).
    allow_minors: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Кэш общей join-request ссылки (когда PER_USER_INVITE_LINKS=false).
    invite_link: Mapped[str | None] = mapped_column(String(255))
    offer_text: Mapped[str | None] = mapped_column(Text)
    button_text: Mapped[str | None] = mapped_column(String(64))

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"<Channel {self.chat_id} {self.title}>"


class User(Base):
    """Юзер бота + его позиция в воронке. tg_id = PK (п.4 ТЗ)."""

    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(8))

    ref_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref_links.id", ondelete="SET NULL"), index=True
    )
    ref_payload: Mapped[str | None] = mapped_column(String(64))

    age_status: Mapped[str | None] = mapped_column(String(8))
    gender: Mapped[str | None] = mapped_column(String(8))
    segment: Mapped[str | None] = mapped_column(String(16), index=True)

    assigned_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL")
    )
    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    subscribed_channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL")
    )
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    started_at: Mapped[dt.datetime] = mapped_column(TimestampTZ, nullable=False, index=True)
    filter_passed_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    subscribed_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)

    # Состояние синхронизации с Google Sheets.
    needs_sheet_sync: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )
    sheet_tab: Mapped[str | None] = mapped_column(String(64))
    sheet_row: Mapped[int | None] = mapped_column(Integer)

    ref_link: Mapped[RefLink | None] = relationship(lazy="selectin")
    assigned_channel: Mapped[Channel | None] = relationship(
        lazy="selectin", foreign_keys=[assigned_channel_id]
    )
    subscribed_channel: Mapped[Channel | None] = relationship(
        lazy="selectin", foreign_keys=[subscribed_channel_id]
    )

    __table_args__ = (
        Index("ix_users_segment_subscribed", "segment", "is_subscribed"),
        Index("ix_users_ref_started", "ref_link_id", "started_at"),
    )

    @property
    def filter_passed(self) -> bool:
        return self.filter_passed_at is not None

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        return " ".join(filter(None, (self.first_name, self.last_name))) or str(self.tg_id)

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"<User {self.tg_id} {self.segment}>"


class Offer(Base, TimestampMixin):
    """Факт выдачи инвайта в канал + результат (вступил / нет)."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[int] = mapped_column(
        ForeignKey("users.tg_id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ref_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref_links.id", ondelete="SET NULL"), index=True
    )
    invite_link: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(16), default=OfferStatus.ISSUED.value, nullable=False
    )
    joined_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)

    __table_args__ = (UniqueConstraint("user_tg_id", "channel_id", name="uq_offer_user_channel"),)


class Event(Base, TimestampMixin):
    """Сырой лог воронки — на случай пересчёта/разбора спорной статистики."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_tg_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    ref_link_id: Mapped[int | None] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(default=None)

    __table_args__ = (Index("ix_events_type_created", "type", "created_at"),)


class Broadcast(Base, TimestampMixin):
    """Push-рассылка: фильтры сегмента + исходное сообщение для copy_message."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    from_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    buttons: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(
        String(16), default=BroadcastStatus.DRAFT.value, nullable=False, index=True
    )
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Keyset-курсор, чтобы продолжить рассылку после рестарта контейнера.
    cursor_tg_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TimestampTZ)

    @property
    def processed(self) -> int:
        return self.sent + self.failed + self.blocked


class SheetState(Base):
    """Служебная таблица: сколько строк уже занято на каждом листе Google Sheets."""

    __tablename__ = "sheet_state"

    tab: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_row: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class AgeCB(CallbackData, prefix="age"):
    adult: bool


class GenderCB(CallbackData, prefix="gender"):
    male: bool


class MenuCB(CallbackData, prefix="menu"):
    section: str  # root | stats | links | buyers | channels | broadcast | export


class StatsCB(CallbackData, prefix="stats"):
    view: str  # links | buyers | segments | channels
    period: str  # today | 7d | 30d | all


class LinkCB(CallbackData, prefix="link"):
    action: str  # open | toggle | new | pack | list | arch | askdel | del
    link_id: int = 0
    page: int = 0


class PackCB(CallbackData, prefix="pk"):
    # list | open | arch | unarch | askdel | del | resend | clean | askclean
    # | delall | askdelall | archview
    action: str
    pid: str | None = None   # короткий id пака (pack_id), пусто для глобальных действий
    page: int = 0
    arch: bool = False       # показывать архив в списке паков


class BuyerCB(CallbackData, prefix="buyer"):
    action: str  # list | new | open | pick
    buyer_id: int = 0


class ChannelCB(CallbackData, prefix="chan"):
    action: str  # list | open | toggle | seg | minors | refresh | delete
    channel_id: int = 0
    # Строго `str | None`: aiogram распаковывает пустой сегмент callback_data
    # как None, и объявление `str = ""` роняло бы валидацию — фильтр тогда
    # молча не матчится, а кнопка висит в бесконечной загрузке.
    value: str | None = None


class BroadcastCB(CallbackData, prefix="bc"):
    action: str  # new | set | msg | buttons | nobuttons | preview | run | cancel | status | list
    field: str | None = None
    value: str | None = None
    broadcast_id: int = 0


class ExportCB(CallbackData, prefix="exp"):
    action: str  # xlsx | sheets_sync | sheets_full

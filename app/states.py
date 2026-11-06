from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class NewLink(StatesGroup):
    title = State()
    buyer = State()


class NewLinkPack(StatesGroup):
    title = State()
    count = State()
    buyer = State()


class NewBuyer(StatesGroup):
    name = State()


class NewChannel(StatesGroup):
    source = State()


class BroadcastFlow(StatesGroup):
    editing = State()
    message = State()
    buttons = State()

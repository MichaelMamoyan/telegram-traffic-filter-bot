from aiogram import Dispatcher, Router

from app.handlers import (
    admin_broadcast,
    admin_channels,
    admin_export,
    admin_links,
    admin_menu,
    buyer,
    errors,
    funnel,
    join,
)

# Порядок важен: админка и системные апдейты разбираются раньше воронки.
ROUTERS: tuple[Router, ...] = (
    admin_menu.router,
    admin_links.router,
    admin_channels.router,
    admin_broadcast.router,
    admin_export.router,
    join.router,
    buyer.router,
    funnel.router,
    errors.router,
)


def register_routers(dp: Dispatcher) -> None:
    dp.include_routers(*ROUTERS)


__all__ = ["ROUTERS", "register_routers"]

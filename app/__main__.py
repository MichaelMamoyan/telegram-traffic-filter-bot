from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from app.config import Settings, get_settings
from app.db.base import make_engine, make_sessionmaker
from app.db.init import create_schema, seed_channels
from app.handlers import register_routers
from app.middlewares import DbSessionMiddleware
from app.services.broadcast import BroadcastService
from app.services.commands import setup_commands
from app.services.sheets import SheetsSyncer

log = logging.getLogger("bot")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def make_storage(settings: Settings):  # type: ignore[no-untyped-def]
    """Redis для FSM; если он недоступен — не валим бота, работаем в памяти."""
    try:
        storage = RedisStorage.from_url(settings.redis_dsn)
        await storage.redis.ping()
        log.info("FSM storage: Redis")
        return storage
    except Exception as exc:
        log.warning("Redis недоступен (%s) — FSM в памяти процесса", exc)
        return MemoryStorage()


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    engine = make_engine(settings.db_dsn)
    sessionmaker = make_sessionmaker(engine)
    await create_schema(engine)
    if settings.seed_channels_file:
        await seed_channels(sessionmaker, settings.seed_channels_file)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    storage = await make_storage(settings)
    dp = Dispatcher(storage=storage)

    broadcasts = BroadcastService(
        bot, sessionmaker, rate=settings.broadcast_rate, batch=settings.broadcast_batch
    )
    sheets = SheetsSyncer(settings, sessionmaker)

    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.workflow_data.update(
        settings=settings, broadcasts=broadcasts, sheets=sheets, sessionmaker=sessionmaker
    )
    register_routers(dp)

    me = await bot.me()
    log.info("Запускаю @%s (admins: %s)", me.username, settings.admin_ids or "не заданы")
    if not settings.admin_ids:
        log.warning("ADMIN_IDS пуст — админка будет недоступна")

    await setup_commands(bot, settings, sessionmaker)
    sheets.start()
    resumed = await broadcasts.resume_unfinished()
    if resumed:
        log.info("Возобновлено рассылок: %s", resumed)

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(
            bot, allowed_updates=dp.resolve_used_update_types(), handle_as_tasks=True
        )
    finally:
        log.info("Останавливаюсь…")
        await broadcasts.shutdown()
        await sheets.stop()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено пользователем")

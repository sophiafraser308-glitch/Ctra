"""Entry point:  python -m app

Startup order follows docs/architecture.md. The Twisted asyncio reactor MUST be installed first.
"""
from __future__ import annotations

import asyncio
import signal
import sys


async def run() -> int:
    from app.ctrader.reactor import install_asyncio_reactor
    install_asyncio_reactor(asyncio.get_running_loop())          # 1. before any Twisted/SDK import

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties

    from app import __version__
    from app.config import load_settings
    from app.core.container import AppContext
    from app.core.enums import Category
    from app.core.exceptions import ConfigError
    from app.database import Database
    from app.logging import DbLogWriter, get_logger, setup_logging
    from app.security.masking import masker
    from app.web.server import WebServer

    settings = load_settings()
    handler = setup_logging(settings.log_level, settings.db_log_level)             # 2. logging (masked)
    log = get_logger(Category.SYSTEM)
    masker.register(*settings.secret_values())
    try:
        settings.validate_runtime()                                                  # 3. config validation (fail fast)
    except ConfigError as exc:
        log.critical("%s", exc)
        return 2

    db = Database(settings)                                                          # 4. database
    if settings.is_sqlite:
        await db.create_all_if_empty()
    else:
        try:
            async with db.session() as s:
                from sqlalchemy import text
                await s.execute(text("SELECT 1 FROM system_settings LIMIT 1"))
        except Exception:
            log.critical("Database schema missing. Run: alembic upgrade head   (scripts/run_migrations.sh)")
            return 3
    log_writer = DbLogWriter(db, handler)
    await log_writer.start()

    ctx = AppContext(settings, db)                                                   # 5. services
    await ctx.sys_settings.load()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    bot = Bot(settings.telegram_bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode="HTML"))
    ctx.bot_api = bot
    try:
        from aiogram.types import BotCommand
        await bot.set_my_commands([BotCommand(command="start", description="Main menu"), BotCommand(command="backtest", description="🧪 Backtest a strategy on past days"),
                                   BotCommand(command="cancel", description="Cancel the current action")])
    except Exception:
        pass
    ctx.notifier.bot = bot
    from app.bot.router import build_dispatcher
    dp = build_dispatcher(ctx)
    web = WebServer(ctx)

    ctx.heartbeat.update("application", "STARTING", operation="startup")
    try:
        await ctx.heartbeat.start()                                                  # 6. heartbeat + notifier
        await ctx.notifier.start()
        restored = await ctx.strategies.restore_active()                             # 7. strategy cache restore
        log.info("restored %d active strategies", restored)
        await ctx.market.start()
        await web.start()                                                            # 8. OAuth callback / health
        await ctx.accounts.connect_enabled_accounts()                                # 9. cTrader connections
        await ctx.recon.reconcile_all("startup")                                     # 10. reconcile before trading
        await ctx.recon.start()
        await ctx.bots.recover_on_startup()                                          # 11. bot restart recovery
        await ctx.watchdog.start()                                                   # 12. watchdog
        ctx.heartbeat.update("application", "OK", operation="running")
        await ctx.notifier.notify("SYSTEM_START", f"Platform v{__version__} started ({settings.app_env}).", dedup_key="start", throttle_seconds=0)
        log.info("startup complete; polling Telegram")
        polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False, allowed_updates=["message", "callback_query"]), name="telegram-polling")
        waiter = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({polling, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if polling in done and polling.exception():
            log.critical("telegram polling terminated: %s", type(polling.exception()).__name__)
    finally:
        log.info("graceful shutdown…")                                               # shutdown sequence (spec 53)
        try:
            await dp.stop_polling()
        except Exception:
            pass
        await ctx.watchdog.stop()
        await ctx.recon.stop()
        await ctx.bots.shutdown()            # keeps desired_state so bots auto-recover on next start
        try:
            await ctx.notifier.notify("SYSTEM_STOP", "Platform shutting down.", dedup_key="stop", throttle_seconds=0)
            await asyncio.sleep(1.5)
        except Exception:
            pass
        await ctx.notifier.stop()
        await ctx.market.stop()
        await ctx.gateway.shutdown()
        await web.stop()
        await ctx.heartbeat.stop()
        await bot.session.close()
        await log_writer.stop()
        await db.close()
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

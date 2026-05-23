"""Точка входа бота."""

from __future__ import annotations

import asyncio
import logging
import sys

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .config import Settings, load_settings
from .handlers import (
    analyze_message,
    budget_cmd,
    flip_cmd,
    help_cmd,
    list_cmd,
    on_callback,
    range_cmd,
    remove_cmd,
    scan_now_cmd,
    scan_off_cmd,
    scan_on_cmd,
    start,
)
from .models.db import Database
from .services.scanner import AutoScanner
from .services.scheduler import PriceRefresher

log = logging.getLogger("nft_flip_bot")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Меньше шума от httpx по запросам Telegram getUpdates
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)


def build_application(settings: Settings, db: Database) -> Application:
    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.bot_data["settings"] = settings
    app.bot_data["db"] = db
    app.bot_data["refresher"] = PriceRefresher(db, settings)
    app.bot_data["scanner"] = AutoScanner(app.bot, db, settings)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("budget", budget_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("flip", flip_cmd))
    app.add_handler(CommandHandler("scan_on", scan_on_cmd))
    app.add_handler(CommandHandler("scan_off", scan_off_cmd))
    app.add_handler(CommandHandler("scan_now", scan_now_cmd))
    app.add_handler(CommandHandler("range", range_cmd))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^flip:"))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_message)
    )

    return app


async def _post_init(application: Application) -> None:
    db: Database = application.bot_data["db"]
    await db.init()
    refresher: PriceRefresher = application.bot_data["refresher"]
    refresher.start()
    scanner: AutoScanner = application.bot_data["scanner"]
    scanner.start()
    log.info("Bot post_init complete")


async def _post_shutdown(application: Application) -> None:
    refresher: PriceRefresher = application.bot_data["refresher"]
    refresher.stop()
    scanner: AutoScanner = application.bot_data["scanner"]
    scanner.stop()


def main() -> None:
    _configure_logging()
    settings = load_settings()

    if not settings.bot_token:
        log.error("BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN.")
        sys.exit(1)

    db = Database(settings.db_path)
    app = build_application(settings, db)

    log.info("Starting nft_flip_bot polling…")
    # run_polling сам управляет event loop'ом.
    app.run_polling(allowed_updates=None, close_loop=True)


if __name__ == "__main__":
    # asyncio.run не нужен — run_polling сам стартует loop, но если кому-то
    # понадобится явный async-запуск через webhook, оставим заглушку.
    try:
        main()
    except KeyboardInterrupt:
        asyncio.get_event_loop().close()

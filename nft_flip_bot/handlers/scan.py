"""Команды управления авто-сканом: /scan_on, /scan_off, /scan_now."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Settings
from ..models.db import Database
from ..services.scanner import AutoScanner

log = logging.getLogger(__name__)


async def scan_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Включить подписку на авто-уведомления о выгодных NFT."""

    if update.message is None or update.effective_user is None:
        return

    db: Database = context.application.bot_data["db"]
    settings: Settings = context.application.bot_data["settings"]

    await db.upsert_user(update.effective_user.id, update.effective_user.username)
    await db.set_subscription(update.effective_user.id, True)

    await update.message.reply_text(
        "🔔 Авто-скан включён. Раз в "
        f"{settings.scan_interval_min} мин буду слать тебе до "
        f"{settings.scan_max_notify} новых выгодных лотов за прогон.\n\n"
        "Отключить: /scan_off\n"
        "Прогон вручную сейчас: /scan_now"
    )


async def scan_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отключить подписку."""

    if update.message is None or update.effective_user is None:
        return

    db: Database = context.application.bot_data["db"]
    await db.set_subscription(update.effective_user.id, False)
    await update.message.reply_text(
        "🔕 Авто-скан выключен. Включить снова: /scan_on"
    )


async def scan_now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Однократный прогон скана по запросу пользователя."""

    if update.message is None or update.effective_user is None:
        return

    scanner: AutoScanner = context.application.bot_data["scanner"]

    await update.message.reply_text("🔎 Запускаю скан, подожди немного…")
    try:
        sent = await scanner.run_for_user(update.effective_user.id)
    except Exception as exc:  # noqa: BLE001
        log.exception("scan_now failed for %s", update.effective_user.id)
        await update.message.reply_text(f"⚠️ Скан не удался: {exc}")
        return

    if sent == 0:
        await update.message.reply_text(
            "Сейчас новых выгодных лотов не нашлось. "
            "Скорее всего фид Tonel ещё не подключён к боту "
            "(см. README → раздел про реальный источник данных) "
            "или все подходящие лоты ты уже видел — попробуй позже."
        )
    else:
        await update.message.reply_text(
            f"Готово, отправил {sent} новых выгодных лот(ов) сообщениями выше."
        )

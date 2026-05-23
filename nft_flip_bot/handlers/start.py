"""/start и /help."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..models.db import Database
from .utils import user_label

log = logging.getLogger(__name__)


WELCOME = (
    "👋 Привет! Я помогаю флипперить NFT-подарки через Tonel.\n\n"
    "Команды:\n"
    "• Отправь ссылку https://tonel.io/item/<id> или ID токена — я проанализирую.\n"
    "• /budget <amount> — установить бюджет в TON\n"
    "• /budget show — текущее состояние бюджета\n"
    "• /list — список выбранных NFT\n"
    "• /remove <token_id> — удалить позицию\n"
    "• /flip <token_id> — получить ссылку для покупки/продажи\n"
    "\n"
    "Авто-скан выгодных лотов:\n"
    "• /scan_on — включить уведомления (по умолчанию раз в 5 мин)\n"
    "• /scan_off — выключить уведомления\n"
    "• /scan_now — прогон скана прямо сейчас\n"
    "\n"
    "• /help — показать справку"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    db: Database = context.application.bot_data["db"]
    await db.upsert_user(user.id, user.username)

    log.info("/start from %s", user_label(update))
    await update.message.reply_text(WELCOME)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(WELCOME)

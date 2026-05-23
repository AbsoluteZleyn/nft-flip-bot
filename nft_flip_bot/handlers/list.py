"""Команды /list и /remove."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..models.db import Database
from .utils import render_list_table

log = logging.getLogger(__name__)


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    db: Database = context.application.bot_data["db"]
    items = await db.list_nfts(update.effective_user.id)
    await update.message.reply_text(render_list_table(items))


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /remove <token_id>")
        return

    token_id = args[0].strip()
    db: Database = context.application.bot_data["db"]
    removed = await db.remove_nft(update.effective_user.id, token_id)
    if removed:
        await update.message.reply_text(f"🗑 Удалил `{token_id}` из списка.")
    else:
        await update.message.reply_text(f"NFT `{token_id}` не найден в вашем списке.")

"""Команда /budget."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..models.db import Database
from .utils import fmt_ton

log = logging.getLogger(__name__)


def _format_state(total: float, used: float) -> str:
    remaining = max(total - used, 0.0)
    return (
        f"💰 Бюджет: {fmt_ton(total)} TON. "
        f"Использовано: {fmt_ton(used)} TON. "
        f"Оставшееся: {fmt_ton(remaining)} TON."
    )


async def budget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    db: Database = context.application.bot_data["db"]
    args = context.args or []

    if not args or args[0].lower() in {"show", "status"}:
        state = await db.get_budget(user_id)
        await update.message.reply_text(_format_state(state.total_ton, state.used_ton))
        return

    raw = args[0].replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await update.message.reply_text(
            "Не понял сумму. Пример: /budget 150 (или /budget show)"
        )
        return

    if amount < 0:
        await update.message.reply_text("Бюджет не может быть отрицательным.")
        return

    state = await db.set_budget(user_id, amount)
    await update.message.reply_text(
        "✅ Бюджет обновлён.\n" + _format_state(state.total_ton, state.used_ton)
    )

"""Команда `/range <min> <max>` — пользовательский диапазон цен.

Перекрывает глобальные ``MIN_PRICE_TON`` / ``MAX_PRICE_TON`` для авто-скана
и ручного анализа. Без аргументов показывает текущий диапазон. ``/range off``
сбрасывает к глобальным значениям.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Settings
from ..models.db import Database

log = logging.getLogger(__name__)


def _parse_amount(text: str) -> float | None:
    cleaned = text.strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


async def range_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    db: Database = context.application.bot_data["db"]
    settings: Settings = context.application.bot_data["settings"]
    user_id = update.effective_user.id
    await db.upsert_user(user_id, update.effective_user.username)

    args = list(context.args or [])

    # /range            → показать текущее
    if not args:
        f = await db.get_user_filter(user_id)
        if f is None or (f.min_price_ton is None and f.max_price_ton is None):
            await update.message.reply_text(
                "Диапазон не задан персонально, использую глобальные:\n"
                f"• min = {settings.min_price_ton:.4f} TON\n"
                f"• max = {settings.max_price_ton:.4f} TON\n\n"
                "Задать: `/range <min> <max>` (в TON)\n"
                "Сбросить: `/range off`",
                parse_mode="Markdown",
            )
            return
        min_part = (
            f"{f.min_price_ton:.4f}"
            if f.min_price_ton is not None
            else f"{settings.min_price_ton:.4f} (gl)"
        )
        max_part = (
            f"{f.max_price_ton:.4f}"
            if f.max_price_ton is not None
            else f"{settings.max_price_ton:.4f} (gl)"
        )
        await update.message.reply_text(
            f"Текущий диапазон: {min_part} — {max_part} TON\n"
            "Сбросить: `/range off`",
            parse_mode="Markdown",
        )
        return

    # /range off        → сбросить
    if args[0].lower() in {"off", "reset", "clear", "сброс"}:
        removed = await db.clear_user_filter(user_id)
        if removed:
            await update.message.reply_text(
                "Персональный диапазон сброшен, использую глобальные "
                f"{settings.min_price_ton:.4f}..{settings.max_price_ton:.4f} TON."
            )
        else:
            await update.message.reply_text(
                "Персональный диапазон и так не был задан."
            )
        return

    # /range <min> <max>
    if len(args) != 2:
        await update.message.reply_text(
            "Использование: `/range <min> <max>` (две суммы в TON, например "
            "`/range 0.5 5`). Сбросить: `/range off`.",
            parse_mode="Markdown",
        )
        return

    min_value = _parse_amount(args[0])
    max_value = _parse_amount(args[1])
    if min_value is None or max_value is None:
        await update.message.reply_text(
            "Не понял суммы. Пример: `/range 0.5 5` — диапазон от 0.5 до 5 TON.",
            parse_mode="Markdown",
        )
        return

    if min_value > max_value:
        await update.message.reply_text(
            f"Минимум {min_value:.4f} TON не может быть больше максимума "
            f"{max_value:.4f} TON."
        )
        return

    await db.set_user_filter(user_id, min_value, max_value)
    await update.message.reply_text(
        f"✅ Диапазон установлен: {min_value:.4f}..{max_value:.4f} TON. "
        "Сбросить — `/range off`."
    )

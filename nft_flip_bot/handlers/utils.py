"""Вспомогательные функции для хендлеров."""

from __future__ import annotations

from typing import Iterable

from telegram import Update

from ..models.nft import FlipItem


def fmt_ton(amount: float) -> str:
    """Форматирование TON-сумм для сообщений."""

    return f"{amount:.4f}".rstrip("0").rstrip(".") or "0"


def user_label(update: Update) -> str:
    """Человекочитаемый идентификатор пользователя для логов."""

    user = update.effective_user
    if user is None:
        return "unknown"
    if user.username:
        return f"@{user.username}"
    return str(user.id)


def render_list_table(items: Iterable[FlipItem]) -> str:
    """Markdown-таблица со списком NFT."""

    items = list(items)
    if not items:
        return "Список пуст. Отправьте мне ссылку на NFT в Tonel или его ID."

    header = "| ID | Коллекция | Цена (TON) | Прогноз | Прибыль |\n|---|---|---|---|---|"
    body = "\n".join(
        f"| {item.token_id} | {item.collection or '-'} | "
        f"{fmt_ton(item.current_price_ton)} | "
        f"{fmt_ton(item.resale_price_ton)} | "
        f"{fmt_ton(item.profit_ton)} |"
        for item in items
    )
    return "📊 Ваши NFT для флиппинга:\n" + header + "\n" + body

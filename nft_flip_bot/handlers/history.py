"""Команда `/history <UUID>` — статистика по комбо (модель + фон).

PR #8a — **публичная** часть: сейчас активные листинги с тем же комбо
(`filter_by_models` + `filter_by_backdrops` в API Portals).

Sold-история требует Telethon-авторизации (`Authorization: tma <init_data>`)
и попадёт в отдельный PR #8b.
"""

from __future__ import annotations

import html
import logging
from statistics import median
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import Settings
from ..models.nft import NFTInfo
from ..services.portals_api import PortalsClient
from ..services.tonel_api import TonelError, TonelNotFound

log = logging.getLogger(__name__)


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def _fmt_combo_section(
    target: NFTInfo,
    active: list[NFTInfo],
) -> str:
    """Сформировать блок «сейчас на маркете»."""

    if not active:
        return (
            "🟢 <b>Сейчас на маркете</b>: 0 шт. с таким же комбо.\n"
            "Считайте лот уникальным — можно ставить любую цену."
        )

    prices = sorted(n.price_ton for n in active)
    cheaper_count = sum(1 for p in prices if p < target.price_ton)
    same_count = sum(
        1
        for p in prices
        if abs(p - target.price_ton) < 0.01 and len(active) > 1
    )
    pmin, pmax = prices[0], prices[-1]
    pavg = sum(prices) / len(prices)
    pmed = median(prices)

    lines = [
        f"🟢 <b>Сейчас на маркете</b>: {len(active)} шт.",
        (
            f"   Min {pmin:.2f} · Median {pmed:.2f} · Avg {pavg:.2f} · "
            f"Max {pmax:.2f} TON"
        ),
    ]
    if target.price_ton > 0:
        lines.append(
            f"   Твой лот за {target.price_ton:.2f} TON — "
            f"дешевле в фиде: {cheaper_count}, дороже: "
            f"{len(active) - cheaper_count - same_count}."
        )

    # Топ-3 самых дешёвых для контекста.
    top = active[:3]
    if top:
        lines.append("")
        lines.append("   <i>Самые дешёвые сейчас:</i>")
        for n in top:
            marker = " 👉 (твой)" if n.token_id == target.token_id else ""
            label_name = _esc(n.name or n.token_id)
            lines.append(
                f"   • {n.price_ton:.2f} TON — {label_name}{marker}"
            )

    return "\n".join(lines)


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    settings: Settings = context.application.bot_data["settings"]
    args = list(context.args or [])

    if not args:
        await update.message.reply_text(
            "Использование: <code>/history &lt;UUID&gt;</code>\n"
            "Где UUID — идентификатор подарка (есть в подписи под пушем "
            "уведомления в строке <code>/flip ...</code>).",
            parse_mode=ParseMode.HTML,
        )
        return

    uuid = args[0].strip()

    try:
        async with PortalsClient(
            base_url=settings.portals_api_base
        ) as client:
            try:
                target = await client.fetch_nft(uuid)
            except TonelNotFound:
                await update.message.reply_text(
                    f"Лот <code>{_esc(uuid)}</code> не найден на Portals.",
                    parse_mode=ParseMode.HTML,
                )
                return

            model_name: Optional[str] = target.model.name if target.model else None
            background_name: Optional[str] = (
                target.background.name if target.background else None
            )

            if not model_name or not background_name:
                await update.message.reply_text(
                    "У подарка нет модели или фона — комбо-поиск невозможен.",
                    parse_mode=ParseMode.HTML,
                )
                return

            active = await client.list_combo_active(
                model_name=model_name,
                background_name=background_name,
                limit=100,
            )
    except TonelError as exc:
        log.warning("/history: portals error: %s", exc)
        await update.message.reply_text(
            f"Не удалось получить данные с Portals: {_esc(exc)}",
            parse_mode=ParseMode.HTML,
        )
        return

    title_name = _esc(target.name or target.token_id)
    header = (
        f"🕘 <b>История по комбо</b>\n"
        f"Подарок: <b>{title_name}</b>\n"
        f"Модель: {_esc(model_name)}"
    )
    if target.model and target.model.rarity_percent is not None:
        header += f" ({target.model.rarity_percent:.2f}%)"
    header += f"\nФон: {_esc(background_name)}"
    if target.background and target.background.rarity_percent is not None:
        header += f" ({target.background.rarity_percent:.2f}%)"

    combo_section = _fmt_combo_section(target, active)

    sold_section = (
        "\n⚪ <b>Sold (история продаж)</b>: требует Telethon-настройки "
        "(см. <code>/history_auth</code>, появится в следующем PR)."
    )

    text = f"{header}\n\n{combo_section}\n{sold_section}"
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

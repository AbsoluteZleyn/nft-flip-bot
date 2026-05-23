"""Анализ NFT и добавление в список флиппинга."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..config import Settings
from ..models.db import Database
from ..models.nft import FlipItem
from ..services.price_estimator import estimate_flip, passes_filters
from ..services.tonel_api import (
    TonelClient,
    TonelError,
    TonelNotFound,
    build_trade_link,
    is_allowed_url,
    parse_token_id,
)
from .utils import fmt_ton

log = logging.getLogger(__name__)

ADD_PREFIX = "flip:add:"
SKIP_PREFIX = "flip:skip:"


async def analyze_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимает текстовое сообщение со ссылкой/ID и анализирует NFT."""

    if update.message is None or update.message.text is None:
        return
    if update.effective_user is None:
        return

    text = update.message.text.strip()
    settings: Settings = context.application.bot_data["settings"]
    db: Database = context.application.bot_data["db"]

    # SSRF-guard: если это URL — обязан быть с tonel.io
    if text.lower().startswith(("http://", "https://")) and not is_allowed_url(text):
        await update.message.reply_text(
            "⛔️ Принимаю только ссылки с домена tonel.io."
        )
        return

    token_id = parse_token_id(text)
    if not token_id:
        await update.message.reply_text(
            "Не понял запрос. Пришли ссылку https://tonel.io/item/<id> "
            "или ID токена (буквы/цифры/`_`/`-`, до 64 символов)."
        )
        return

    await db.upsert_user(update.effective_user.id, update.effective_user.username)

    try:
        async with TonelClient() as client:
            nft = await client.fetch_nft(token_id)
    except TonelNotFound:
        await update.message.reply_text(f"❌ NFT `{token_id}` не найден на Tonel.")
        return
    except TonelError as exc:
        log.warning("Tonel error for %s: %s", token_id, exc)
        await update.message.reply_text(
            f"⚠️ Не удалось получить данные по `{token_id}`: {exc}"
        )
        return

    estimate = estimate_flip(nft, settings)
    ok, reason = passes_filters(nft, settings)

    lines = [
        f"🔎 NFT `{nft.token_id}` ({nft.collection or '—'})",
        f"Цена: {fmt_ton(nft.price_ton)} TON",
    ]
    if nft.rank is not None:
        lines.append(f"Ранк: {nft.rank}")
    if nft.volume_24h_ton is not None:
        lines.append(f"24h-объём: {fmt_ton(nft.volume_24h_ton)} TON")
    if nft.volume_7d_ton is not None:
        lines.append(f"7d-объём: {fmt_ton(nft.volume_7d_ton)} TON")

    lines.append(
        f"Прогноз resale: {fmt_ton(estimate.resale_price_ton)} TON "
        f"(growth = {settings.growth_factor:+.0%})"
    )
    lines.append(
        "Комиссия Tonel: "
        f"{fmt_ton(estimate.tonel_fee_ton)} TON · "
        f"Сетевая: {fmt_ton(estimate.tx_fee_ton)} TON"
    )
    lines.append(f"Ожидаемая прибыль: {fmt_ton(estimate.profit_ton)} TON")

    if not ok:
        lines.append(f"⚠️ Не подходит под фильтры: {reason}")
        await update.message.reply_text("\n".join(lines))
        return

    if not estimate.is_profitable:
        lines.append("⚠️ Прибыль не положительная — добавлять невыгодно.")
        await update.message.reply_text("\n".join(lines))
        return

    lines.append(
        f"✅ NFT #{nft.token_id} ({nft.collection or '—'}) стоит "
        f"{fmt_ton(nft.price_ton)} TON. Прогноз resale ≈ "
        f"{fmt_ton(estimate.resale_price_ton)} TON → прибыль ≈ "
        f"{fmt_ton(estimate.profit_ton)} TON. Добавить в список?"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Добавить", callback_data=f"{ADD_PREFIX}{nft.token_id}"
                ),
                InlineKeyboardButton(
                    "✖️ Пропустить", callback_data=f"{SKIP_PREFIX}{nft.token_id}"
                ),
            ]
        ]
    )

    # Сохраним в user_data промежуточный результат, чтобы потом не делать
    # повторный запрос к Tonel.
    pending = context.user_data.setdefault("pending_flips", {})
    pending[nft.token_id] = {
        "collection": nft.collection,
        "current_price_ton": nft.price_ton,
        "resale_price_ton": estimate.resale_price_ton,
        "profit_ton": estimate.profit_ton,
    }

    await update.message.reply_text("\n".join(lines), reply_markup=keyboard)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик inline-кнопок «добавить/пропустить»."""

    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return

    db: Database = context.application.bot_data["db"]
    await query.answer()

    if query.data.startswith(SKIP_PREFIX):
        token_id = query.data[len(SKIP_PREFIX) :]
        context.user_data.get("pending_flips", {}).pop(token_id, None)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if not query.data.startswith(ADD_PREFIX):
        return

    token_id = query.data[len(ADD_PREFIX) :]
    pending = context.user_data.get("pending_flips", {})
    payload = pending.get(token_id)
    if payload is None:
        await query.edit_message_text(
            "Не нашёл данные по этому NFT. Пришли ссылку ещё раз."
        )
        return

    user_id = update.effective_user.id
    state = await db.get_budget(user_id)
    requested_used = state.used_ton + float(payload["current_price_ton"])

    if state.total_ton > 0 and requested_used > state.total_ton:
        await query.edit_message_text(
            "⚠️ Добавление превысит ваш бюджет "
            f"(текущий = {fmt_ton(state.total_ton)} TON, "
            f"требуется = {fmt_ton(requested_used)} TON). "
            "Удалите менее прибыльные позиции или увеличьте бюджет."
        )
        return

    item = FlipItem(
        token_id=token_id,
        user_id=user_id,
        collection=str(payload.get("collection") or ""),
        current_price_ton=float(payload["current_price_ton"]),
        resale_price_ton=float(payload["resale_price_ton"]),
        profit_ton=float(payload["profit_ton"]),
        added_at=datetime.now(timezone.utc),
    )
    await db.add_nft(item)
    pending.pop(token_id, None)

    state = await db.get_budget(user_id)
    await query.edit_message_text(
        f"✅ Добавил `{token_id}` в список. "
        f"Использовано {fmt_ton(state.used_ton)} / "
        f"{fmt_ton(state.total_ton)} TON."
    )


async def flip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/flip <token_id> — выдать готовую ссылку на сделку."""

    if update.message is None or update.effective_user is None:
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /flip <token_id>")
        return

    token_id = args[0].strip()
    db: Database = context.application.bot_data["db"]
    item = await db.get_nft(update.effective_user.id, token_id)
    if item is None:
        await update.message.reply_text(
            f"NFT `{token_id}` не найден в вашем списке."
        )
        return

    link = build_trade_link(token_id, item.resale_price_ton)
    await update.message.reply_text(
        f"🔗 Ссылка для покупки/продажи: {link}"
    )

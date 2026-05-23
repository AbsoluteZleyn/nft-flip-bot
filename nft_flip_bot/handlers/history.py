"""Команда `/history <UUID>` — статистика по комбо (модель + фон).

* **Активные листинги** (всегда доступно): публичный Portals endpoint
  с `filter_by_models` + `filter_by_backdrops`.
* **Sold-история** (опционально): требует ``Authorization: tma <init_data>``.
  Файл с init_data генерируется скриптом
  ``python -m nft_flip_bot.scripts.refresh_init_data`` (см. README).
  Если файла нет — sold-секция показывает инструкцию.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Optional

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


def _load_init_data(path_str: str) -> Optional[str]:
    if not path_str:
        return None
    path = Path(path_str)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("/history: не смог прочитать %s: %s", path, exc)
        return None
    return text or None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _to_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    txt = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(txt)
    except ValueError:
        return None


def _fmt_combo_section(target: NFTInfo, active: list[NFTInfo]) -> str:
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


def _ago(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = now - dt
    if delta.days >= 1:
        return f"{delta.days} дн назад"
    if delta.seconds >= 3600:
        return f"{delta.seconds // 3600} ч назад"
    return f"{max(1, delta.seconds // 60)} мин назад"


def _parse_sale_record(s: dict[str, Any]) -> tuple[Optional[float], Optional[datetime]]:
    price: Optional[float] = None
    for key in ("sold_price", "price", "amount", "sell_price"):
        value = _to_float(s.get(key))
        if value is not None and value > 0:
            price = value
            break
    dt: Optional[datetime] = None
    for key in ("sold_at", "created_at", "executed_at", "completed_at", "date"):
        parsed = _to_dt(s.get(key))
        if parsed is not None:
            dt = parsed
            break
    return price, dt


def _fmt_nft_sales_section(sales: list[dict[str, Any]]) -> str:
    """История продаж **этого конкретного** лота."""

    parsed: list[tuple[Optional[float], Optional[datetime]]] = [
        _parse_sale_record(s) for s in sales
    ]
    parsed = [(p, d) for p, d in parsed if p is not None]

    if not parsed:
        return (
            "🔴 <b>История этого лота</b>: раньше никому не продавался."
        )

    prices = sorted(p for p, _ in parsed if p is not None)
    pmin, pmax = prices[0], prices[-1]
    pavg = sum(prices) / len(prices)
    pmed = median(prices)
    sorted_by_date = sorted(
        ((p, d) for p, d in parsed if d is not None),
        key=lambda x: x[1],
        reverse=True,
    )

    lines = [
        f"🔴 <b>История этого лота</b>: {len(parsed)} продаж(и).",
        (
            f"   Min {pmin:.2f} · Median {pmed:.2f} · Avg {pavg:.2f} · "
            f"Max {pmax:.2f} TON"
        ),
    ]
    if sorted_by_date:
        lines.append("")
        lines.append("   <i>Последние продажи:</i>")
        for price, dt in sorted_by_date[:5]:
            lines.append(
                f"   • {price:.2f} TON — {_ago(dt)}"
            )
    return "\n".join(lines)


def _fmt_combo_sold_section(sold: list[dict[str, Any]]) -> str:
    """Комбо-sold (все проданные с тем же model+background)."""

    parsed = [_parse_sale_record(s) for s in sold]
    parsed = [(p, d) for p, d in parsed if p is not None]

    if not parsed:
        return (
            "🔴 <b>Sold по комбо</b>: данных нет."
        )

    prices = sorted(p for p, _ in parsed if p is not None)
    pmin, pmax = prices[0], prices[-1]
    pavg = sum(prices) / len(prices)
    pmed = median(prices)

    lines = [
        f"🔴 <b>Sold по комбо</b>: {len(parsed)} шт.",
        (
            f"   Min {pmin:.2f} · Median {pmed:.2f} · Avg {pavg:.2f} · "
            f"Max {pmax:.2f} TON"
        ),
    ]
    dated = [(p, d) for p, d in parsed if d is not None]
    if dated:
        last_dt = max(d for _, d in dated)
        lines.append(f"   Последняя продажа: {_ago(last_dt)}")
    return "\n".join(lines)


def _sold_unavailable_section() -> str:
    return (
        "⚪ <b>Sold (история продаж)</b>: не настроено.\n"
        "Для активации запусти локально:\n"
        "<code>python -m nft_flip_bot.scripts.refresh_init_data</code>\n"
        "Подробности — см. README."
    )


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
    init_data = _load_init_data(settings.portals_init_data_path)

    try:
        async with PortalsClient(
            base_url=settings.portals_api_base,
            init_data=init_data,
        ) as client:
            try:
                target = await client.fetch_nft(uuid)
            except TonelNotFound:
                await update.message.reply_text(
                    f"Лот <code>{_esc(uuid)}</code> не найден на Portals.",
                    parse_mode=ParseMode.HTML,
                )
                return

            model_name = target.model.name if target.model else None
            background_name = (
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

            nft_sales: list[dict[str, Any]] = []
            nft_sales_error: Optional[str] = None
            combo_sold: list[dict[str, Any]] = []
            combo_sold_error: Optional[str] = None
            if init_data:
                try:
                    nft_sales = await client.list_nft_sales(
                        uuid=uuid,
                        limit=50,
                    )
                except TonelError as exc:
                    nft_sales_error = str(exc)
                    log.info("/history: nft_sales lookup failed: %s", exc)
                try:
                    combo_sold = await client.list_combo_sold(
                        model_name=model_name,
                        background_name=background_name,
                        limit=50,
                    )
                except TonelError as exc:
                    combo_sold_error = str(exc)
                    log.info("/history: combo_sold lookup failed: %s", exc)
    except TonelError as exc:
        log.warning("/history: portals error: %s", exc)
        await update.message.reply_text(
            f"Не удалось получить данные с Portals: {_esc(exc)}",
            parse_mode=ParseMode.HTML,
        )
        return

    title_name = _esc(target.name or target.token_id)
    header_lines = [
        f"🕘 <b>История по комбо</b>",
        f"Подарок: <b>{title_name}</b>",
    ]
    model_line = f"Модель: {_esc(model_name)}"
    if target.model and target.model.rarity_percent is not None:
        model_line += f" ({target.model.rarity_percent:.2f}%)"
    header_lines.append(model_line)
    bg_line = f"Фон: {_esc(background_name)}"
    if target.background and target.background.rarity_percent is not None:
        bg_line += f" ({target.background.rarity_percent:.2f}%)"
    header_lines.append(bg_line)
    header = "\n".join(header_lines)

    combo_section = _fmt_combo_section(target, active)

    sections: list[str] = [combo_section]

    if init_data:
        if nft_sales_error:
            sections.append(
                "🟠 <b>История этого лота</b>: init_data не подошёл "
                f"(<code>{_esc(nft_sales_error[:200])}</code>). "
                "Перевыпусти <code>refresh_init_data</code>."
            )
        else:
            sections.append(_fmt_nft_sales_section(nft_sales))

        if combo_sold_error:
            sections.append(
                "⚪ <b>Sold по комбо</b>: комбо-эндпоинт в Portals пока не найден. "
                f"Детали в серверном логе (пришли мне строку «Portals combo-sold candidates failures»)."
            )
        else:
            sections.append(_fmt_combo_sold_section(combo_sold))
    else:
        sections.append(_sold_unavailable_section())

    text = f"{header}\n\n" + "\n\n".join(sections)
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

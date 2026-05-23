"""Автоматический сканер выгодных NFT.

Раз в `SCAN_INTERVAL_MIN` минут запрашивает список свежих лотов через
`TonelClient.scan_listings`, прогоняет их через те же фильтры и расчёт
прибыли, что и ручной анализ, и шлёт в Telegram уведомления тем
пользователям, кто включил подписку через `/scan_on`.

Дедуп — таблица `notified` в БД: один и тот же `token_id` каждому
пользователю отправится максимум один раз.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiocron
from telegram import Bot
from telegram.error import TelegramError

from ..config import Settings
from ..models.db import Database
from ..models.nft import NFTInfo
from .price_estimator import estimate_flip, passes_filters
from .tonel_api import TonelClient, build_portals_link, build_trade_link

log = logging.getLogger(__name__)


def _cron_every_n_min(n: int) -> str:
    """Сформировать cron-строку «каждые N минут» (1..59)."""

    n = max(1, min(59, int(n)))
    if n == 1:
        return "* * * * *"
    return f"*/{n} * * * *"


class AutoScanner:
    """Фоновая задача: сканит маркет и шлёт выгодные лоты подписчикам."""

    def __init__(self, bot: Bot, db: Database, settings: Settings) -> None:
        self._bot = bot
        self._db = db
        self._settings = settings
        self._task: Optional[aiocron.Cron] = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is not None:
            return
        cron = _cron_every_n_min(self._settings.scan_interval_min)
        self._task = aiocron.crontab(cron, func=self.run_once, start=True)
        log.info(
            "AutoScanner started: cron=%s, limit=%d, max_notify=%d",
            cron,
            self._settings.scan_limit,
            self._settings.scan_max_notify,
        )

    def stop(self) -> None:
        if self._task is not None:
            self._task.stop()
            self._task = None
            log.info("AutoScanner stopped")

    async def run_once(self) -> dict[int, int]:
        """Один прогон. Возвращает {user_id: число отправленных уведомлений}."""

        # Не позволяем накладываться, если предыдущий прогон затянулся.
        if self._lock.locked():
            log.info("AutoScanner: предыдущий прогон ещё идёт, пропускаю")
            return {}

        async with self._lock:
            users = await self._db.list_subscribed_users()
            if not users:
                log.debug("AutoScanner: нет подписчиков")
                return {}

            log.info("AutoScanner: %d подписчик(а/ов), запрашиваю фид", len(users))
            async with TonelClient() as client:
                listings = await client.scan_listings(limit=self._settings.scan_limit)

            if not listings:
                log.info("AutoScanner: фид пуст (или эндпоинт ещё не подключён)")
                return {}

            sent_per_user: dict[int, int] = {}
            for user_id in users:
                candidates = await self._filter_candidates_for_user(listings, user_id)
                sent_per_user[user_id] = await self.notify_user(user_id, candidates)
            log.info(
                "AutoScanner: обработал %d лотов, пушей по пользователям=%s",
                len(listings),
                sent_per_user,
            )
            return sent_per_user

    async def run_for_user(self, user_id: int) -> int:
        """Однократный прогон для конкретного пользователя (`/scan_now`).

        В отличие от ``run_once`` берёт ``self._lock`` блокирующе: пользователь
        явно попросил скан и ждёт результат, пропускать прогон молча нельзя.
        Lock защищает от race с плановым прогоном: иначе одинаковый
        ``(user_id, token_id)`` мог пройти через ``was_notified``/``send_message``
        одновременно в двух корутинах и привести к дублям уведомлений.
        """

        async with self._lock:
            async with TonelClient() as client:
                listings = await client.scan_listings(limit=self._settings.scan_limit)

            candidates = await self._filter_candidates_for_user(listings, user_id)
            return await self.notify_user(user_id, candidates)

    # ------------------------------------------------------------------

    async def _filter_candidates_for_user(
        self,
        listings: list[NFTInfo],
        user_id: int,
    ) -> list[tuple[NFTInfo, float]]:
        """Выбрать выгодные лоты с учётом персонального ``/range``."""

        user_filter = await self._db.get_user_filter(user_id)
        result: list[tuple[NFTInfo, float]] = []
        for nft in listings:
            ok, _reason = passes_filters(nft, self._settings, user_filter)
            if not ok:
                continue
            estimate = estimate_flip(nft, self._settings)
            if not estimate.is_profitable:
                continue
            result.append((nft, estimate.profit_ton))

        # Сортируем по убыванию прибыли — сначала самые выгодные.
        result.sort(key=lambda x: x[1], reverse=True)
        return result

    async def notify_user(
        self,
        user_id: int,
        candidates: list[tuple[NFTInfo, float]],
    ) -> int:
        """Отправить пользователю до `scan_max_notify` новых лотов."""

        if not candidates:
            return 0

        sent = 0
        for nft, profit in candidates:
            if sent >= self._settings.scan_max_notify:
                break
            if await self._db.was_notified(user_id, nft.token_id):
                continue

            try:
                await self._send_one(user_id, nft, profit)
            except TelegramError as exc:
                log.warning(
                    "AutoScanner: не смог уведомить user_id=%s: %s", user_id, exc
                )
                # Если бот заблокирован пользователем — отписываем.
                if "blocked" in str(exc).lower() or "chat not found" in str(exc).lower():
                    await self._db.set_subscription(user_id, False)
                    log.info("AutoScanner: отписал user_id=%s (бот заблокирован)", user_id)
                    return sent
                continue

            await self._db.mark_notified(user_id, nft.token_id)
            sent += 1
        return sent

    async def _send_one(self, user_id: int, nft: NFTInfo, profit: float) -> None:
        """Отправить одно уведомление: фото+подпись, или текст без фото."""

        caption = self._format_notification(nft, profit)
        if nft.photo_url:
            try:
                await self._bot.send_photo(
                    chat_id=user_id,
                    photo=nft.photo_url,
                    caption=caption,
                    parse_mode="Markdown",
                )
                return
            except TelegramError as exc:
                log.info(
                    "AutoScanner: send_photo упал, фоллбэк на текст (%s)", exc
                )
        await self._bot.send_message(
            chat_id=user_id,
            text=caption,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    def _format_notification(self, nft: NFTInfo, profit: float) -> str:
        estimate = estimate_flip(nft, self._settings)
        collection = nft.collection or "—"
        title_name = nft.name or nft.token_id
        gain_percent = (profit / nft.price_ton * 100.0) if nft.price_ton else 0.0

        lines = [
            f"🔔 Найден выгодный подарок: *{title_name}* ({collection})",
        ]
        if nft.model is not None:
            lines.append(f"Модель: {nft.model.label()}")
        if nft.background is not None:
            lines.append(f"Фон: {nft.background.label()}")
        if nft.pattern is not None:
            lines.append(f"Узор: {nft.pattern.label()}")
        if nft.rank is not None:
            lines.append(f"Ранк: {nft.rank}")

        lines.append("")
        lines.append(f"Цена: {nft.price_ton:.4f} TON")
        lines.append(f"Resale ≈ {estimate.resale_price_ton:.4f} TON")
        lines.append(
            f"Комиссия: buy {estimate.buy_fee_ton:.4f} · "
            f"sell {estimate.sell_fee_ton:.4f} · "
            f"tx {estimate.tx_fee_ton:.4f} TON"
        )
        lines.append(
            f"Прибыль ≈ {profit:.4f} TON ({gain_percent:+.1f}%)"
        )
        lines.append("")

        portals_link = nft.portals_link or build_portals_link(nft.token_id)
        if nft.telegram_link:
            lines.append(f"📱 Telegram: {nft.telegram_link}")
        lines.append(f"🛒 Купить: {portals_link}")
        # Старая ссылка на trade оставлена для обратной совместимости с
        # пользователями текущей версии Tonel/реверс-источника.
        legacy_link = build_trade_link(nft.token_id, estimate.resale_price_ton)
        lines.append(f"🔗 {legacy_link}")
        lines.append("")
        lines.append(f"➕ Добавить в портфель: /flip {nft.token_id}")
        return "\n".join(lines)

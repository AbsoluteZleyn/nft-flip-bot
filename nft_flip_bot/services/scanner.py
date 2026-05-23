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
from .tonel_api import TonelClient, build_trade_link

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

            candidates = self._filter_candidates(listings)
            if not candidates:
                log.info(
                    "AutoScanner: %d лотов в фиде, но ни один не прошёл фильтры",
                    len(listings),
                )
                return {}

            log.info(
                "AutoScanner: %d/%d лотов прошли фильтры",
                len(candidates),
                len(listings),
            )

            sent_per_user: dict[int, int] = {}
            for user_id in users:
                sent_per_user[user_id] = await self.notify_user(user_id, candidates)
            return sent_per_user

    async def run_for_user(self, user_id: int) -> int:
        """Однократный прогон для конкретного пользователя (`/scan_now`)."""

        async with TonelClient() as client:
            listings = await client.scan_listings(limit=self._settings.scan_limit)

        candidates = self._filter_candidates(listings)
        return await self.notify_user(user_id, candidates)

    # ------------------------------------------------------------------

    def _filter_candidates(self, listings: list[NFTInfo]) -> list[tuple[NFTInfo, float]]:
        """Оставить только выгодные лоты. Возвращает [(nft, profit_ton), ...]."""

        result: list[tuple[NFTInfo, float]] = []
        for nft in listings:
            ok, _reason = passes_filters(nft, self._settings)
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
                await self._bot.send_message(
                    chat_id=user_id,
                    text=self._format_notification(nft, profit),
                    disable_web_page_preview=True,
                )
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

    def _format_notification(self, nft: NFTInfo, profit: float) -> str:
        estimate = estimate_flip(nft, self._settings)
        collection = nft.collection or "—"
        lines = [
            f"🔔 Найден выгодный NFT: `{nft.token_id}` ({collection})",
            f"Цена: {nft.price_ton:.4f} TON · "
            f"Resale ≈ {estimate.resale_price_ton:.4f} TON",
            f"Ожидаемая прибыль ≈ {profit:.4f} TON "
            f"(growth = {self._settings.growth_factor:+.0%})",
        ]
        if nft.rank is not None:
            lines.append(f"Ранк: {nft.rank}")
        link = build_trade_link(nft.token_id, estimate.resale_price_ton)
        lines.append(f"🔗 {link}")
        lines.append(f"➕ Добавить: /flip {nft.token_id}")
        return "\n".join(lines)

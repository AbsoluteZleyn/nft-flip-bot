"""Периодическое обновление цен через aiocron."""

from __future__ import annotations

import logging
from typing import Optional

import aiocron

from ..config import Settings
from ..models.db import Database
from .price_estimator import estimate_flip
from .tonel_api import TonelClient, TonelError

log = logging.getLogger(__name__)

# Каждый день в 03:00 по UTC обновляем цены.
DEFAULT_CRON = "0 3 * * *"


class PriceRefresher:
    """Обновляет цены и пересчитывает прибыль для всех сохранённых NFT."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._task: Optional[aiocron.Cron] = None

    def start(self, cron: str = DEFAULT_CRON) -> None:
        if self._task is not None:
            return
        self._task = aiocron.crontab(cron, func=self.run_once, start=True)
        log.info("Scheduler started: %s", cron)

    def stop(self) -> None:
        if self._task is not None:
            self._task.stop()
            self._task = None
            log.info("Scheduler stopped")

    async def run_once(self) -> None:
        """Один проход обновления."""

        rows = await self._db.all_token_ids()
        if not rows:
            log.info("Refresh: ничего обновлять")
            return

        log.info("Refresh: %d позиций", len(rows))

        async with TonelClient() as client:
            for user_id, token_id in rows:
                try:
                    nft = await client.fetch_nft(token_id)
                except TonelError as exc:
                    log.warning("Не удалось обновить %s: %s", token_id, exc)
                    continue

                estimate = estimate_flip(nft, self._settings)
                await self._db.update_price(
                    user_id=user_id,
                    token_id=token_id,
                    current_price_ton=estimate.current_price_ton,
                    resale_price_ton=estimate.resale_price_ton,
                    profit_ton=estimate.profit_ton,
                )

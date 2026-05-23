"""Оценка resale-цены и прибыли с учётом комиссий по обе стороны сделки."""

from __future__ import annotations

from typing import Optional

from ..config import Settings
from ..models.nft import FlipEstimate, NFTInfo, UserFilter


def passes_filters(
    nft: NFTInfo,
    settings: Settings,
    user_filter: Optional[UserFilter] = None,
) -> tuple[bool, str]:
    """Подходит ли NFT под критерии флиппинга.

    Если у пользователя задан персональный диапазон цен через ``/range``,
    он перекрывает глобальные ``MIN_PRICE_TON``/``MAX_PRICE_TON``.

    Возвращает (ok, причина_если_нет).
    """

    min_price = settings.min_price_ton
    max_price = settings.max_price_ton
    if user_filter is not None:
        if user_filter.min_price_ton is not None:
            min_price = user_filter.min_price_ton
        if user_filter.max_price_ton is not None:
            max_price = user_filter.max_price_ton

    if nft.price_ton < min_price:
        return False, (
            f"цена {nft.price_ton:.4f} TON < минимум {min_price:.4f} TON"
        )

    if nft.price_ton > max_price:
        return False, (
            f"цена {nft.price_ton:.4f} TON > лимит {max_price:.4f} TON"
        )

    if nft.rank is not None and nft.rank > settings.max_rank:
        return False, f"ранк {nft.rank} > лимит {settings.max_rank}"

    if nft.volume_24h_ton is not None and nft.volume_24h_ton < settings.min_volume_ton:
        return False, (
            f"24h-объём {nft.volume_24h_ton:.4f} TON < минимум "
            f"{settings.min_volume_ton:.4f} TON"
        )

    return True, ""


def estimate_flip(nft: NFTInfo, settings: Settings) -> FlipEstimate:
    """Расчёт ожидаемой resale-цены и прибыли.

    Комиссии:
    * ``buy_fee``   = ``CurrentPrice * tonel_buy_fee``   (берётся при покупке)
    * ``sell_fee``  = ``Resale       * tonel_fee``       (берётся при продаже)
    * ``tx_fee``    = ``tx_fee_ton * 2``                 (сетевая, две транзакции)

    Resale  = ``CurrentPrice * (1 + GrowthFactor)``
    Profit  = ``Resale - CurrentPrice - buy_fee - sell_fee - tx_fee``
    """

    resale = nft.price_ton * (1.0 + settings.growth_factor)
    buy_fee = nft.price_ton * settings.tonel_buy_fee
    sell_fee = resale * settings.tonel_fee
    tx_fee = settings.tx_fee_ton * 2.0
    profit = resale - nft.price_ton - buy_fee - sell_fee - tx_fee

    return FlipEstimate(
        token_id=nft.token_id,
        current_price_ton=nft.price_ton,
        resale_price_ton=resale,
        buy_fee_ton=buy_fee,
        sell_fee_ton=sell_fee,
        tx_fee_ton=tx_fee,
        profit_ton=profit,
        growth_factor=settings.growth_factor,
    )

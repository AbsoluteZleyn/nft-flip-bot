"""Оценка resale-цены и прибыли."""

from __future__ import annotations

from ..config import Settings
from ..models.nft import FlipEstimate, NFTInfo


def passes_filters(nft: NFTInfo, settings: Settings) -> tuple[bool, str]:
    """Подходит ли NFT под критерии флиппинга.

    Возвращает (ok, причина_если_нет).
    """

    if nft.price_ton > settings.max_price_ton:
        return False, (
            f"цена {nft.price_ton:.4f} TON > лимит {settings.max_price_ton:.4f} TON"
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

    Resale = CurrentPrice * (1 + GrowthFactor)
    Profit = Resale - CurrentPrice - комиссия Tonel - сетевая комиссия
    """

    resale = nft.price_ton * (1.0 + settings.growth_factor)
    tonel_fee_ton = resale * settings.tonel_fee
    profit = resale - nft.price_ton - tonel_fee_ton - settings.tx_fee_ton

    return FlipEstimate(
        token_id=nft.token_id,
        current_price_ton=nft.price_ton,
        resale_price_ton=resale,
        tonel_fee_ton=tonel_fee_ton,
        tx_fee_ton=settings.tx_fee_ton,
        profit_ton=profit,
        growth_factor=settings.growth_factor,
    )

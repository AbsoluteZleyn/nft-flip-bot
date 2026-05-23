"""Pydantic-модели предметной области."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Attribute(BaseModel):
    """Одно свойство NFT-подарка (модель, фон или узор)."""

    name: str = Field(..., description="Название атрибута (e.g. 'Sakura')")
    rarity_percent: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Доля владельцев с таким атрибутом, в процентах. "
                    "Чем меньше, тем редче.",
    )

    def label(self) -> str:
        """Человеко-читаемая строка, например ``Sakura (0.5%)``."""

        if self.rarity_percent is None:
            return self.name
        return f"{self.name} ({self.rarity_percent:.2f}%)"


class NFTInfo(BaseModel):
    """Срез данных по NFT-подарку из маркетплейса."""

    token_id: str = Field(..., description="ID токена в маркетплейсе")
    collection: str = Field(default="", description="Коллекция / название")
    name: str = Field(default="", description="Имя конкретного экземпляра")
    price_ton: float = Field(..., ge=0, description="Текущая цена в TON")
    rank: Optional[int] = Field(default=None, description="Ранк/редкость, если есть")
    volume_24h_ton: Optional[float] = Field(default=None, ge=0)
    volume_7d_ton: Optional[float] = Field(default=None, ge=0)
    history: list[tuple[datetime, float]] = Field(default_factory=list)
    url: str = Field(default="", description="Прямая ссылка на лот в маркетплейсе")

    # ── Telegram-Gift специфика ─────────────────────────────────────────
    model: Optional[Attribute] = Field(
        default=None, description="Модель подарка (e.g. 'Sakura')"
    )
    background: Optional[Attribute] = Field(
        default=None, description="Фон подарка"
    )
    pattern: Optional[Attribute] = Field(
        default=None, description="Узор подарка"
    )
    photo_url: Optional[str] = Field(
        default=None, description="URL картинки превью подарка"
    )
    telegram_link: Optional[str] = Field(
        default=None,
        description="Ссылка на подарок в Telegram (t.me/nft/<slug>-<num>)",
    )
    portals_link: Optional[str] = Field(
        default=None,
        description="Ссылка на лот в Portals Mini App "
                    "(t.me/portals/market?startapp=gift_<id>)",
    )


class FlipEstimate(BaseModel):
    """Расчёт перепродажи с учётом комиссий по обе стороны сделки."""

    token_id: str
    current_price_ton: float
    resale_price_ton: float
    buy_fee_ton: float = Field(default=0.0, description="Комиссия маркетплейса при покупке")
    sell_fee_ton: float = Field(default=0.0, description="Комиссия маркетплейса при продаже")
    tx_fee_ton: float = Field(default=0.0, description="Суммарная сетевая комиссия (buy+sell)")
    profit_ton: float
    growth_factor: float

    @property
    def is_profitable(self) -> bool:
        return self.profit_ton > 0


class FlipItem(BaseModel):
    """Запись о NFT, добавленном в список флиппинга."""

    token_id: str
    user_id: int
    collection: str
    current_price_ton: float
    resale_price_ton: float
    profit_ton: float
    added_at: datetime


class BudgetState(BaseModel):
    """Состояние бюджета пользователя."""

    user_id: int
    total_ton: float = 0.0
    used_ton: float = 0.0

    @property
    def remaining_ton(self) -> float:
        return max(self.total_ton - self.used_ton, 0.0)


class UserFilter(BaseModel):
    """Персональные фильтры пользователя (перекрывают глобальные настройки)."""

    user_id: int
    min_price_ton: Optional[float] = None
    max_price_ton: Optional[float] = None

"""Pydantic-модели предметной области."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class NFTInfo(BaseModel):
    """Срез данных по NFT, который мы получаем из Tonel."""

    token_id: str = Field(..., description="ID токена в Tonel")
    collection: str = Field(default="", description="Коллекция / название")
    name: str = Field(default="", description="Имя конкретного экземпляра")
    price_ton: float = Field(..., ge=0, description="Текущая цена в TON")
    rank: Optional[int] = Field(default=None, description="Ранк/редкость, если есть")
    volume_24h_ton: Optional[float] = Field(default=None, ge=0)
    volume_7d_ton: Optional[float] = Field(default=None, ge=0)
    history: list[tuple[datetime, float]] = Field(default_factory=list)
    url: str = Field(default="", description="Прямая ссылка на лот в Tonel")


class FlipEstimate(BaseModel):
    """Расчёт перепродажи."""

    token_id: str
    current_price_ton: float
    resale_price_ton: float
    tonel_fee_ton: float
    tx_fee_ton: float
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

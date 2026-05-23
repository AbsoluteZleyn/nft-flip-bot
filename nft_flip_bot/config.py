"""Конфигурация бота. Считывается из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Настройки бота."""

    bot_token: str
    db_path: str

    # Фильтры «флиппинг-подходит ли NFT»
    min_price_ton: float
    max_price_ton: float
    max_rank: int
    min_volume_ton: float

    # Параметры расчёта прибыли
    growth_factor: float
    tonel_fee: float       # комиссия маркета при ПРОДАЖЕ
    tonel_buy_fee: float   # комиссия маркета при ПОКУПКЕ
    tx_fee_ton: float      # сетевая комиссия за ОДНУ транзакцию

    # Авто-сканер
    scan_interval_min: int
    scan_max_notify: int
    scan_limit: int

    # Прочее
    tonel_allowed_host: str = "tonel.io"


def load_settings() -> Settings:
    """Загрузить настройки из окружения."""

    tonel_fee = _env_float("TONEL_FEE", 0.05)
    # По умолчанию комиссия покупки равна комиссии продажи; можно
    # переопределить, если маркет берёт ассиметричный fee.
    tonel_buy_fee = _env_float("TONEL_BUY_FEE", tonel_fee)

    return Settings(
        bot_token=os.getenv("BOT_TOKEN", ""),
        db_path=os.getenv("DB_PATH", "nft_flip_bot.sqlite3"),
        min_price_ton=_env_float("MIN_PRICE_TON", 0.0),
        max_price_ton=_env_float("MAX_PRICE_TON", 5.0),
        max_rank=_env_int("MAX_RANK", 1000),
        min_volume_ton=_env_float("MIN_VOLUME_TON", 1.0),
        growth_factor=_env_float("GROWTH_FACTOR", 0.5),
        tonel_fee=tonel_fee,
        tonel_buy_fee=tonel_buy_fee,
        tx_fee_ton=_env_float("TX_FEE_TON", 0.05),
        scan_interval_min=_env_int("SCAN_INTERVAL_MIN", 1),
        scan_max_notify=_env_int("SCAN_MAX_NOTIFY", 5),
        scan_limit=_env_int("SCAN_LIMIT", 50),
    )

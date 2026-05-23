"""Обёртка над Tonel API.

Публичный API сервиса Tonel в рамках задачи документирован не был,
поэтому реализован условный JSON-эндпоинт + fallback на парсинг
HTML-страницы лота. Для продакшена подставьте реальные пути.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ..models.nft import NFTInfo

log = logging.getLogger(__name__)

ALLOWED_HOST = "tonel.io"
TONEL_BASE = f"https://{ALLOWED_HOST}"

# TODO: уточнить реальные пути API Tonel перед продакшеном.
TONEL_ITEM_API = TONEL_BASE + "/api/v1/items/{token_id}"
TONEL_ITEM_PAGE = TONEL_BASE + "/item/{token_id}"
TONEL_TRADE_LINK = TONEL_BASE + "/trade?item={token_id}&price={price}"

# Принимаем только HTTPS-ссылки на tonel.io (см. требования к безопасности).
ITEM_URL_RE = re.compile(
    r"^https://(?:www\.)?tonel\.io/item/(?P<token_id>[A-Za-z0-9_\-]+)/?(?:\?.*)?$"
)
TOKEN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _first_not_none(*values: Any) -> Any:
    """Вернуть первое значение, которое строго не None.

    Используется вместо ``a or b`` при выборе альтернативного поля payload'а,
    т.к. ``or`` считает ложными в т.ч. ``0`` и пустую строку.
    """

    for value in values:
        if value is not None:
            return value
    return None


class TonelError(RuntimeError):
    """Базовая ошибка взаимодействия с Tonel."""


class TonelNotFound(TonelError):
    """Лот не найден или скрыт."""


def is_allowed_url(url: str) -> bool:
    """Только tonel.io по HTTPS — защита от SSRF."""

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST)


def parse_token_id(text: str) -> Optional[str]:
    """Извлечь token_id из ссылки на Tonel или из «голого» ID."""

    text = text.strip()
    if not text:
        return None

    m = ITEM_URL_RE.match(text)
    if m:
        return m.group("token_id")

    # URL не из tonel.io / не HTTPS — отбрасываем (SSRF guard).
    if text.lower().startswith(("http://", "https://")):
        return None

    if TOKEN_ID_RE.match(text):
        return text

    return None


def build_trade_link(token_id: str, price_ton: float) -> str:
    return TONEL_TRADE_LINK.format(token_id=token_id, price=f"{price_ton:.4f}")


def _parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.,\-]", "", value).replace(",", ".")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _parse_int(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9\-]", "", value)
        if not cleaned:
            return None
        try:
            return int(cleaned)
        except ValueError:
            return None
    return None


class TonelClient:
    """Асинхронный клиент Tonel."""

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 15.0,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            http2=True,
            timeout=timeout,
            headers={
                "User-Agent": "nft-flip-bot/1.0 (+https://tonel.io)",
                "Accept": "application/json, text/html;q=0.8",
            },
            follow_redirects=True,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "TonelClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ------------------------------------------------------------------

    async def fetch_nft(self, token_id: str) -> NFTInfo:
        """Сначала пробуем JSON API, затем fallback на парсинг HTML."""

        if not TOKEN_ID_RE.match(token_id):
            raise TonelError(f"Некорректный token_id: {token_id!r}")

        try:
            return await self._fetch_via_api(token_id)
        except TonelNotFound:
            raise
        except TonelError as exc:
            log.warning("Tonel API недоступен (%s), пробую HTML", exc)
        except httpx.HTTPError as exc:
            log.warning("Tonel API HTTP error (%s), пробую HTML", exc)

        try:
            return await self._fetch_via_html(token_id)
        except httpx.HTTPError as exc:
            raise TonelError(f"Сетевая ошибка при обращении к Tonel: {exc}") from exc

    # ------------------------------------------------------------------

    async def _fetch_via_api(self, token_id: str) -> NFTInfo:
        url = TONEL_ITEM_API.format(token_id=token_id)
        resp = await self._client.get(url)
        if resp.status_code == 404:
            raise TonelNotFound(f"NFT {token_id} не найден")
        if resp.status_code >= 400:
            raise TonelError(f"Tonel API HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise TonelError("Tonel API вернул не-JSON") from exc

        # TODO: подогнать под реальную структуру ответа Tonel.
        # Используем _first_not_none, чтобы корректно пробрасывать значения
        # типа 0 (валидная цена/ранк), которые ``or`` посчитал бы ложными.
        price = _parse_float(_first_not_none(payload.get("price_ton"), payload.get("price")))
        if price is None:
            raise TonelError("В ответе нет поля цены")

        history_raw = payload.get("price_history")
        if history_raw is None:
            history_raw = []
        history: list[tuple[datetime, float]] = []
        for entry in history_raw:
            ts = _first_not_none(entry.get("ts"), entry.get("date"))
            value = _parse_float(entry.get("price"))
            if ts is None or value is None:
                continue
            try:
                history.append((datetime.fromisoformat(str(ts)), value))
            except ValueError:
                continue

        raw_id = _first_not_none(payload.get("id"), token_id)
        raw_collection = _first_not_none(
            payload.get("collection"), payload.get("collection_name"), ""
        )
        raw_name = _first_not_none(payload.get("name"), "")
        raw_rank = _first_not_none(payload.get("rank"), payload.get("rarity_rank"))

        return NFTInfo(
            token_id=str(raw_id),
            collection=str(raw_collection),
            name=str(raw_name),
            price_ton=price,
            rank=_parse_int(raw_rank),
            volume_24h_ton=_parse_float(payload.get("volume_24h_ton")),
            volume_7d_ton=_parse_float(payload.get("volume_7d_ton")),
            history=history,
            url=TONEL_ITEM_PAGE.format(token_id=token_id),
        )

    # ------------------------------------------------------------------

    async def _fetch_via_html(self, token_id: str) -> NFTInfo:
        url = TONEL_ITEM_PAGE.format(token_id=token_id)
        resp = await self._client.get(url)
        if resp.status_code == 404:
            raise TonelNotFound(f"NFT {token_id} не найден")
        if resp.status_code >= 400:
            raise TonelError(f"Tonel HTML HTTP {resp.status_code}")

        soup = BeautifulSoup(resp.text, "html.parser")

        # TODO: подобрать реальные селекторы под актуальную вёрстку Tonel.
        def _attr(name: str) -> Optional[str]:
            tag = soup.find(attrs={"data-field": name})
            if tag is None:
                tag = soup.find(attrs={"data-test": name})
            if tag is None:
                return None
            text = tag.get_text(strip=True)
            if text:
                return text
            content = tag.get("content")
            return content if content else None

        price = _parse_float(_first_not_none(_attr("price"), _attr("price-ton")))
        if price is None:
            # TODO: при необходимости добавить обработку капчи.
            raise TonelError("Не удалось распарсить цену из HTML")

        return NFTInfo(
            token_id=token_id,
            collection=_attr("collection") or "",
            name=_attr("name") or "",
            price_ton=price,
            rank=_parse_int(_attr("rank")),
            volume_24h_ton=_parse_float(_attr("volume-24h")),
            volume_7d_ton=_parse_float(_attr("volume-7d")),
            url=url,
        )

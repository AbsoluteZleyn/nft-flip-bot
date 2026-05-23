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

from ..models.nft import Attribute, NFTInfo

log = logging.getLogger(__name__)

ALLOWED_HOST = "tonel.io"
TONEL_BASE = f"https://{ALLOWED_HOST}"

# TODO: уточнить реальные пути API Tonel перед продакшеном.
TONEL_ITEM_API = TONEL_BASE + "/api/v1/items/{token_id}"
TONEL_ITEM_PAGE = TONEL_BASE + "/item/{token_id}"
TONEL_TRADE_LINK = TONEL_BASE + "/trade?item={token_id}&price={price}"
# TODO: эндпоинт фида лотов (для авто-скана). Без реальных путей Tonel
# скан вернёт пустой список — это ожидаемо и попадаёт в логи.
TONEL_LISTINGS_API = TONEL_BASE + "/api/v1/listings"

# Telegram Mini App, через который пользователь покупает подарок в Portals.
# Этот URL открывается из Telegram-клиента, домен portals-market.com у
# Portals напрямую недоступен из обычного браузера.
PORTALS_MINIAPP_TEMPLATE = "https://t.me/portals/market?startapp=gift_{token_id}"
# Прямая ссылка на подарок в Telegram (web preview работает без авторизации).
TELEGRAM_GIFT_TEMPLATE = "https://t.me/nft/{slug}"

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


def build_portals_link(token_id: str) -> str:
    """Сформировать ссылку на лот в Portals Mini App.

    Открывается из любого Telegram-клиента (мобильного и десктопного).
    """

    return PORTALS_MINIAPP_TEMPLATE.format(token_id=token_id)


def build_telegram_gift_link(slug: Optional[str]) -> Optional[str]:
    """Сформировать ссылку на подарок в Telegram (``t.me/nft/<slug>``).

    ``slug`` — это «<CollectionName>-<number>», например ``PlushPepe-12345``.
    Если фид не прислал slug, возвращаем None: бот не должен генерить
    некорректные deep-link'и.
    """

    if not slug:
        return None
    cleaned = str(slug).strip().lstrip("/")
    if not cleaned:
        return None
    return TELEGRAM_GIFT_TEMPLATE.format(slug=cleaned)


def _parse_attribute(raw: object) -> Optional[Attribute]:
    """Распарсить одно свойство подарка из payload'а маркетплейса.

    Поддерживаем два формата:
    * ``{"name": "Sakura", "rarity_percent": 0.5}`` — структурный
    * ``"Sakura"``                                   — голая строка без редкости
    """

    if raw is None:
        return None
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            return None
        return Attribute(name=name)
    if isinstance(raw, dict):
        name = _first_not_none(raw.get("name"), raw.get("value"), raw.get("label"))
        if name is None:
            return None
        rarity = _parse_float(
            _first_not_none(
                raw.get("rarity_percent"),
                raw.get("rarity"),
                raw.get("percent"),
            )
        )
        # Нормализуем: если редкость пришла как доля (0.005), переведём в %.
        if rarity is not None and 0 <= rarity <= 1 and "rarity" in raw and "rarity_percent" not in raw:
            rarity = rarity * 100.0
        if rarity is not None and rarity > 100:
            rarity = None
        return Attribute(name=str(name), rarity_percent=rarity)
    return None


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

    async def scan_listings(self, limit: int = 50) -> list[NFTInfo]:
        """Вернуть активные лоты для авто-скана.

        Сейчас эндпоинт фида Tonel ещё не подтверждён, поэтому метод
        пробует GET по стабовому ``TONEL_LISTINGS_API`` с параметром ``limit``
        и логирует ошибки как warning — авто-сканер в этом случае просто
        ничего не отправляет пользователям.

        TODO: подключить реальный эндпоинт Tonel / Tonnel.Network или Getgems
        и разобрать формат ответа.
        """

        url = TONEL_LISTINGS_API
        try:
            resp = await self._client.get(url, params={"limit": limit})
        except httpx.HTTPError as exc:
            log.warning("scan_listings: сетевая ошибка %s", exc)
            return []

        if resp.status_code == 404:
            # Стаб эндпоинт ещё не реализован — это ожидаемо.
            log.info("scan_listings: эндпоинт фида пока не подключён (404)")
            return []
        if resp.status_code >= 400:
            log.warning("scan_listings: HTTP %s", resp.status_code)
            return []

        try:
            payload = resp.json()
        except ValueError:
            log.warning("scan_listings: вернулся не-JSON")
            return []

        # TODO: подогнать под реальную структуру.
        raw_items = _first_not_none(
            payload.get("items") if isinstance(payload, dict) else None,
            payload.get("results") if isinstance(payload, dict) else None,
            payload if isinstance(payload, list) else None,
            [],
        )

        result: list[NFTInfo] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            try:
                nft = self._parse_listing_item(raw)
            except (TonelError, KeyError, ValueError) as exc:
                log.debug("scan_listings: пропускаю спорный элемент: %s", exc)
                continue
            if nft is not None:
                result.append(nft)
        return result

    def _parse_listing_item(self, payload: dict) -> Optional[NFTInfo]:
        """Преобразовать входящий элемент фида в NFTInfo.

        Маркетплейсы по-разному называют поля — поддерживаем несколько
        синонимов через :func:`_first_not_none`. Атрибуты подарка (model,
        background, pattern) ожидаются как объекты ``{"name": str,
        "rarity_percent": float}`` или как чистая строка.
        """

        raw_id = _first_not_none(payload.get("id"), payload.get("token_id"))
        if raw_id is None:
            return None
        token_id = str(raw_id)
        if not TOKEN_ID_RE.match(token_id):
            return None

        price = _parse_float(
            _first_not_none(payload.get("price_ton"), payload.get("price"))
        )
        if price is None:
            return None

        raw_collection = _first_not_none(
            payload.get("collection"), payload.get("collection_name"), ""
        )
        raw_name = _first_not_none(payload.get("name"), "")
        raw_rank = _first_not_none(payload.get("rank"), payload.get("rarity_rank"))

        # Атрибуты Telegram-Gift'а.
        attrs = payload.get("attributes") if isinstance(payload.get("attributes"), dict) else {}
        model = _parse_attribute(
            _first_not_none(payload.get("model"), attrs.get("model") if attrs else None)
        )
        background = _parse_attribute(
            _first_not_none(
                payload.get("background"),
                payload.get("backdrop"),
                attrs.get("background") if attrs else None,
                attrs.get("backdrop") if attrs else None,
            )
        )
        pattern = _parse_attribute(
            _first_not_none(
                payload.get("pattern"),
                payload.get("symbol"),
                attrs.get("pattern") if attrs else None,
                attrs.get("symbol") if attrs else None,
            )
        )

        photo_url = _first_not_none(
            payload.get("photo_url"),
            payload.get("photo"),
            payload.get("image"),
            payload.get("image_url"),
            payload.get("preview"),
        )
        slug = _first_not_none(
            payload.get("slug"),
            payload.get("gift_slug"),
            payload.get("telegram_slug"),
        )
        portals_url_raw = _first_not_none(
            payload.get("portals_url"),
            payload.get("portals_link"),
        )
        portals_link = (
            str(portals_url_raw) if portals_url_raw else build_portals_link(token_id)
        )

        return NFTInfo(
            token_id=token_id,
            collection=str(raw_collection),
            name=str(raw_name),
            price_ton=price,
            rank=_parse_int(raw_rank),
            volume_24h_ton=_parse_float(payload.get("volume_24h_ton")),
            volume_7d_ton=_parse_float(payload.get("volume_7d_ton")),
            url=TONEL_ITEM_PAGE.format(token_id=token_id),
            model=model,
            background=background,
            pattern=pattern,
            photo_url=str(photo_url) if photo_url else None,
            telegram_link=build_telegram_gift_link(slug),
            portals_link=portals_link,
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

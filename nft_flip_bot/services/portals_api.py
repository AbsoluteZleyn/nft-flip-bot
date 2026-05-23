"""Клиент публичного API маркетплейса Portals.

Базовый URL — ``https://portal-market.com/api`` (домен в **единственном**
числе, без ``s`` в конце; ``portals-market.com`` за Cloudflare и из вне
не резолвится). Эндпоинты:

* ``GET /api/nfts/search?limit=N&status=listed&sort_by=listed_at%20desc``
    — лента активных листингов; **не требует авторизации**.
* ``GET /api/nfts/{uuid}`` — отдельный подарок по UUID; без авторизации.

Авторизованные эндпоинты (``Authorization: tma <tgWebAppData>``) — это
``/api/sales``, ``/api/nft-types`` и т.п. Для бота они недоступны, т.к.
требуют пользовательской Telegram-сессии (а не Bot API токена); в этом
модуле они не используются.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

import httpx

from ..models.nft import Attribute, NFTInfo
from .tonel_api import (
    PORTALS_MINIAPP_TEMPLATE,
    TELEGRAM_GIFT_TEMPLATE,
    TonelError,
    TonelNotFound,
)

log = logging.getLogger(__name__)

PORTALS_API_BASE = "https://portal-market.com/api"
PORTALS_SEARCH_PATH = "/nfts/search"
PORTALS_ITEM_PATH = "/nfts/{uuid}"

# Маппинг типа атрибута Portals → поле NFTInfo.
# Portals хранит «узор» подарка под именем ``symbol``, а «фон» под именем
# ``backdrop``; у нас в модели это ``pattern`` и ``background``.
_PORTALS_ATTR_TYPE_TO_FIELD = {
    "model": "model",
    "backdrop": "background",
    "symbol": "pattern",
}


def _to_float(value: Any) -> Optional[float]:
    """Распарсить число из любого допустимого представления Portals."""

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _rarity_per_mille_to_percent(rarity_per_mille: Any) -> Optional[float]:
    """Portals отдаёт редкость в промилле (``per_mille``).

    1 промилле = 0.1%. В нашей модели мы храним проценты (0..100).
    """

    value = _to_float(rarity_per_mille)
    if value is None:
        return None
    return value / 10.0


def _parse_attribute(raw: Any) -> Optional[Attribute]:
    """Привести один элемент Portals ``attributes`` к ``Attribute``."""

    if not isinstance(raw, dict):
        return None
    name = raw.get("value")
    if not name:
        return None
    rarity = _rarity_per_mille_to_percent(raw.get("rarity_per_mille"))
    if rarity is not None:
        rarity = max(0.0, min(100.0, rarity))
    return Attribute(name=str(name), rarity_percent=rarity)


def _parse_listing(item: dict[str, Any]) -> Optional[NFTInfo]:
    """Преобразовать один результат ``/nfts/search`` в ``NFTInfo``.

    Возвращает ``None``, если запись не имеет обязательных полей
    (UUID, цена) — такие лоты игнорируем.
    """

    uuid = item.get("id")
    price = _to_float(item.get("price"))
    if not uuid or price is None:
        return None

    name = item.get("name") or "—"
    tg_id = item.get("tg_id")
    photo_url = item.get("photo_url")

    fields: dict[str, Optional[Attribute]] = {
        "model": None,
        "background": None,
        "pattern": None,
    }
    for raw_attr in item.get("attributes") or []:
        if not isinstance(raw_attr, dict):
            continue
        field = _PORTALS_ATTR_TYPE_TO_FIELD.get(raw_attr.get("type"))
        if field is None:
            continue
        attribute = _parse_attribute(raw_attr)
        if attribute is not None:
            fields[field] = attribute

    telegram_link: Optional[str] = None
    if isinstance(tg_id, str) and "-" in tg_id:
        # tg_id вида "PrettyPosy-26429" — каноничный slug Telegram-NFT.
        telegram_link = TELEGRAM_GIFT_TEMPLATE.format(slug=tg_id)

    # В качестве идентификатора для глубокой ссылки в Mini App Portals
    # используем тот же tg_id (он же стабилен между листингами).
    deeplink_id = tg_id if isinstance(tg_id, str) and tg_id else str(uuid)
    portals_link = PORTALS_MINIAPP_TEMPLATE.format(token_id=deeplink_id)

    floor_price = _to_float(item.get("floor_price"))
    return NFTInfo(
        token_id=str(uuid),
        collection=name,
        name=name,
        price_ton=price,
        rank=None,
        volume_24h_ton=None,
        model=fields["model"],
        background=fields["background"],
        pattern=fields["pattern"],
        photo_url=str(photo_url) if photo_url else None,
        telegram_link=telegram_link,
        portals_link=portals_link,
        floor_price_ton=floor_price,
    )


class PortalsClient:
    """HTTP-клиент маркетплейса Portals (только публичные эндпоинты)."""

    def __init__(
        self,
        base_url: str = PORTALS_API_BASE,
        timeout: float = 15.0,
        init_data: Optional[str] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._init_data = init_data
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "PortalsClient":
        headers = {
            "Accept": "application/json",
            "User-Agent": (
                "nft-flip-bot/0.1 (+github.com/AbsoluteZleyn/nft-flip-bot)"
            ),
        }
        if self._init_data:
            headers["Authorization"] = f"tma {self._init_data}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers=headers,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _session(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "PortalsClient используется вне async-with-блока"
            )
        return self._client

    async def scan_listings(self, limit: int = 50) -> list[NFTInfo]:
        """Свежие активные листинги, отсортированные по дате выставления."""

        params = {
            "limit": str(max(1, min(200, int(limit)))),
            "offset": "0",
            "status": "listed",
            "sort_by": "listed_at desc",
        }
        try:
            response = await self._session.get(PORTALS_SEARCH_PATH, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TonelError(f"Portals scan_listings failed: {exc}") from exc

        payload = response.json()
        raw_results: Iterable[Any] = payload.get("results") or []

        listings: list[NFTInfo] = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            nft = _parse_listing(raw)
            if nft is not None:
                listings.append(nft)
        return listings

    async def list_combo_active(
        self,
        model_name: str,
        background_name: str,
        limit: int = 50,
    ) -> list[NFTInfo]:
        """Активные листинги с тем же ``model + background`` (комбо).

        Используется для команды ``/history``: показывает, сколько таких же
        подарков сейчас продаётся, минимальную/среднюю/максимальную цену.

        Параметры Portals: ``filter_by_models``/``filter_by_backdrops`` —
        принимают **имя** модели/фона (точно как в `attributes[].value`).
        """

        params = {
            "limit": str(max(1, min(200, int(limit)))),
            "offset": "0",
            "status": "listed",
            "sort_by": "price asc",
            "filter_by_models": model_name,
            "filter_by_backdrops": background_name,
        }
        try:
            response = await self._session.get(PORTALS_SEARCH_PATH, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TonelError(f"Portals list_combo_active failed: {exc}") from exc

        payload = response.json()
        raw_results: Iterable[Any] = payload.get("results") or []

        listings: list[NFTInfo] = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            nft = _parse_listing(raw)
            if nft is not None:
                listings.append(nft)
        return listings

    async def list_combo_sold(
        self,
        model_name: str,
        background_name: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Продано (sold) тем же комбо ``model + background``.

        Эндпоинт **требует** ``Authorization: tma <init_data>``. Если
        клиент создан без ``init_data``, метод выбросит ``TonelError``.

        Возвращает список словарей («сырой» JSON от Portals): у нас тут нет
        полной NFTInfo, потому что нам важны лишь цены и даты продажи для
        агрегации в ``/history``.

        Endpoint выбирается по очереди из списка кандидатов: точное имя
        ещё не подтверждено публично, и мы пробуем самые вероятные пути:

        * ``/sales`` (видели 401 «missing auth» — значит существует)
        * ``/trades`` (тоже 401)
        * ``/activity`` (тоже 401)

        Первый, который вернул 200, считается «нашим». Лог пишет, какой
        именно сработал, чтобы потом можно было захардкодить.
        """

        if not self._init_data:
            raise TonelError(
                "list_combo_sold: требуется init_data (Telethon-сессия)"
            )

        params = {
            "limit": str(max(1, min(200, int(limit)))),
            "offset": "0",
            "filter_by_models": model_name,
            "filter_by_backdrops": background_name,
        }

        candidates = [
            "/sales",
            "/trades",
            "/activity",
            "/nfts/sales",
        ]
        last_error: Optional[str] = None
        for path in candidates:
            try:
                response = await self._session.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = f"{path}: {exc}"
                continue
            if response.status_code == 200:
                log.info("Portals sold endpoint = %s", path)
                payload = response.json()
                if isinstance(payload, dict):
                    results = payload.get("results") or payload.get("data") or []
                    if isinstance(results, list):
                        return [r for r in results if isinstance(r, dict)]
                    return []
                if isinstance(payload, list):
                    return [r for r in payload if isinstance(r, dict)]
                return []
            last_error = (
                f"{path}: HTTP {response.status_code} {response.text[:200]}"
            )
            log.debug("Portals sold candidate %s failed: %s", path, last_error)

        raise TonelError(
            f"Portals list_combo_sold: ни один эндпоинт не сработал. "
            f"Последняя ошибка: {last_error}"
        )

    async def fetch_nft(self, uuid: str) -> NFTInfo:
        """Один лот по UUID Portals."""

        path = PORTALS_ITEM_PATH.format(uuid=uuid)
        try:
            response = await self._session.get(path)
        except httpx.HTTPError as exc:
            raise TonelError(f"Portals fetch_nft failed: {exc}") from exc

        if response.status_code == 404:
            raise TonelNotFound(f"Лот {uuid} не найден на Portals")
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TonelError(f"Portals fetch_nft failed: {exc}") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise TonelError("Portals fetch_nft: неожиданный формат ответа")
        nft = _parse_listing(payload)
        if nft is None:
            raise TonelError("Portals fetch_nft: ответ не содержит id/price")
        return nft

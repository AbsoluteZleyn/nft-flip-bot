"""Получает свежий ``init_data`` Mini App Portals и сохраняет в файл.

ЗАПУСКАЕТСЯ ЛОКАЛЬНО ПОЛЬЗОВАТЕЛЕМ. Не вызывается ботом.

Использование:

    python -m nft_flip_bot.scripts.refresh_init_data

Скрипт:
1. Подключается к Telegram через Telethon (если ещё не подключён — попросит
   код и 2FA-пароль в консоли).
2. Сохраняет сессию в ``data/portals_user.session`` (далее код подтверждения
   уже не понадобится).
3. Открывает WebView Mini App ``t.me/portals/market`` от твоего имени.
4. Достаёт из WebView-URL параметр ``tgWebAppData`` — это и есть ``init_data``.
5. Пишет его в ``data/portals_init_data.txt`` (одна строка).

Бот читает этот файл при запросах к приватным эндпоинтам Portals
(``Authorization: tma <init_data>``). ``init_data`` истекает через ~24 ч —
после этого запусти скрипт снова.

Переменные окружения:

* ``TELEGRAM_API_ID``  — обязательно (число; https://my.telegram.org/apps)
* ``TELEGRAM_API_HASH`` — обязательно (строка-хэш)
* ``PORTALS_BOT_USERNAME`` — по умолчанию ``portals``; имя бота, у которого
  открываем Mini App. Указано без ``@``.
* ``DATA_DIR`` — где хранить ``.session`` и init_data. По умолчанию ``data``
  рядом с CWD.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)


def _data_dir() -> Path:
    base = Path(os.environ.get("DATA_DIR", "data")).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"❌ Переменная окружения {name} не задана. "
            f"Сначала установи её, потом запусти скрипт."
        )
    return value


def _extract_init_data(url: str) -> Optional[str]:
    """Из URL вида ``https://...#tgWebAppData=...&...`` достать tgWebAppData.

    Telegram кладёт init_data в **fragment** (после ``#``), а не в query.
    """

    parsed = urlparse(url)
    # Сначала пробуем fragment (по спеке Telegram Mini App).
    for source in (parsed.fragment, parsed.query):
        if not source:
            continue
        params = parse_qs(source)
        value = params.get("tgWebAppData")
        if value:
            return value[0]
    return None


async def _fetch_init_data(api_id: int, api_hash: str, session_path: Path) -> str:
    # Импортируем Telethon лениво, чтобы основной бот не падал, если её нет.
    try:
        from telethon import TelegramClient, functions
        from telethon.tl.types import InputBotAppShortName, InputUser
    except ImportError as exc:  # noqa: BLE001
        raise SystemExit(
            "❌ Не установлен telethon. Поставь: "
            "pip install telethon==1.36.0"
        ) from exc

    bot_username = os.environ.get("PORTALS_BOT_USERNAME", "portals")

    async with TelegramClient(
        str(session_path),
        api_id,
        api_hash,
    ) as client:
        # При первом запуске Telethon сам спросит телефон / код / 2FA
        # в STDIN. Дальше .session уже хранит авторизацию.
        await client.start()

        log.info("Авторизация в Telegram OK. Получаю WebView Mini App…")

        # Резолвим бота portals → нужно для request_web_view.
        peer = await client.get_input_entity(bot_username)

        # Mini App у Portals открывается через t.me/portals/market —
        # это short_name="market". Сначала пробуем как Bot App
        # (request_app_web_view), если бот его не поддерживает —
        # фоллбэк на старый messages.RequestWebView.
        url: Optional[str] = None

        try:
            bot_app = InputBotAppShortName(bot_id=peer, short_name="market")
            result = await client(
                functions.messages.RequestAppWebViewRequest(
                    peer="me",
                    app=bot_app,
                    platform="android",
                    write_allowed=True,
                )
            )
            url = result.url
            log.info("RequestAppWebView OK")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "RequestAppWebView не сработал (%s), фоллбэк на RequestWebView",
                exc,
            )
            result = await client(
                functions.messages.RequestWebViewRequest(
                    peer="me",
                    bot=peer,
                    platform="android",
                    from_bot_menu=False,
                    url="https://t.me/portals/market",
                )
            )
            url = result.url

        if not url:
            raise SystemExit(
                "❌ Telegram не вернул URL WebView. "
                "Возможно, у бота @portals изменилось имя Mini App."
            )

    log.debug("WebView URL: %s", url)
    init_data = _extract_init_data(url)
    if not init_data:
        raise SystemExit(
            f"❌ В URL WebView нет параметра tgWebAppData. URL = {url[:200]}…"
        )
    return init_data


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    api_id_str = _require_env("TELEGRAM_API_ID")
    try:
        api_id = int(api_id_str)
    except ValueError:
        log.error("TELEGRAM_API_ID должен быть числом, а сейчас: %r", api_id_str)
        return 2
    api_hash = _require_env("TELEGRAM_API_HASH")

    data_dir = _data_dir()
    session_path = data_dir / "portals_user"
    out_path = data_dir / "portals_init_data.txt"

    log.info("Сессия Telethon: %s.session", session_path)
    log.info("init_data будет записан в: %s", out_path)

    init_data = asyncio.run(_fetch_init_data(api_id, api_hash, session_path))

    out_path.write_text(init_data, encoding="utf-8")
    log.info("✅ init_data сохранён (%d символов).", len(init_data))
    log.info(
        "Теперь можно запускать бота — он подхватит файл при /history."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

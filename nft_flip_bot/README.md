# nft_flip_bot

Telegram-бот для «флиппинга» Telegram-NFT-подарков. Основной целевой
маркетплейс — **Portals** (mini-app `t.me/portals/market`); для базовой
совместимости часть кода сохраняет старые имена `tonel_*`, но
выдаваемые ссылки и расчёт комиссий ориентированы на Portals.

## Возможности

- Анализ NFT по ссылке/ID токена с разбором атрибутов **модель / фон /
  узор** и их редкости в процентах.
- Расчёт ожидаемой resale-цены и потенциальной прибыли с учётом
  **двусторонних** комиссий маркета (`buy_fee` + `sell_fee`) и **двух**
  сетевых транзакций (`tx_fee * 2`).
- Бюджет в TON: `/budget <amount>` и `/budget show`.
- Портфель: `/list`, `/remove <token_id>`, `/flip <token_id>`.
- **Авто-скан** выгодных лотов: `/scan_on`, `/scan_off`, `/scan_now`.
  По умолчанию **раз в 1 минуту** бот запрашивает фид, фильтрует через
  `passes_filters` + `is_profitable` и пушит подписчикам **фото подарка
  с подписью**, в которой:
    - имя и коллекция,
    - модель / фон / узор с редкостью,
    - текущая цена, прогноз resale, прибыль (TON и %),
    - суммы комиссий buy/sell/tx,
    - ссылка `t.me/nft/<slug>` на подарок в Telegram,
    - ссылка `t.me/portals/market?startapp=gift_<id>` на покупку в Portals.
  Дедуп в таблице `notified`: один и тот же `token_id` отправится
  пользователю максимум один раз.
- **Персональный диапазон цен**: `/range <min> <max>` (TON) — перекрывает
  глобальные `MIN_PRICE_TON`/`MAX_PRICE_TON` для авто-скана и анализа.
  Сброс — `/range off`.
- **`/history <UUID>`** — статистика по комбо (модель + фон) лота:
  - **Сейчас на маркете**: сколько таких же активно (min/median/avg/max
    цены), сколько дешевле/дороже исходного лота, топ-3 самых дешёвых.
    Опирается на публичный Portals API
    (`filter_by_models` + `filter_by_backdrops`).
  - **Sold-история** — заглушка; требует Telethon-авторизации к
    приватному Portals-эндпоинту, будет в отдельном PR.
- Периодическое обновление цен в портфеле через `aiocron`.
- SQLite-хранилище через `aiosqlite`.
- SSRF-guard: для ручного анализа принимаются только HTTPS-ссылки с
  whitelisted-домена.

## Запуск локально

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
python -m nft_flip_bot.main
```

## Запуск в Docker

```bash
docker build -t nft-flip-bot .
docker run --rm -e BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN -v $(pwd)/data:/app/data nft-flip-bot
```

или через docker-compose:

```bash
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN docker compose up -d --build
```

## Переменные окружения

| Переменная          | По умолчанию           | Описание                                                       |
|---------------------|------------------------|----------------------------------------------------------------|
| `BOT_TOKEN`         | —                      | Токен Telegram-бота (обязателен)                               |
| `DB_PATH`           | `nft_flip_bot.sqlite3` | Путь к SQLite базе                                             |
| `MIN_PRICE_TON`     | `0`                    | Глобальный минимум цены NFT в TON                              |
| `MAX_PRICE_TON`     | `50`                   | Глобальный максимум цены NFT в TON                             |
| `MAX_RANK`          | `1000`                 | Максимальный ранк                                              |
| `MIN_VOLUME_TON`    | `0`                    | Минимальный 24h-объём в TON (Portals не отдаёт per-NFT volume) |
| `GROWTH_FACTOR`     | `0.5`                  | Множитель ожидаемого роста цены при перепродаже                |
| `TONEL_FEE`         | `0.05`                 | Комиссия маркета при **продаже** (доля от resale)              |
| `TONEL_BUY_FEE`     | =`TONEL_FEE`           | Комиссия маркета при **покупке** (доля от цены)                |
| `TX_FEE_TON`        | `0.05`                 | Сетевая комиссия за **одну** транзакцию (учитывается дважды)   |
| `SCAN_INTERVAL_MIN` | `1`                    | Как часто запускается авто-скан (мин, 1–59)                    |
| `SCAN_MAX_NOTIFY`   | `5`                    | Лимит пушей на пользователя за один прогон                     |
| `SCAN_LIMIT`        | `50`                   | Сколько лотов брать из фида за один запрос                     |
| `PORTALS_API_BASE`  | `https://portal-market.com/api` | URL Portals API для авто-скана                      |

## Структура

```
nft_flip_bot/
├─ main.py
├─ handlers/
│   ├─ start.py
│   ├─ budget.py
│   ├─ analyze.py
│   ├─ list.py
│   ├─ range_cmd.py      # /range — персональный диапазон цен
│   ├─ scan.py           # /scan_on /scan_off /scan_now
│   └─ utils.py
├─ services/
│   ├─ portals_api.py    # HTTP-клиент Portals (публичный API фида)
│   ├─ tonel_api.py      # legacy: ссылки + ручной анализ по ссылке
│   ├─ price_estimator.py
│   ├─ scanner.py        # авто-скан + пуш фото-уведомлений
│   └─ scheduler.py
├─ models/
│   ├─ db.py
│   └─ nft.py
├─ requirements.txt
├─ Dockerfile
└─ docker-compose.yml
```

## Источник данных

Авто-скан тянет фид из публичного API Portals:
`GET https://portal-market.com/api/nfts/search?limit=N&status=listed&sort_by=listed_at desc`.
Авторизация не требуется для чтения ленты. Endpoint можно
переопределить через переменную окружения `PORTALS_API_BASE`.

Поля payload'а: `id` (UUID — наш `token_id`), `tg_id` (slug
`PrettyPosy-26429` → `t.me/nft/<slug>`), `name`, `price` (TON),
`attributes[].type ∈ {model, backdrop, symbol}` с `rarity_per_mille`
(делим на 10 → проценты), `photo_url`, `floor_price`. Sales-history
эндпоинт у Portals требует пользовательскую Telegram-сессию (`tma`
init data), поэтому пока не используется — будет в отдельном PR через
Telethon-клиент.

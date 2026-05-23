# nft_flip_bot

Telegram-бот для «флиппинга» NFT-подарков через сервис **Tonel**.

## Возможности

- Анализ NFT по ссылке `https://tonel.io/item/...` или по ID токена.
- Расчёт ожидаемой resale-цены и потенциальной прибыли (с учётом комиссии Tonel и сетевых издержек).
- Бюджет в TON: команды `/budget <amount>` и `/budget show`.
- Список выбранных позиций: `/list`, `/remove <token_id>`, `/flip <token_id>`.
- **Авто-скан** выгодных лотов: `/scan_on`, `/scan_off`, `/scan_now`.
  Раз в N минут (по умолчанию 5) бот запрашивает фид Tonel,
  фильтрует через `passes_filters` + `is_profitable` и пушит подписчикам.
  Дедуп в таблице `notified` (один и тот же `token_id` придёт один раз на пользователя).
- Периодическое (раз в сутки) обновление цен через `aiocron`.
- SQLite-хранилище через `aiosqlite`.
- Защита от SSRF: принимаются только HTTPS-ссылки с домена `tonel.io`.

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

| Переменная       | По умолчанию | Описание                                       |
|------------------|--------------|------------------------------------------------|
| `BOT_TOKEN`      | —            | Токен Telegram-бота (обязателен)               |
| `DB_PATH`        | `nft_flip_bot.sqlite3` | Путь к SQLite базе                   |
| `MAX_PRICE_TON`  | `5`          | Фильтр: максимальная цена NFT в TON            |
| `MAX_RANK`       | `1000`       | Фильтр: максимальный ранк                      |
| `MIN_VOLUME_TON` | `1`          | Фильтр: минимальный 24h-объём в TON            |
| `GROWTH_FACTOR`  | `0.5`        | Множитель ожидаемого роста цены                |
| `TONEL_FEE`      | `0.02`       | Комиссия Tonel (доля)                          |
| `TX_FEE_TON`     | `0.05`       | Транзакционная комиссия сети (TON)             |
| `SCAN_INTERVAL_MIN` | `5`       | Как часто запускается авто-скан (мин, 1–59)         |
| `SCAN_MAX_NOTIFY`| `5`          | Лимит пушей на пользователя за один прогон            |
| `SCAN_LIMIT`     | `50`         | Сколько лотов брать из фида Tonel за один запрос          |

## Структура

```
nft_flip_bot/
├─ main.py
├─ handlers/
│   ├─ start.py
│   ├─ budget.py
│   ├─ analyze.py
│   ├─ list.py
│   └─ utils.py
├─ services/
│   ├─ tonel_api.py
│   ├─ price_estimator.py
│   ├─ scanner.py        # авто-скан + пуш подписчикам
│   └─ scheduler.py
├─ models/
│   ├─ db.py
│   └─ nft.py
├─ requirements.txt
├─ Dockerfile
└─ docker-compose.yml
```

> ⚠️ Публичный API сервиса Tonel в рамках задачи документирован не был.
> В `services/tonel_api.py` использованы условные endpoint'ы и реализован
> fallback-парсинг HTML страницы. Перед продакшен-запуском подставьте
> реальные пути API/селекторы — для удобства они помечены `# TODO`.

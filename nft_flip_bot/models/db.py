"""Слой работы с SQLite через aiosqlite."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from .nft import BudgetState, FlipItem, UserFilter

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budget (
    user_id     INTEGER PRIMARY KEY,
    total_ton   REAL NOT NULL DEFAULT 0,
    used_ton    REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS nfts (
    token_id            TEXT NOT NULL,
    user_id             INTEGER NOT NULL,
    collection          TEXT,
    current_price_ton   REAL NOT NULL,
    resale_price_ton    REAL NOT NULL,
    profit_ton          REAL NOT NULL,
    added_at            TEXT NOT NULL,
    PRIMARY KEY (token_id, user_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Подписка пользователя на авто-скан выгодных NFT.
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id     INTEGER PRIMARY KEY,
    enabled     INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Дедуп пушей: что мы уже кому слали.
CREATE TABLE IF NOT EXISTS notified (
    user_id      INTEGER NOT NULL,
    token_id     TEXT NOT NULL,
    notified_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, token_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Персональный диапазон цен для авто-скана и анализа (`/range <min> <max>`).
-- NULL → использовать глобальные MIN_PRICE_TON / MAX_PRICE_TON из env.
CREATE TABLE IF NOT EXISTS user_filters (
    user_id        INTEGER PRIMARY KEY,
    min_price_ton  REAL,
    max_price_ton  REAL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
"""


class Database:
    """Тонкая обёртка над aiosqlite с нужными нам операциями."""

    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        log.info("DB initialised at %s", self.path)

    # ---- users -----------------------------------------------------------

    async def upsert_user(self, user_id: int, username: Optional[str]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO users(user_id, username, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
                """,
                (user_id, username or "", datetime.now(timezone.utc).isoformat()),
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO budget(user_id, total_ton, used_ton)
                VALUES (?, 0, 0)
                """,
                (user_id,),
            )
            await db.commit()

    # ---- budget ----------------------------------------------------------

    async def set_budget(self, user_id: int, total_ton: float) -> BudgetState:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO budget(user_id, total_ton, used_ton)
                VALUES (?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET total_ton=excluded.total_ton
                """,
                (user_id, total_ton),
            )
            await db.commit()
        return await self.get_budget(user_id)

    async def get_budget(self, user_id: int) -> BudgetState:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT total_ton, used_ton FROM budget WHERE user_id=?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return BudgetState(user_id=user_id, total_ton=0.0, used_ton=0.0)
        return BudgetState(user_id=user_id, total_ton=row[0], used_ton=row[1])

    async def _recalc_used(self, db: aiosqlite.Connection, user_id: int) -> float:
        async with db.execute(
            "SELECT COALESCE(SUM(current_price_ton), 0) FROM nfts WHERE user_id=?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        used = float(row[0]) if row else 0.0
        await db.execute(
            "UPDATE budget SET used_ton=? WHERE user_id=?",
            (used, user_id),
        )
        return used

    # ---- nfts ------------------------------------------------------------

    async def add_nft(self, item: FlipItem) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO nfts(token_id, user_id, collection,
                                 current_price_ton, resale_price_ton,
                                 profit_ton, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_id, user_id) DO UPDATE SET
                    collection=excluded.collection,
                    current_price_ton=excluded.current_price_ton,
                    resale_price_ton=excluded.resale_price_ton,
                    profit_ton=excluded.profit_ton
                """,
                (
                    item.token_id,
                    item.user_id,
                    item.collection,
                    item.current_price_ton,
                    item.resale_price_ton,
                    item.profit_ton,
                    item.added_at.isoformat(),
                ),
            )
            await self._recalc_used(db, item.user_id)
            await db.commit()

    async def remove_nft(self, user_id: int, token_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM nfts WHERE user_id=? AND token_id=?",
                (user_id, token_id),
            )
            removed = cur.rowcount > 0
            await self._recalc_used(db, user_id)
            await db.commit()
        return removed

    async def list_nfts(self, user_id: int) -> list[FlipItem]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """
                SELECT token_id, user_id, collection, current_price_ton,
                       resale_price_ton, profit_ton, added_at
                FROM nfts WHERE user_id=? ORDER BY added_at DESC
                """,
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            FlipItem(
                token_id=r[0],
                user_id=r[1],
                collection=r[2] or "",
                current_price_ton=r[3],
                resale_price_ton=r[4],
                profit_ton=r[5],
                added_at=datetime.fromisoformat(r[6]),
            )
            for r in rows
        ]

    async def get_nft(self, user_id: int, token_id: str) -> Optional[FlipItem]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """
                SELECT token_id, user_id, collection, current_price_ton,
                       resale_price_ton, profit_ton, added_at
                FROM nfts WHERE user_id=? AND token_id=?
                """,
                (user_id, token_id),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return FlipItem(
            token_id=row[0],
            user_id=row[1],
            collection=row[2] or "",
            current_price_ton=row[3],
            resale_price_ton=row[4],
            profit_ton=row[5],
            added_at=datetime.fromisoformat(row[6]),
        )

    async def all_token_ids(self) -> list[tuple[int, str]]:
        """Список (user_id, token_id) для периодического обновления цен."""

        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT user_id, token_id FROM nfts"
            ) as cur:
                rows = await cur.fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    async def update_price(
        self,
        user_id: int,
        token_id: str,
        current_price_ton: float,
        resale_price_ton: float,
        profit_ton: float,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE nfts SET current_price_ton=?, resale_price_ton=?,
                                profit_ton=?
                WHERE user_id=? AND token_id=?
                """,
                (current_price_ton, resale_price_ton, profit_ton, user_id, token_id),
            )
            await self._recalc_used(db, user_id)
            await db.commit()

    # ---- subscriptions ---------------------------------------------------

    async def set_subscription(self, user_id: int, enabled: bool) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO subscriptions(user_id, enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (user_id, 1 if enabled else 0, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def get_subscription(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT enabled FROM subscriptions WHERE user_id=?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
        return bool(row[0]) if row else False

    async def list_subscribed_users(self) -> list[int]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT user_id FROM subscriptions WHERE enabled=1"
            ) as cur:
                rows = await cur.fetchall()
        return [int(r[0]) for r in rows]

    # ---- dedup -----------------------------------------------------------

    async def was_notified(self, user_id: int, token_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT 1 FROM notified WHERE user_id=? AND token_id=?",
                (user_id, token_id),
            ) as cur:
                row = await cur.fetchone()
        return row is not None

    async def mark_notified(self, user_id: int, token_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO notified(user_id, token_id, notified_at)
                VALUES (?, ?, ?)
                """,
                (user_id, token_id, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def clear_notifications(self, user_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM notified WHERE user_id=?",
                (user_id,),
            )
            removed = cur.rowcount
            await db.commit()
        return int(removed or 0)

    # ---- user filters ----------------------------------------------------

    async def set_user_filter(
        self,
        user_id: int,
        min_price_ton: Optional[float],
        max_price_ton: Optional[float],
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO user_filters(user_id, min_price_ton, max_price_ton,
                                         updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    min_price_ton=excluded.min_price_ton,
                    max_price_ton=excluded.max_price_ton,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    min_price_ton,
                    max_price_ton,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()

    async def get_user_filter(self, user_id: int) -> Optional[UserFilter]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT min_price_ton, max_price_ton FROM user_filters "
                "WHERE user_id=?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return UserFilter(
            user_id=user_id,
            min_price_ton=row[0],
            max_price_ton=row[1],
        )

    async def clear_user_filter(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM user_filters WHERE user_id=?", (user_id,)
            )
            removed = cur.rowcount > 0
            await db.commit()
        return removed

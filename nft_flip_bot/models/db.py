"""Слой работы с SQLite через aiosqlite."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import aiosqlite

from .nft import BudgetState, FlipItem

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
                (user_id, username or "", datetime.utcnow().isoformat()),
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

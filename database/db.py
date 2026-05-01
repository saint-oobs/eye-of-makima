import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

log = logging.getLogger("bot.database")

DB_PATH = os.getenv("DB_PATH", "data/bot.db")


class Database:
    """
    Async SQLite wrapper using aiosqlite.

    Features:
    - Single shared connection with WAL mode (concurrent reads + writes)
    - Per-operation asyncio.Lock for safe concurrent writes
    - Helper methods: execute, fetchone, fetchall, executemany
    - Schema auto-init on startup via db.init()
    """

    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ──────────────────────────────────────────────
    async def init(self) -> None:
        """
        Open the connection, apply PRAGMA settings, and
        run the schema migrations.
        """
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row  # dict-like row access

        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA synchronous = NORMAL")
        await self._conn.execute("PRAGMA temp_store = MEMORY")
        await self._conn.execute("PRAGMA mmap_size = 268435456")  # 256 MB
        await self._conn.commit()

        await self._apply_schema()
        log.info("Database ready: %s", self.path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            log.info("Database connection closed.")

    # ── Schema ─────────────────────────────────────────────────
    async def _apply_schema(self) -> None:
        schema_path = os.path.join(
            os.path.dirname(__file__), "schema.sql"
        )
        if not os.path.exists(schema_path):
            log.warning("schema.sql not found at %s — skipping", schema_path)
            return
        with open(schema_path, "r", encoding="utf-8") as fh:
            schema_sql = fh.read()
        async with self._lock:
            await self._conn.executescript(schema_sql)
            await self._conn.commit()
        log.debug("Schema applied.")

    # ── Core Helpers ───────────────────────────────────────────
    async def execute(
        self,
        sql: str,
        params: tuple | list = (),
        *,
        commit: bool = True,
    ) -> sqlite3.Cursor:
        """
        Execute a single DML/DDL statement.
        Returns the cursor (gives access to lastrowid, rowcount).
        """
        async with self._lock:
            cursor = await self._conn.execute(sql, params)
            if commit:
                await self._conn.commit()
            return cursor

    async def executemany(
        self,
        sql: str,
        params_seq: list[tuple],
        *,
        commit: bool = True,
    ) -> None:
        """
        Execute a statement with multiple parameter sets (bulk ops).
        """
        async with self._lock:
            await self._conn.executemany(sql, params_seq)
            if commit:
                await self._conn.commit()

    async def fetchone(
        self,
        sql: str,
        params: tuple | list = (),
    ) -> aiosqlite.Row | None:
        """
        Fetch a single row. Returns None if no match.
        """
        async with self._lock:
            cursor = await self._conn.execute(sql, params)
            return await cursor.fetchone()

    async def fetchall(
        self,
        sql: str,
        params: tuple | list = (),
    ) -> list[aiosqlite.Row]:
        """
        Fetch all matching rows. Returns an empty list if no match.
        """
        async with self._lock:
            cursor = await self._conn.execute(sql, params)
            return await cursor.fetchall()

    # ── Transaction Context Manager ───────────────────────────
    @asynccontextmanager
    async def transaction(self):
        """
        Wrap multiple statements in a single atomic transaction.

        Usage:
            async with db.transaction():
                await db.execute("INSERT ...", commit=False)
                await db.execute("UPDATE ...", commit=False)
            # Commits automatically on exit, rolls back on exception.
        """
        async with self._lock:
            try:
                yield self._conn
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

    # ── Convenience Wrappers ──────────────────────────────────
    async def get_value(
        self,
        sql: str,
        params: tuple | list = (),
        *,
        default: Any = None,
    ) -> Any:
        """
        Fetch a single column from the first matching row.
        Returns `default` if no row found.

        Example:
            count = await db.get_value(
                "SELECT COUNT(*) FROM mod_actions WHERE guild_id = ?",
                (guild_id,),
                default=0,
            )
        """
        row = await self.fetchone(sql, params)
        if row is None:
            return default
        return row[0]

    async def exists(self, sql: str, params: tuple | list = ()) -> bool:
        """
        Return True if at least one row matches the query.

        Example:
            exists = await db.exists(
                "SELECT 1 FROM premium_guilds WHERE guild_id = ?",
                (guild_id,),
            )
        """
        row = await self.fetchone(sql, params)
        return row is not None

    async def upsert(
        self,
        table: str,
        data: dict[str, Any],
        conflict_cols: list[str],
        *,
        commit: bool = True,
    ) -> None:
        """
        INSERT OR REPLACE helper for simple upserts.

        Example:
            await db.upsert(
                "premium_guilds",
                {"guild_id": guild_id, "expires_at": None},
                conflict_cols=["guild_id"],
            )
        """
        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        conflict = ", ".join(conflict_cols)
        update_set = ", ".join(
            f"{c} = excluded.{c}"
            for c in cols
            if c not in conflict_cols
        )
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {update_set}"
        )
        await self.execute(sql, list(data.values()), commit=commit)
"""Lightweight run registry so the UI can list runs without scanning checkpoints.

Backed by the same database as the checkpointer: SQLite for local development,
Postgres when DATABASE_URL is set.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import aiosqlite
    from psycopg_pool import AsyncConnectionPool

_CREATE_SQLITE = """
CREATE TABLE IF NOT EXISTS runs (
    issue_key TEXT PRIMARY KEY,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT 'ui',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_PG = """
CREATE TABLE IF NOT EXISTS runs (
    issue_key TEXT PRIMARY KEY,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT 'ui',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
"""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class RunRegistry(Protocol):
    async def setup(self) -> None: ...
    async def close(self) -> None: ...
    async def upsert(
        self, issue_key: str, *, summary: str | None = None, status: str | None = None, channel: str | None = None
    ) -> None: ...
    async def list(self, limit: int = 50) -> list[dict]: ...
    async def get(self, issue_key: str) -> dict | None: ...


class SqliteRunRegistry:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Run registry is not set up; call setup() first.")
        return self._conn

    async def setup(self) -> None:
        import aiosqlite

        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute(_CREATE_SQLITE)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def upsert(self, issue_key, *, summary=None, status=None, channel=None) -> None:
        now = _now().isoformat()
        await self._db.execute(
            """
            INSERT INTO runs (issue_key, summary, status, channel, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(issue_key) DO UPDATE SET
                summary = COALESCE(excluded.summary, runs.summary),
                status = COALESCE(?, runs.status),
                channel = COALESCE(?, runs.channel),
                updated_at = excluded.updated_at
            """,
            (issue_key, summary or "", status or "", channel or "ui", now, now, status, channel),
        )
        await self._db.commit()

    async def list(self, limit: int = 50) -> list[dict]:
        cur = await self._db.execute(
            "SELECT issue_key, summary, status, channel, created_at, updated_at FROM runs ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get(self, issue_key: str) -> dict | None:
        cur = await self._db.execute(
            "SELECT issue_key, summary, status, channel, created_at, updated_at FROM runs WHERE issue_key = ?",
            (issue_key,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


class PostgresRunRegistry:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: AsyncConnectionPool | None = None

    @property
    def _db(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("Run registry is not set up; call setup() first.")
        return self._pool

    async def setup(self) -> None:
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(self.dsn, min_size=1, max_size=4, open=False)
        await self._pool.open()
        async with self._pool.connection() as conn:
            await conn.execute(_CREATE_PG)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def upsert(self, issue_key, *, summary=None, status=None, channel=None) -> None:
        now = _now()
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO runs (issue_key, summary, status, channel, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (issue_key) DO UPDATE SET
                    summary = CASE WHEN EXCLUDED.summary = '' THEN runs.summary ELSE EXCLUDED.summary END,
                    status = COALESCE(%s, runs.status),
                    channel = COALESCE(%s, runs.channel),
                    updated_at = EXCLUDED.updated_at
                """,
                (issue_key, summary or "", status or "", channel or "ui", now, now, status, channel),
            )

    async def list(self, limit: int = 50) -> list[dict]:
        async with self._db.connection() as conn:
            cur = await conn.execute(
                "SELECT issue_key, summary, status, channel, created_at, updated_at FROM runs ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        cols = ["issue_key", "summary", "status", "channel", "created_at", "updated_at"]
        return [
            {c: (v.isoformat() if isinstance(v, dt.datetime) else v) for c, v in zip(cols, r, strict=True)}
            for r in rows
        ]

    async def get(self, issue_key: str) -> dict | None:
        async with self._db.connection() as conn:
            cur = await conn.execute(
                "SELECT issue_key, summary, status, channel, created_at, updated_at FROM runs WHERE issue_key = %s",
                (issue_key,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        cols = ["issue_key", "summary", "status", "channel", "created_at", "updated_at"]
        return {c: (v.isoformat() if isinstance(v, dt.datetime) else v) for c, v in zip(cols, row, strict=True)}


def make_registry(database_url: str, sqlite_path: str) -> RunRegistry:
    if database_url:
        return PostgresRunRegistry(database_url)
    return SqliteRunRegistry(sqlite_path)

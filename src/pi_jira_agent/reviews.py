"""Standalone reviews of a branch (R-58): where they are stored and how one runs.

A standalone review is not a run. It has no issue key, no gates and nothing to resume, so it
lives in its own small ``reviews`` table and runs as one background task: check the branch out
in a worktree, review merge-base to branch head in a read-only Pi session, store the findings,
remove the worktree. A review describes one fixed commit per repository; reviewing the branch
again creates a new entry.

The table sits in the same database as the run registry: SQLite locally, Postgres when
DATABASE_URL is set. Keep both backends in sync.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .graph import progress
from .graph.slots import PiSlots
from .jira_client import JiraClient
from .models import RepoConfig
from .pi_agent import PiAgentExecutor
from .workspace import RunWorkspaces

if TYPE_CHECKING:
    import aiosqlite
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_COLUMNS = (
    "id",
    "branch",
    "repos",
    "issue_key",
    "status",
    "started_by",
    "error",
    "commits",
    "result",
    "manifest",
    "created_at",
    "updated_at",
)
_JSON_COLUMNS = {"repos", "commits", "result", "manifest"}
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM reviews"

_CREATE_SQLITE = """
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    branch TEXT NOT NULL,
    repos TEXT NOT NULL DEFAULT '[]',
    issue_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    started_by TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    commits TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT 'null',
    manifest TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_PG = _CREATE_SQLITE.replace("created_at TEXT", "created_at TIMESTAMPTZ").replace(
    "updated_at TEXT", "updated_at TIMESTAMPTZ"
)

# A review that is in one of these when the service starts was cut short by a restart.
_UNFINISHED = ("queued", "running")

# What a branch name may look like before it goes anywhere near git.
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class ReviewConflict(Exception):
    """The review is still running, so it cannot be deleted (HTTP 409)."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _row(values) -> dict:
    row = {}
    for column, value in zip(_COLUMNS, values, strict=True):
        if column in _JSON_COLUMNS:
            value = json.loads(value) if value else None
        elif isinstance(value, dt.datetime):
            value = value.isoformat()
        row[column] = value
    return row


def _encode(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: (json.dumps(v) if k in _JSON_COLUMNS else v) for k, v in fields.items()}


class ReviewStore(Protocol):
    async def setup(self) -> None: ...
    async def close(self) -> None: ...
    async def create(self, review_id: str, **fields: Any) -> None: ...
    async def update(self, review_id: str, **fields: Any) -> None: ...
    async def get(self, review_id: str) -> dict | None: ...
    async def list(self, limit: int = 50) -> list[dict]: ...
    async def delete(self, review_id: str) -> bool: ...
    async def mark_interrupted(self) -> int: ...


class SqliteReviewStore:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Review store is not set up; call setup() first.")
        return self._conn

    async def setup(self) -> None:
        import aiosqlite

        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute(_CREATE_SQLITE)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def create(self, review_id: str, **fields: Any) -> None:
        now = _now().isoformat()
        values = {"id": review_id, **_encode(fields), "created_at": now, "updated_at": now}
        await self._db.execute(
            f"INSERT INTO reviews ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )
        await self._db.commit()

    async def update(self, review_id: str, **fields: Any) -> None:
        values = {**_encode(fields), "updated_at": _now().isoformat()}
        await self._db.execute(
            f"UPDATE reviews SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
            (*values.values(), review_id),
        )
        await self._db.commit()

    async def get(self, review_id: str) -> dict | None:
        cur = await self._db.execute(f"{_SELECT} WHERE id = ?", (review_id,))
        row = await cur.fetchone()
        return _row(row) if row else None

    async def list(self, limit: int = 50) -> list[dict]:
        cur = await self._db.execute(f"{_SELECT} ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_row(r) for r in await cur.fetchall()]

    async def delete(self, review_id: str) -> bool:
        cur = await self._db.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
        await self._db.commit()
        return cur.rowcount > 0

    async def mark_interrupted(self) -> int:
        cur = await self._db.execute(
            "UPDATE reviews SET status = 'interrupted', updated_at = ? WHERE status IN (?, ?)",
            (_now().isoformat(), *_UNFINISHED),
        )
        await self._db.commit()
        return cur.rowcount


class PostgresReviewStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: AsyncConnectionPool | None = None

    @property
    def _db(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("Review store is not set up; call setup() first.")
        return self._pool

    async def setup(self) -> None:
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(self.dsn, min_size=1, max_size=2, open=False)
        await self._pool.open()
        async with self._pool.connection() as conn:
            await conn.execute(_CREATE_PG)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def create(self, review_id: str, **fields: Any) -> None:
        now = _now()
        values = {"id": review_id, **_encode(fields), "created_at": now, "updated_at": now}
        async with self._db.connection() as conn:
            await conn.execute(
                f"INSERT INTO reviews ({', '.join(values)}) VALUES ({', '.join('%s' for _ in values)})",  # pyright: ignore[reportArgumentType]
                tuple(values.values()),
            )

    async def update(self, review_id: str, **fields: Any) -> None:
        values = {**_encode(fields), "updated_at": _now()}
        async with self._db.connection() as conn:
            await conn.execute(
                f"UPDATE reviews SET {', '.join(f'{k} = %s' for k in values)} WHERE id = %s",  # pyright: ignore[reportArgumentType]
                (*values.values(), review_id),
            )

    async def get(self, review_id: str) -> dict | None:
        async with self._db.connection() as conn:
            cur = await conn.execute(f"{_SELECT} WHERE id = %s", (review_id,))  # pyright: ignore[reportArgumentType]
            row = await cur.fetchone()
        return _row(row) if row else None

    async def list(self, limit: int = 50) -> list[dict]:
        async with self._db.connection() as conn:
            cur = await conn.execute(f"{_SELECT} ORDER BY created_at DESC LIMIT %s", (limit,))  # pyright: ignore[reportArgumentType]
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def delete(self, review_id: str) -> bool:
        async with self._db.connection() as conn:
            cur = await conn.execute("DELETE FROM reviews WHERE id = %s", (review_id,))
            return cur.rowcount > 0

    async def mark_interrupted(self) -> int:
        async with self._db.connection() as conn:
            cur = await conn.execute(
                "UPDATE reviews SET status = 'interrupted', updated_at = %s WHERE status IN (%s, %s)",
                (_now(), *_UNFINISHED),
            )
            return cur.rowcount


def make_review_store(database_url: str, sqlite_path: str) -> ReviewStore:
    if database_url:
        return PostgresReviewStore(database_url)
    return SqliteReviewStore(sqlite_path)


class ReviewService:
    """Starts, runs and lists standalone reviews."""

    def __init__(
        self,
        *,
        store: ReviewStore,
        workspaces: RunWorkspaces,
        slots: PiSlots,
        reviewer: PiAgentExecutor,
        jira: JiraClient,
        rules: str = "",
    ):
        self.store = store
        self.workspaces = workspaces
        self.slots = slots
        self.reviewer = reviewer
        self.jira = jira
        self.rules = rules
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        await self.store.setup()
        cut_short = await self.store.mark_interrupted()
        if cut_short:
            logger.warning("%d standalone review(s) were cut short by a restart; run them again.", cut_short)

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.store.close()

    async def start_review(
        self,
        *,
        repos: list[RepoConfig],
        branch: str,
        issue_key: str = "",
        started_by: str = "ui",
        manifest: dict | None = None,
    ) -> dict:
        """Queue a review of ``branch`` in ``repos``. Refused before anything is checked out when
        the branch name is not one, or the branch is missing in any of the repositories."""
        branch = branch.strip()
        if not _BRANCH.match(branch) or ".." in branch or branch.endswith("/"):
            raise ValueError(f"{branch!r} is not a branch name.")
        if not repos:
            raise ValueError("Choose at least one repository to review.")
        missing = await asyncio.to_thread(lambda: [r.name for r in repos if not self.workspaces.branch_head(r, branch)])
        if missing:
            raise ValueError(
                f"Branch {branch!r} does not exist in {', '.join(missing)}. "
                "A review covers the same branch in every chosen repository."
            )
        review_id = f"review-{uuid.uuid4().hex[:8]}"
        issue_key = issue_key.strip().upper()
        await self.store.create(
            review_id,
            branch=branch,
            repos=[r.name for r in repos],
            issue_key=issue_key,
            status="queued",
            started_by=started_by,
            manifest=manifest or {},
        )
        progress.reset_activity(review_id)
        self._tasks[review_id] = asyncio.create_task(self._run(review_id, repos, branch, issue_key))
        return await self.get(review_id) or {}

    async def _run(self, review_id: str, repos: list[RepoConfig], branch: str, issue_key: str) -> None:
        try:
            await self.store.update(review_id, status="running")
            progress.mark(review_id, "pr_review", label="Checking out the branch")
            # git is a blocking subprocess; keep it off the event loop.
            commits = await asyncio.to_thread(
                lambda: {r.name: self.workspaces.checkout_branch(review_id, r, branch) for r in repos}
            )
            await self.store.update(review_id, commits=commits)
            issue = await self.jira.get_issue(issue_key) if issue_key else None
            roots = [
                {"name": r.name, "path": str(self.workspaces.path(review_id, r.name)), "properties": r.properties}
                for r in repos
            ]
            async with self.slots.hold(review_id, "pr_review"):
                result = await self.reviewer.run_review(
                    review_id,
                    issue=issue,
                    repo_cwd=roots[0]["path"],
                    repo_roots=roots,
                    diff={name: {"base": c["base"], "tree": c["head"]} for name, c in commits.items()},
                    rules=self.rules,
                )
            await self.store.update(review_id, status="done", result=result.model_dump())
            logger.info("Standalone review %s of %s: %d finding(s)", review_id, branch, len(result.findings))
        except asyncio.CancelledError:
            # Shutdown: leave the row as it is; the next start marks it interrupted.
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Standalone review %s failed", review_id)
            await self.store.update(review_id, status="failed", error=str(exc)[:2000])
        finally:
            # The findings and commit SHAs are kept; the checkout is not.
            await asyncio.to_thread(self.workspaces.remove, review_id)
            self._tasks.pop(review_id, None)
            progress.clear(review_id)

    async def get(self, review_id: str) -> dict | None:
        row = await self.store.get(review_id)
        if not row:
            return None
        live = progress.get(review_id)
        return {
            **row,
            "running": review_id in self._tasks,
            "node_label": live["label"] if live else None,
            "activity": progress.events(review_id, limit=150),
        }

    async def list(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list(limit)
        for row in rows:
            row["running"] = row["id"] in self._tasks
            # The list shows counts; the findings themselves come with the single review.
            result = row.pop("result", None) or {}
            row["findings"] = len(result.get("findings") or [])
            row["must"] = sum(1 for f in result.get("findings") or [] if f.get("severity") == "must")
        return rows

    async def delete(self, review_id: str) -> bool:
        if review_id in self._tasks:
            raise ReviewConflict(f"{review_id} is still running; wait for it to finish before deleting it.")
        progress.reset_activity(review_id)
        return await self.store.delete(review_id)

"""Job queue that decouples inbound triggers (webhooks, UI) from graph execution.

Two backends share one interface:
- InMemoryJobQueue: asyncio.Queue, single process, lost on restart (development).
- RedisJobQueue: Redis list, survives restarts and lets several workers share load.

Jobs are small dicts; the handler decides what to do with them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

JobHandler = Callable[[dict], Awaitable[None]]


class JobQueue(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def enqueue(self, job: dict) -> None: ...
    def size(self) -> int: ...


class InMemoryJobQueue:
    def __init__(self, max_size: int, handler: JobHandler):
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_size)
        self.handler = handler
        self._worker_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop(), name="job-worker")
        logger.info("In-memory job queue started.")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker_task:
            await self._worker_task
            logger.info("In-memory job queue stopped.")

    async def enqueue(self, job: dict) -> None:
        await self.queue.put(job)
        logger.info("Job enqueued. current_size=%s", self.queue.qsize())

    def size(self) -> int:
        return self.queue.qsize()

    async def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                await self.handler(job)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Job processing failed: %s", exc)
            finally:
                self.queue.task_done()


class RedisJobQueue:
    LIST_KEY = "pi-jira-agent:jobs"

    def __init__(self, redis_url: str, handler: JobHandler):
        self.redis_url = redis_url
        self.handler = handler
        self._redis: Any = None  # redis.asyncio.Redis once started
        self._worker_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._last_size = 0

    @property
    def _client(self) -> Any:
        if self._redis is None:
            raise RuntimeError("Redis job queue is not started; call start() first.")
        return self._redis

    async def start(self) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
        await self._redis.ping()
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop(), name="redis-job-worker")
        logger.info("Redis job queue started (%s).", self.redis_url)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker_task:
            await self._worker_task
        if self._redis:
            await self._redis.aclose()
        logger.info("Redis job queue stopped.")

    async def enqueue(self, job: dict) -> None:
        self._last_size = await self._client.lpush(self.LIST_KEY, json.dumps(job))
        logger.info("Job enqueued to Redis. current_size=%s", self._last_size)

    def size(self) -> int:
        return self._last_size

    async def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            item = await self._client.brpop(self.LIST_KEY, timeout=1)
            if not item:
                continue
            _, raw = item
            try:
                await self.handler(json.loads(raw))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Job processing failed: %s", exc)


def make_job_queue(redis_url: str, max_size: int, handler: JobHandler) -> JobQueue:
    if redis_url:
        return RedisJobQueue(redis_url, handler)
    return InMemoryJobQueue(max_size, handler)


# Backwards-compatible alias for older imports.
AsyncEventQueue = InMemoryJobQueue

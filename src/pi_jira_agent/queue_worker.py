import asyncio
import logging
from collections.abc import Awaitable, Callable


logger = logging.getLogger(__name__)

JobHandler = Callable[[dict], Awaitable[None]]


class AsyncEventQueue:
    def __init__(self, max_size: int, handler: JobHandler):
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_size)
        self.handler = handler
        self._worker_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop(), name="jira-event-worker")
        logger.info("Queue worker started.")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker_task:
            await self._worker_task
            logger.info("Queue worker stopped.")

    async def enqueue(self, event: dict) -> None:
        await self.queue.put(event)
        logger.info("Event enqueued. current_size=%s", self.queue.qsize())

    async def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            try:
                await self.handler(event)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Event processing failed: %s", exc)
            finally:
                self.queue.task_done()

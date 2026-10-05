"""Caps how many Pi sessions run at once.

Worktrees let any number of runs work side by side, but each Pi session costs tokens and
CPU. A run waiting at a gate holds no slot; a run that wants to plan or code while every
slot is taken waits, and says so in its progress label.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from . import progress

WAITING_LABEL = "Waiting for a free agent slot"


class PiSlots:
    def __init__(self, limit: int):
        self._semaphore = asyncio.Semaphore(max(1, limit))

    @asynccontextmanager
    async def hold(self, issue_key: str, node: str) -> AsyncIterator[None]:
        waited = self._semaphore.locked()
        if waited:
            progress.mark(issue_key, node, label=WAITING_LABEL)
        async with self._semaphore:
            if waited:
                progress.mark(issue_key, node)  # restart the node's clock now that it really runs
            yield

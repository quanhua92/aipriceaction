"""Share concurrent identical read work without caching completed results."""

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from typing import TypeVar

T = TypeVar("T")


class AsyncSingleFlight:
    """Run at most one active operation for each key in an event loop."""

    def __init__(self):
        self._tasks: dict[Hashable, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def run(self, key: Hashable, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.create_task(operation())
                self._tasks[key] = task
                task.add_done_callback(lambda done: self._discard(key, done))
        # A disconnecting waiter must not cancel work shared by other callers.
        return await asyncio.shield(task)

    def _discard(self, key: Hashable, task: asyncio.Task) -> None:
        if self._tasks.get(key) is task:
            del self._tasks[key]
        # Retrieve a possible exception when every waiting request disconnected.
        if not task.cancelled():
            task.exception()

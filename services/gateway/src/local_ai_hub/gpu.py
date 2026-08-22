from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .observability import GPU_IN_FLIGHT, GPU_WAITERS


class GpuGate:
    """Serializes model transitions and inference on constrained single-GPU hosts."""

    def __init__(self, capacity: int = 1):
        self._semaphore = asyncio.Semaphore(capacity)
        self._transition_lock = asyncio.Lock()

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        GPU_WAITERS.inc()
        try:
            await self._semaphore.acquire()
        finally:
            GPU_WAITERS.dec()
        GPU_IN_FLIGHT.inc()
        try:
            yield
        finally:
            GPU_IN_FLIGHT.dec()
            self._semaphore.release()

    @property
    def transition_lock(self) -> asyncio.Lock:
        return self._transition_lock

"""A local model (fastembed ONNX) loaded once, on first use, off the event loop."""

import asyncio
from collections.abc import Callable
from typing import Any


class LazyModel:
    """Loads `_load()` in a thread as one shared task: a caller's timeout cancels only its own
    wait, never the load (a cold load longer than a request's budget would otherwise restart on
    every request and never finish). A failed load is retried by the next caller. `concurrency`
    caps calls running at once (an ONNX model already uses every core for one call)."""

    def __init__(self, *, concurrency: int | None = None) -> None:
        self._impl: Any = None
        self._loading: asyncio.Future[Any] | None = None
        self._slots = asyncio.Semaphore(concurrency) if concurrency else None

    def _load(self) -> Any:
        raise NotImplementedError

    async def _model(self) -> Any:
        if self._impl is None:
            if self._loading is None:
                self._loading = asyncio.ensure_future(asyncio.to_thread(self._load))
            loading = self._loading
            try:
                self._impl = await asyncio.shield(loading)
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._loading is loading:
                    self._loading = None  # let the next caller try again
                raise
        return self._impl

    async def _run[T](self, call: Callable[..., T], *args: Any) -> T:
        model = await self._model()
        if self._slots is None:
            return await asyncio.to_thread(call, model, *args)
        async with self._slots:
            return await asyncio.to_thread(call, model, *args)

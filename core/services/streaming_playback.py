"""Pure streaming text and ordered output helpers.

These helpers do not know about the speaker or a TTS provider. Callers supply
the consumer, which makes cancellation and silent acceptance deterministic.
"""

from __future__ import annotations

import asyncio
import inspect


class SentenceAggregator:
    """Turn text deltas into non-empty, ordered sentence chunks."""

    _BOUNDARIES = frozenset("。！？!?；;\n")

    def __init__(self):
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        if not isinstance(text, str) or not text:
            return []
        self._buffer += text
        return self._drain(False)

    def finish(self) -> list[str]:
        return self._drain(True)

    def _drain(self, flush_tail: bool) -> list[str]:
        emitted: list[str] = []
        boundary_at: int | None = None
        for index, char in enumerate(self._buffer):
            if char in self._BOUNDARIES:
                boundary_at = index + 1
            elif char == "." and not self._decimal_point(index):
                boundary_at = index + 1
            if boundary_at is not None:
                chunk = self._buffer[:boundary_at].strip()
                self._buffer = self._buffer[boundary_at:]
                if chunk:
                    emitted.append(chunk)
                return emitted + self._drain(flush_tail)
        if flush_tail:
            chunk = self._buffer.strip()
            self._buffer = ""
            if chunk:
                emitted.append(chunk)
        return emitted

    def _decimal_point(self, index: int) -> bool:
        if index == 0 or index + 1 >= len(self._buffer):
            return False
        return self._buffer[index - 1].isdigit() and self._buffer[index + 1].isdigit()


class OrderedPlaybackQueue:
    """Consume queued sentences in order and discard them on cancellation."""

    _SENTINEL = object()

    def __init__(self, consumer):
        self._consumer = consumer
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._closed = asyncio.Event()
        self._cancelled = False

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def put(self, sentence: str):
        if self._cancelled:
            raise asyncio.CancelledError()
        if not isinstance(sentence, str) or not sentence.strip():
            return
        await self._queue.put(sentence)

    async def finish(self):
        if self._cancelled:
            await self.wait_closed()
            return
        await self._queue.join()
        await self._queue.put(self._SENTINEL)
        await self.wait_closed()

    async def cancel(self):
        if self._cancelled:
            await self.wait_closed()
            return
        self._cancelled = True
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()
                if item is self._SENTINEL:
                    break
        if self._task is not None and not self._task.done():
            self._task.cancel()
        await self.wait_closed()

    async def wait_closed(self):
        if self._task is None:
            self._closed.set()
            return
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._closed.set()

    async def _run(self):
        try:
            while True:
                item = await self._queue.get()
                try:
                    if item is self._SENTINEL:
                        return
                    result = self._consumer(item)
                    if inspect.isawaitable(result):
                        await result
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise

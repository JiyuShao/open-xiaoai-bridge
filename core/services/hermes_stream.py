"""Hermes OpenAI-compatible SSE events and a transport-only parser.

The parser deliberately knows nothing about TTS, devices or Home Assistant. It
turns wire frames into small immutable events so the conversation layer can
apply its own ordering and cancellation policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import codecs
import json
from typing import Any

try:  # Parser-only and silent tests do not need the optional HTTP runtime.
    import aiohttp
except ImportError:  # pragma: no cover - exercised only in dependency-light hosts
    aiohttp = None


@dataclass(frozen=True)
class TextDelta:
    text: str
    # Optional voice metadata may arrive with an incremental response.
    voice: Any = None


@dataclass(frozen=True)
class ToolProgress:
    payload: Any


@dataclass(frozen=True)
class TurnFinished:
    reason: str = "done"


@dataclass(frozen=True)
class StreamError:
    code: str
    detail: str | None = None


StreamEvent = TextDelta | ToolProgress | TurnFinished | StreamError


class HermesStreamFallback(RuntimeError):
    """The endpoint answered successfully but did not provide an SSE stream."""

    def __init__(self, body: Any = None, *, status: int = 200, reason: str = "not_event_stream"):
        super().__init__(reason)
        self.body = body
        self.status = status
        self.reason = reason


class HermesHTTPError(RuntimeError):
    """A non-successful Hermes response without exposing its body or headers."""

    def __init__(self, status: int):
        super().__init__(f"Hermes HTTP {status}")
        self.status = status


class HermesSSEParser:
    """Parse SSE response chunks into Hermes stream events.

    ``feed`` accepts either text or bytes and may receive arbitrary chunk
    boundaries. ``finish`` is called when the HTTP body ends; a body without a
    terminal event becomes one ``StreamError`` so callers can choose fallback.
    """

    _FINISH_EVENTS = frozenset({"finish", "done", "response.completed", "turn.finished"})

    def __init__(self):
        self._buffer = ""
        self._event_name = ""
        self._data: list[str] = []
        self._terminal = False
        self._decoder = codecs.getincrementaldecoder("utf-8")()

    def feed(self, chunk: bytes | str) -> list[StreamEvent]:
        if self._terminal:
            return []
        try:
            text = chunk if isinstance(chunk, str) else self._decoder.decode(chunk)
        except UnicodeDecodeError:
            return [self._error("invalid_utf8")]
        if not isinstance(text, str):
            return [self._error("invalid_chunk")]
        self._buffer += text
        events: list[StreamEvent] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            events.extend(self._line(line.rstrip("\r")))
        return events

    def finish(self) -> list[StreamEvent]:
        if self._terminal:
            return []
        self._buffer = ""
        self._event_name = ""
        self._data.clear()
        return [self._error("stream_ended_without_finish")]

    def _line(self, line: str) -> list[StreamEvent]:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return []
        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""
        if field == "event":
            self._event_name = value
        elif field == "data":
            self._data.append(value)
        return []

    def _dispatch(self) -> list[StreamEvent]:
        event_name, data = self._event_name, "\n".join(self._data)
        self._event_name = ""
        self._data.clear()
        if not data or self._terminal:
            return []
        if data == "[DONE]":
            return [self._finish("done")]
        if event_name in self._FINISH_EVENTS:
            reason = event_name
            try:
                payload = json.loads(data)
                if isinstance(payload, dict):
                    reason = str(payload.get("reason") or payload.get("finish_reason") or reason)
            except (TypeError, ValueError):
                # A finish event may carry a plain text reason; it is already
                # an explicit terminal signal and need not fail the turn.
                if data.strip():
                    reason = data.strip()[:80]
            return [self._finish(reason)]
        if event_name == "hermes.tool.progress":
            try:
                payload = json.loads(data)
            except (TypeError, ValueError):
                return [self._error("invalid_progress_json")]
            return [ToolProgress(payload)]
        try:
            payload = json.loads(data)
        except (TypeError, ValueError):
            return [self._error("invalid_json")]
        if isinstance(payload, dict) and payload.get("error") is not None:
            return [self._error("hermes_error")]
        return self._from_completion(payload)

    def _from_completion(self, payload: Any) -> list[StreamEvent]:
        if not isinstance(payload, dict):
            return [self._error("invalid_completion")]
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return []
        choice = choices[0]
        delta = choice.get("delta")
        text = self._delta_text(delta)
        voice = None
        if isinstance(delta, dict):
            metadata = delta.get("metadata")
            if isinstance(delta.get("voice"), dict):
                voice = delta["voice"]
            elif isinstance(metadata, dict) and isinstance(metadata.get("voice"), dict):
                voice = metadata["voice"]
        events: list[StreamEvent] = [TextDelta(text, voice)] if text else []
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            events.append(self._finish(str(finish_reason)))
        return events

    @staticmethod
    def _delta_text(delta: Any) -> str:
        if not isinstance(delta, dict):
            return ""
        content = delta.get("content", delta.get("text", ""))
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type", "text") == "text" and isinstance(item.get("text", ""), str)
            )
        return ""

    def _finish(self, reason: str) -> TurnFinished:
        self._terminal = True
        return TurnFinished(reason or "done")

    def _error(self, code: str) -> StreamError:
        return StreamError(code)


class HermesStreamTransport:
    """Reusable HTTP transport for a single Hermes endpoint.

    The injected session seam is used by silent tests. In production the
    transport owns one ``aiohttp.ClientSession`` until ``close`` is called.
    """

    def __init__(self, base_url: str, *, timeout: float = 120, session=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session
        self._owns_session = session is None
        self._streaming_supported: bool | None = None
        self._last_headers: dict[str, str] = {}

    @property
    def session(self):
        return self._session

    @property
    def streaming_supported(self) -> bool | None:
        return self._streaming_supported

    @property
    def last_headers(self) -> dict[str, str]:
        """Headers from the latest response, excluding cookie material."""
        return dict(self._last_headers)

    async def _get_session(self):
        if self._session is None:
            if aiohttp is None:
                raise RuntimeError("aiohttp is required for Hermes streaming transport")
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                connector=aiohttp.TCPConnector(limit=4, enable_cleanup_closed=True),
            )
        return self._session

    async def stream(self, payload: dict[str, Any], headers: dict[str, str]):
        """Yield normalized events, or raise ``HermesStreamFallback`` once."""
        if self._streaming_supported is False:
            raise HermesStreamFallback(None, reason="capability_cached")
        session = await self._get_session()
        async with session.post(
            self._chat_completions_url(),
            json=payload,
            headers=headers,
            allow_redirects=False,
        ) as response:
            self._last_headers = {
                str(key): str(value)
                for key, value in getattr(response, "headers", {}).items()
                if str(key).lower() != "set-cookie"
            }
            if response.status >= 400:
                raise HermesHTTPError(response.status)
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if "text/event-stream" not in content_type:
                body = await response.json(content_type=None)
                self._streaming_supported = False
                raise HermesStreamFallback(body, status=response.status)
            self._streaming_supported = True
            parser = HermesSSEParser()
            async for chunk in response.content.iter_chunked(16 * 1024):
                for event in parser.feed(chunk):
                    yield event
                    if isinstance(event, TurnFinished):
                        return
            for event in parser.finish():
                yield event

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    async def close(self):
        if self._owns_session and self._session is not None:
            close = getattr(self._session, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            self._session = None

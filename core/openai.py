"""OpenAI-compatible service manager.

This module talks to any OpenAI-compatible Chat Completions endpoint,
including Hermes Agent API server mode.
"""

import asyncio
import inspect
import json
import time
import uuid
from typing import Any

import aiohttp

from core.services.chat_sessions import ChatSessions
from core.services.conversation_tools import ConversationTools
from core.services.tts.voice import VoiceDirective
from core.services.hermes_stream import (
    HermesStreamFallback,
    StreamError,
    TextDelta,
    ToolProgress,
    TurnFinished,
    HermesStreamTransport,
)
from core.services.streaming_playback import SentenceAggregator
from core.utils.base import get_env
from core.utils.config import ConfigManager
from core.utils.logger import logger


class OpenAIManager:
    """Manager for OpenAI-compatible chat backends."""

    XIAOAI_TTS_SPEAKER = "xiaoai"

    _initialized = False
    _reload_listener_registered = False
    _enabled = False
    _base_url = "http://127.0.0.1:8000/v1"
    _api_key = ""
    _model = "gpt-4o-mini"
    _session_key = "agent:default:open-xiaoai-bridge"
    # Optional header used to send session_key to the server (e.g. Hermes'
    # "X-Hermes-Session-Key" for long-term memory scoping). Standard OpenAI /
    # Ollama / LM Studio ignore the unknown header; set empty to disable.
    _session_header = "X-Hermes-Session-Key"
    _system_prompt = ""
    _temperature: float | None = None
    _max_tokens: int | None = None
    _timeout = 120
    # Chat streaming is opt-in so existing OpenAI-compatible deployments keep
    # their complete-response behavior until explicitly enabled.
    _chat_streaming_enabled = False
    _hermes_stream_transport: HermesStreamTransport | None = None
    _stream_tasks: dict[str, asyncio.Task] = {}
    _stream_generation = 0  # retained for diagnostics/backward compatibility
    _stream_active: dict[str, bool] = {}
    _last_stream_timings: dict[str, float] = {}
    _history_max_messages = 20
    _extra_body: dict[str, Any] = {}
    _protocol = "openai"
    _tts_provider: str | None = None
    _tts_speaker = None
    _session_tts_speakers: dict[str, str] = {}
    _tts_speed = 1.0
    _tts_output_mode = "play"
    _rule_prompt = ""
    _rule_prompt_for_skill = ""
    _sessions: dict[str, list[dict[str, str]]] = {}
    _history_store: ChatSessions | None = None
    _response_events: dict[str, asyncio.Future] = {}
    _response_texts: dict[str, str] = {}
    _response_tts_speakers: dict[str, str | None] = {}
    _response_voice_directives: dict[str, VoiceDirective | None] = {}
    _session_voice_directives: dict[str, VoiceDirective] = {}
    _response_tasks: dict[str, asyncio.Task] = {}
    last_error: str | None = None

    @classmethod
    def initialize_from_config(cls, enabled: bool | None = None):
        logger.info("[OpenAI] Initializing from config...")
        cls.reload_from_config(enabled=enabled)
        cls._initialized = True

    @classmethod
    def reload_from_config(cls, enabled: bool | None = None):
        """Refresh OpenAI settings from config.py."""
        config_manager = ConfigManager.instance()
        if not cls._reload_listener_registered:
            config_manager.add_reload_listener(
                lambda _old, _new: cls.reload_from_config()
            )
            cls._reload_listener_registered = True

        config = config_manager.get_app_config("openai", {})

        if enabled is not None:
            cls._enabled = enabled
        else:
            env_enabled = get_env("OPENAI_ENABLE")
            cls._enabled = (
                env_enabled.lower() in ("1", "true", "yes")
                if env_enabled is not None
                else False
            )

        previous_base_url = cls._base_url
        previous_timeout = cls._timeout
        cls._protocol = config.get("protocol", "openai")
        if cls._protocol not in ("openai", "hermes_native"):
            raise ValueError("unsupported_chat_protocol")
        cls._base_url = str(config.get("base_url", "http://127.0.0.1:8000/v1")).rstrip("/")
        cls._api_key = str(config.get("api_key", "") or "")
        cls._model = str(config.get("model", "gpt-4o-mini"))
        cls._session_key = str(config.get("session_key", "agent:default:open-xiaoai-bridge"))
        cls._session_header = str(config.get("session_header", "X-Hermes-Session-Key") or "").strip()
        cls._system_prompt = str(config.get("system_prompt", "") or "")
        cls._timeout = int(config.get("response_timeout", 120))
        configured_streaming = config.get(
            "chat_streaming",
            config.get("streaming", config.get("streaming_enabled")),
        )
        env_streaming = get_env("HERMES_CHAT_STREAMING_ENABLE")
        # The deployment environment is the operational override for the
        # checked-in default, so the private server file can enable streaming
        # without changing CONFIG_PATH.
        if env_streaming is not None and str(env_streaming).strip() != "":
            configured_streaming = env_streaming
        if isinstance(configured_streaming, str):
            requested_streaming = configured_streaming.strip().lower() in (
                "1", "true", "yes", "on"
            )
        else:
            requested_streaming = bool(configured_streaming)
        # The streaming event contract is Hermes-native.  Generic OpenAI
        # providers stay on the existing tool-aware complete-response path.
        cls._chat_streaming_enabled = requested_streaming and cls._protocol == "hermes_native"
        cls._history_max_messages = max(0, int(config.get("history_max_messages", 20)))
        history_path = config.get("history_path")
        cls._history_store = ChatSessions(history_path) if history_path else None
        cls._temperature = cls._optional_float(config.get("temperature"))
        cls._max_tokens = cls._optional_int(config.get("max_tokens"))
        cls._extra_body = config.get("extra_body", {})
        if not isinstance(cls._extra_body, dict):
            cls._extra_body = {}
        configured_provider = config.get("tts_provider")
        cls._tts_provider = (
            str(configured_provider).strip().lower()
            if configured_provider
            else None
        )
        cls._tts_speaker = config.get("tts_speaker", None)
        cls._session_tts_speakers = (
            {
                str(key): str(value)
                for key, value in config.get("session_tts_speakers", {}).items()
                if key and value
            }
            if isinstance(config.get("session_tts_speakers", {}), dict)
            else {}
        )
        cls._tts_speed = float(config.get("tts_speed", 1.0))
        cls._tts_output_mode = str(config.get("tts_output_mode", "play") or "play")
        cls._rule_prompt = str(config.get("rule_prompt", "") or "")
        cls._rule_prompt_for_skill = str(config.get("rule_prompt_for_skill", "") or "")

        if cls._hermes_stream_transport is not None and (
            previous_base_url != cls._base_url or previous_timeout != cls._timeout
        ):
            old_transport = cls._hermes_stream_transport
            cls._hermes_stream_transport = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                close = getattr(old_transport, "close", None)
                if close is not None:
                    result = close()
                    if inspect.isawaitable(result):
                        loop.create_task(result)

        if cls._enabled:
            logger.info(
                f"[OpenAI] Enabled, base_url={cls._base_url}, model={cls._model}"
            )

    @classmethod
    def _optional_float(cls, value) -> float | None:
        if value is None or value == "":
            return None
        return float(value)

    @classmethod
    def _optional_int(cls, value) -> int | None:
        if value is None or value == "":
            return None
        return int(value)

    @classmethod
    async def connect(cls) -> bool:
        """No-op connect hook for parity with WebSocket backends."""
        if not cls._initialized:
            cls.initialize_from_config()
        return cls._enabled

    @classmethod
    async def close(cls):
        """Cancel pending response waiters."""
        tasks = list(cls._response_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        cls._response_tasks.clear()
        for waiter in list(cls._response_events.values()):
            if not waiter.done():
                waiter.cancel()
        cls._response_events.clear()
        cls._response_texts.clear()
        cls._response_tts_speakers.clear()
        cls._response_voice_directives.clear()
        cls._session_voice_directives.clear()
        stream_tasks = list(cls._stream_tasks.values())
        for task in stream_tasks:
            task.cancel()
        await asyncio.gather(*stream_tasks, return_exceptions=True)
        cls._stream_tasks.clear()
        cls._stream_active.clear()
        if cls._hermes_stream_transport is not None:
            close = getattr(cls._hermes_stream_transport, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
            cls._hermes_stream_transport = None

    @classmethod
    def is_connected(cls) -> bool:
        return cls.is_enabled()

    @classmethod
    def is_enabled(cls) -> bool:
        if not cls._initialized:
            cls.initialize_from_config()
        return cls._enabled

    @classmethod
    def set_session_key(cls, session_key: str):
        logger.info(
            f"[OpenAI] Session key updated: {cls._session_key!r} -> {session_key!r}"
        )
        cls._session_key = session_key

    @classmethod
    def reset_session(cls, session_key: str | None = None):
        if cls._history_store:
            raise RuntimeError("持久化会话请使用 await start_new_session()")
        key = session_key or cls._session_key
        cls._sessions.pop(key, None)
        cls._session_voice_directives.pop(key, None)

    @classmethod
    async def start_new_session(cls, session_key: str | None = None):
        """清除当前上下文，并隔离远端旧会话的记忆。"""
        key = session_key or cls._session_key
        if cls._history_store:
            await cls._history_store.reset(key)
        cls._sessions.pop(key, None)
        cls._session_voice_directives.pop(key, None)

    @classmethod
    def get_tts_speaker_for_session_key(cls, session_key: str | None = None) -> str | None:
        target_session_key = session_key or cls._session_key
        return cls._session_tts_speakers.get(target_session_key) or cls._tts_speaker

    @classmethod
    def _resolve_tts_provider(cls, tts_speaker: str | None) -> str:
        """Resolve an explicit provider while preserving the legacy speaker rule."""
        from core.services.tts.router import TTSRouter

        return TTSRouter.resolve_provider(cls._tts_provider, tts_speaker)

    @classmethod
    async def send(cls, text: str, wait_response: bool = False) -> str | None:
        run_id = await cls._send_and_track(text)
        if run_id is None:
            return None
        if not wait_response:
            asyncio.create_task(cls._wait_response(run_id))
            return run_id
        try:
            return await cls._wait_response(run_id)
        finally:
            cls._response_tts_speakers.pop(run_id, None)
            cls._response_voice_directives.pop(run_id, None)

    @classmethod
    async def send_and_play_reply(cls, text: str, wait_response: bool = False) -> str | None:
        run_id = await cls._send_and_track(text)
        if run_id is None:
            return None
        if not wait_response:
            asyncio.create_task(cls._wait_and_play_response(run_id))
            return run_id
        try:
            response_text = await cls._wait_response(run_id)
            if response_text:
                await cls._play_response_with_tts(
                    response_text,
                    tts_speaker=cls._response_tts_speakers.get(run_id),
                    voice_directive=cls._response_voice_directives.get(run_id),
                )
            return response_text
        finally:
            cls._response_tts_speakers.pop(run_id, None)
            cls._response_voice_directives.pop(run_id, None)

    @classmethod
    async def chat_turn(cls, text: str, tools: ConversationTools | None = None) -> tuple[str | None, VoiceDirective | None]:
        """语音入口保留本轮声音要求，并确保取消会结束对应模型请求。"""
        if cls._chat_streaming_enabled:
            return await cls.chat_turn_stream(text, tools)
        run_id = await cls._send_and_track(text, tools)
        if run_id is None:
            return None, None
        try:
            reply = await cls._wait_response(run_id)
            return reply, cls._response_voice_directives.get(run_id)
        finally:
            cls._response_tts_speakers.pop(run_id, None)
            cls._response_voice_directives.pop(run_id, None)

    @classmethod
    async def chat_turn_stream(
        cls,
        text: str,
        tools: ConversationTools | None = None,
        *,
        on_sentence=None,
        on_tool_progress=None,
    ) -> tuple[str | None, VoiceDirective | None]:
        """Run one Hermes streaming turn and optionally emit complete sentences.

        ``on_sentence`` is called as soon as a sentence is complete.  A
        two-argument callback receives ``(sentence, directive)`` when voice
        metadata is already available; one-argument callbacks remain
        compatible with silent tests and existing callers.  Tool progress is
        delivered only to ``on_tool_progress`` and is never spoken here.
        """
        cls.last_error = None
        if not cls._initialized:
            cls.initialize_from_config()
        if not cls._enabled:
            cls.last_error = "backend_disabled"
            return None, None
        run_id = str(uuid.uuid4())
        cls._stream_generation += 1
        cls._stream_active[run_id] = True
        task = asyncio.current_task()
        if task is not None:
            cls._stream_tasks[run_id] = task
        try:
            result = await cls._request_chat_completion_stream(
                text,
                tools,
                on_sentence=on_sentence,
                on_tool_progress=on_tool_progress,
                generation=run_id,
            )
            return result
        except asyncio.CancelledError:
            cls.last_error = "cancelled"
            raise
        except HermesStreamFallback:
            # The capability probe is handled inside the request method; this
            # guard protects injected transports that raise after probing.
            cls.last_error = "stream_fallback"
            cls._last_stream_timings = {"streaming": 0.0, "fallback": 1.0}
            response, directive = await cls._request_chat_completion(text, tools)
            if response and on_sentence:
                aggregator = SentenceAggregator()
                for sentence in aggregator.feed(response) + aggregator.finish():
                    await cls._call_sentence_callback(on_sentence, sentence, directive)
            return response, directive
        except Exception as exc:
            cls.last_error = type(exc).__name__
            logger.error(f"[OpenAI] Streaming chat failed: {cls.last_error}")
            return None, None
        finally:
            cls._stream_active.pop(run_id, None)
            cls._stream_tasks.pop(run_id, None)

    @classmethod
    async def cancel_stream(cls, run_id: str | None = None):
        """Cancel an owned stream; late events cannot reach its callbacks."""
        if run_id is None:
            tasks = list(cls._stream_tasks.values())
        else:
            task = cls._stream_tasks.get(run_id)
            tasks = [task] if task is not None else []
        if run_id is None:
            for active_id in cls._stream_active:
                cls._stream_active[active_id] = False
        elif run_id in cls._stream_active:
            cls._stream_active[run_id] = False
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @classmethod
    async def _send_and_track(cls, text: str, tools: ConversationTools | None = None) -> str | None:
        cls.last_error = None
        if not cls._initialized:
            cls.initialize_from_config()
        if not cls._enabled:
            cls.last_error = "backend_disabled"
            logger.warning("[OpenAI] send called but backend is disabled")
            return None

        run_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        cls._response_events[run_id] = loop.create_future()
        cls._response_texts[run_id] = ""
        cls._response_tts_speakers[run_id] = cls.get_tts_speaker_for_session_key()
        logger.user_speech(text, module=f"OpenAI({cls._session_key})")
        cls._response_tasks[run_id] = asyncio.create_task(cls._run_chat_completion(run_id, text, tools))
        return run_id

    @classmethod
    async def _wait_response(cls, run_id: str) -> str | None:
        event = cls._response_events.get(run_id)
        if not event:
            logger.warning(f"[OpenAI] No event found for run {run_id}")
            return None
        try:
            await asyncio.wait_for(event, timeout=cls._timeout)
            return cls._response_texts.pop(run_id, "") or None
        except asyncio.TimeoutError:
            cls.last_error = "TimeoutError"
            logger.warning(f"[OpenAI] Timeout waiting for response (runId: {run_id})")
            return None
        finally:
            cls._response_events.pop(run_id, None)
            cls._response_texts.pop(run_id, None)
            task = cls._response_tasks.pop(run_id, None)
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @classmethod
    async def _run_chat_completion(cls, run_id: str, text: str, tools: ConversationTools | None = None):
        try:
            response_text, voice_directive = await cls._request_chat_completion(text, tools)
            if response_text:
                cls._response_texts[run_id] = response_text
                cls._response_voice_directives[run_id] = voice_directive
                logger.ai_response(response_text, module=f"OpenAI({cls._session_key})")
            else:
                cls.last_error = "empty_response"
        except Exception as exc:
            # 服务错误正文可能包含凭据或用户内容，不进入日志。
            cls.last_error = type(exc).__name__
            logger.error(f"[OpenAI] Chat completion failed: {cls.last_error}")
        finally:
            waiter = cls._response_events.get(run_id)
            if waiter and not waiter.done():
                waiter.get_loop().call_soon_threadsafe(waiter.set_result, None)

    @classmethod
    def _get_stream_transport(cls) -> HermesStreamTransport:
        transport = cls._hermes_stream_transport
        if transport is None or transport.base_url != cls._base_url.rstrip("/"):
            transport = HermesStreamTransport(cls._base_url, timeout=cls._timeout)
            cls._hermes_stream_transport = transport
        return transport

    @classmethod
    async def _call_sentence_callback(cls, callback, sentence: str, directive):
        """Call one- or two-argument sentence callbacks without leaking errors."""
        if callback is None:
            return
        try:
            parameters = inspect.signature(callback).parameters
            accepts_two = len(parameters) >= 2 or any(
                parameter.kind == inspect.Parameter.VAR_POSITIONAL
                for parameter in parameters.values()
            )
        except (TypeError, ValueError):
            accepts_two = False
        result = callback(sentence, directive) if accepts_two else callback(sentence)
        if inspect.isawaitable(result):
            await result

    @classmethod
    async def _request_chat_completion_stream(
        cls,
        text: str,
        tools: ConversationTools | None = None,
        *,
        on_sentence=None,
        on_tool_progress=None,
        generation: int | None = None,
    ) -> tuple[str | None, VoiceDirective | None]:
        """Request one SSE turn and preserve the existing history contract."""
        from core.services.hermes_reasoning import ReasoningCommand

        reasoning_command = ReasoningCommand.from_text(text)
        if reasoning_command is not None:
            return reasoning_command.response(), None

        session_key = cls._session_key
        store = cls._history_store
        saved = await store.load(session_key) if store else None
        history = list(saved.messages) if saved else cls._sessions.setdefault(session_key, [])
        messages: list[dict[str, Any]] = list(cls._build_messages(history, text))
        payload: dict[str, Any] = {
            "model": cls._model,
            "messages": messages,
            "stream": True,
            **cls._extra_body,
        }
        # A caller-supplied extra_body must not silently disable the explicit
        # streaming contract.
        payload["stream"] = True
        if cls._temperature is not None:
            payload["temperature"] = cls._temperature
        if cls._max_tokens is not None:
            payload["max_tokens"] = cls._max_tokens

        remote_key = saved.remote_key if saved else session_key
        headers = cls._headers(session_key if cls._protocol == "hermes_native" else remote_key)
        if cls._protocol == "hermes_native":
            headers["X-Hermes-Session-Id"] = remote_key
            messages = cls._build_messages([], text)
            payload["messages"] = messages
            for key in ("tools", "tool_choice", "parallel_tool_calls"):
                payload.pop(key, None)
        elif tools:
            payload["tools"] = tools.definitions
            payload["parallel_tool_calls"] = False

        started = time.monotonic()
        timings: dict[str, float] = {"request_start": started}
        aggregator = SentenceAggregator()
        chunks: list[str] = []
        voice_metadata = None
        current_directive = cls._session_voice_directives.get(session_key)
        finished = False
        envelope_like = False
        tool_progress_seen = False
        transport = cls._get_stream_transport()
        try:
            try:
                async for event in transport.stream(payload, headers):
                    if generation is not None and not cls._stream_active.get(generation, True):
                        raise asyncio.CancelledError()
                    if "first_byte" not in timings:
                        timings["first_byte"] = time.monotonic()
                    if isinstance(event, TextDelta):
                        chunks.append(event.text)
                        if event.voice is not None:
                            voice_metadata = event.voice
                            current_directive = VoiceDirective.from_mapping(
                                event.voice,
                                default_persist=cls._protocol == "hermes_native",
                            ) or current_directive
                        if "first_text" not in timings:
                            timings["first_text"] = time.monotonic()
                        # Hermes native may wrap the final reply in JSON.  Do not
                        # speak protocol envelope fragments as if they were text.
                        current = "".join(chunks).lstrip()
                        envelope_like = cls._protocol == "hermes_native" and current.startswith(("{", "```json"))
                        if not envelope_like:
                            for sentence in aggregator.feed(event.text):
                                if "first_sentence" not in timings:
                                    timings["first_sentence"] = time.monotonic()
                                if on_sentence is not None:
                                    timings.setdefault("tts_start", time.monotonic())
                                await cls._call_sentence_callback(
                                    on_sentence, sentence, current_directive
                                )
                    elif isinstance(event, ToolProgress):
                        tool_progress_seen = True
                        if isinstance(event.payload, dict) and str(
                            event.payload.get("status", "")
                        ).lower() in {"completed", "complete", "succeeded", "success", "failed", "error"}:
                            timings.setdefault("tool_completion", time.monotonic())
                        if on_tool_progress is not None:
                            result = on_tool_progress(event.payload)
                            if inspect.isawaitable(result):
                                await result
                    elif isinstance(event, TurnFinished):
                        finished = True
                        break
                    elif isinstance(event, StreamError):
                        if not chunks and not tool_progress_seen:
                            raise HermesStreamFallback(None, reason=event.code)
                        raise RuntimeError(event.code)
            except HermesStreamFallback:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not chunks and not tool_progress_seen:
                    raise HermesStreamFallback(None, reason=type(exc).__name__) from exc
                # Once text or a tool progress event has arrived, retrying
                # could duplicate a side effect.  Preserve already generated
                # plain text as a partial turn and let the controller avoid a
                # misleading "no response" prompt.  An incomplete JSON voice
                # envelope is not speakable, so report the turn as unknown.
                if chunks and not envelope_like:
                    for sentence in aggregator.finish():
                        timings.setdefault("first_sentence", time.monotonic())
                        timings.setdefault("tts_start", time.monotonic())
                        await cls._call_sentence_callback(
                            on_sentence, sentence, current_directive
                        )
                    cls.last_error = type(exc).__name__
                    return "".join(chunks).strip() or None, current_directive
                raise
            if not finished:
                if not chunks and not tool_progress_seen:
                    raise HermesStreamFallback(None, reason="stream_ended_without_finish")
                if chunks and not envelope_like:
                    for sentence in aggregator.finish():
                        timings.setdefault("first_sentence", time.monotonic())
                        timings.setdefault("tts_start", time.monotonic())
                        await cls._call_sentence_callback(
                            on_sentence, sentence, current_directive
                        )
                    cls.last_error = "stream_ended_without_finish"
                    return "".join(chunks).strip() or None, current_directive
                raise RuntimeError("stream_ended_without_finish")
            response_text = "".join(chunks).strip() or None
            if response_text is None:
                cls.last_error = "empty_response"
                return None, None
            body = {"choices": [{"message": {"content": response_text}}]}
            if voice_metadata is not None:
                body["choices"][0]["message"]["metadata"] = {"voice": voice_metadata}
            response_text, response_directive = cls._extract_response_details(body)
            if response_text and on_sentence:
                # JSON envelopes were deliberately held back until they could
                # be decoded.  Ordinary streams only need the aggregator's
                # remaining tail; appending the full response again would
                # duplicate an unpunctuated sentence.
                pending = (
                    aggregator.feed(response_text) + aggregator.finish()
                    if envelope_like
                    else aggregator.finish()
                )
                for sentence in pending:
                    if "first_sentence" not in timings:
                        timings["first_sentence"] = time.monotonic()
                    timings.setdefault("tts_start", time.monotonic())
                    await cls._call_sentence_callback(
                        on_sentence, sentence, response_directive or current_directive
                    )
            if response_text:
                if tools:
                    response_text = tools.feedback(response_text)
                cls._append_history(history, text, response_text)
                if store and saved:
                    if cls._protocol == "hermes_native":
                        remote_key = transport.last_headers.get("X-Hermes-Session-Id", remote_key)
                        await store.save(saved, history, remote_key=remote_key)
                    else:
                        await store.save(saved, history)
                if response_directive and response_directive.persist:
                    cls._session_voice_directives[session_key] = response_directive.for_one_turn()
            selected_directive = response_directive or cls._session_voice_directives.get(session_key)
            return response_text, selected_directive
        finally:
            timings.setdefault("finish", time.monotonic())
            cls._last_stream_timings = {
                key: max(0.0, value - started) for key, value in timings.items()
            }
            cls._last_stream_timings["streaming"] = 1.0

    @classmethod
    async def _request_chat_completion(
        cls, text: str, tools: ConversationTools | None = None
    ) -> tuple[str | None, VoiceDirective | None]:
        # Hermes 旧版 API 没有会话 reasoning 控制端点。精确拦截用户
        # 明确的切换命令，避免把 ``/reasoning high`` 交给 Agent 后被
        # 模型当成全局配置操作；普通问题不做任何复杂度判断。
        from core.services.hermes_reasoning import ReasoningCommand
        reasoning_command = ReasoningCommand.from_text(text)
        if reasoning_command is not None:
            return reasoning_command.response(), None

        session_key = cls._session_key
        store = cls._history_store
        saved = await store.load(session_key) if store else None
        history = list(saved.messages) if saved else cls._sessions.setdefault(session_key, [])
        messages: list[dict[str, Any]] = list(cls._build_messages(history, text))
        payload: dict[str, Any] = {
            "model": cls._model,
            "messages": messages,
            "stream": False,
            **cls._extra_body,
        }
        if cls._temperature is not None:
            payload["temperature"] = cls._temperature
        if cls._max_tokens is not None:
            payload["max_tokens"] = cls._max_tokens

        headers = cls._headers(saved.remote_key if saved else session_key)
        remote_key = saved.remote_key if saved else session_key
        if cls._protocol == 'hermes_native':
            # 原生 Hermes 保存完整工具轨迹与会话，Bridge 只发送本轮输入。
            # 记忆作用域稳定，短期 transcript 标识可由 Hermes 压缩后更新。
            headers = cls._headers(session_key)
            headers['X-Hermes-Session-Id'] = remote_key
            messages = cls._build_messages([], text)
            for key in ('tools', 'tool_choice', 'parallel_tool_calls'):
                payload.pop(key, None)

        if tools and cls._protocol != 'hermes_native':
            payload["tools"] = tools.definitions
            payload["parallel_tool_calls"] = False
        for round_index in range(5):
            # 每轮新建 payload，保证发送后的请求与日志快照不被后续 append 改写。
            payload["messages"] = list(messages)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=cls._timeout)) as session:
                async with session.post(cls._chat_completions_url(), json=payload, headers=headers, allow_redirects=False) as response:
                    body = await response.json(content_type=None)
                    if response.status >= 400:
                        raise RuntimeError(f"Chat completion HTTP {response.status}")
                    if cls._protocol == 'hermes_native':
                        remote_key = response.headers.get('X-Hermes-Session-Id', remote_key)
            choices = body.get("choices", []) if isinstance(body, dict) else []
            if cls._protocol == 'hermes_native' and choices and isinstance(choices[0], dict) and choices[0].get('finish_reason') == 'error':
                # 原生 Agent 可用 HTTP200 表示协议送达，内部失败不能保存或播报为正常回复。
                raise RuntimeError('hermes_agent_failed')
            message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
            calls = message.get("tool_calls") if isinstance(message, dict) else None
            if not calls:
                break
            if cls._protocol == 'hermes_native':
                raise ValueError('Hermes应在服务端执行工具并返回最终回复')
            if tools is None or not isinstance(calls, list) or len(calls) != 1 or round_index == 4:
                raise ValueError("工具调用未授权或超出本轮上限")
            call = calls[0]
            if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not isinstance(call.get("function"), dict):
                raise ValueError("工具调用格式无效")
            function = call["function"]
            try:
                result = await tools.invoke(call["id"], function.get("name", ""), function.get("arguments", ""))
            except (ValueError, TypeError, KeyError):
                result = {"status":"rejected","message":"工具参数或权限不满足要求，请重新确认目标和操作；没有自动重试"}
                from core.services.native_bridge import OperationOutcome
                async def rejected_tool():
                    return OperationOutcome("rejected",False,error="invalid_tool_or_permission",confirmation="not_submitted")
                await tools.bridge.run_operation("tool_rejected","工具调用被拒绝",{},rejected_tool,session_key=tools.session_key)
                tools.outcomes.append(result)
            messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
            payload = {**payload}

        response_text, response_directive = cls._extract_response_details(body)
        if response_text:
            if tools:
                response_text = tools.feedback(response_text)
            cls._append_history(history, text, response_text)
            if store and saved:
                if cls._protocol == 'hermes_native':
                    await store.save(saved, history, remote_key=remote_key)
                else:
                    await store.save(saved, history)
            if response_directive and response_directive.persist:
                cls._session_voice_directives[session_key] = response_directive.for_one_turn()
        selected_directive = response_directive or cls._session_voice_directives.get(session_key)
        return response_text, selected_directive

    @classmethod
    def _headers(cls, session_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if cls._api_key:
            headers["Authorization"] = f"Bearer {cls._api_key}"
        # X-Hermes-Session-Key 隔离长期记忆作用域；原生 Hermes 的短期
        # 工具轨迹另由 X-Hermes-Session-Id 续接。通用 OpenAI 模式仍发送历史。
        target_session = cls._session_key if session_key is None else session_key
        if cls._session_header and target_session:
            headers[cls._session_header] = target_session
        return headers

    @classmethod
    def _chat_completions_url(cls) -> str:
        if cls._base_url.endswith("/chat/completions"):
            return cls._base_url
        return cls._base_url.rstrip("/") + "/chat/completions"

    @classmethod
    def _build_messages(cls, history: list[dict[str, str]], text: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if cls._system_prompt:
            messages.append({"role": "system", "content": cls._system_prompt})
        messages.extend(history[-cls._history_max_messages :] if cls._history_max_messages else [])
        messages.append({"role": "user", "content": text})
        return messages

    @classmethod
    def _append_history(cls, history: list[dict[str, str]], text: str, response_text: str):
        if cls._history_max_messages <= 0:
            return
        history.extend(
            [
                {"role": "user", "content": text},
                {"role": "assistant", "content": response_text},
            ]
        )
        if len(history) > cls._history_max_messages:
            del history[: len(history) - cls._history_max_messages]

    @classmethod
    def _extract_response_text(cls, body: Any) -> str | None:
        response_text, _ = cls._extract_response_details(body)
        return response_text

    @classmethod
    def _extract_response_details(
        cls, body: Any
    ) -> tuple[str | None, VoiceDirective | None]:
        """读取文字和可选语音元数据，不把元数据交给朗读文本。"""
        if not isinstance(body, dict):
            return None, None
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return None, None
        first = choices[0]
        if not isinstance(first, dict):
            return None, None
        message = first.get("message")
        response_text: str | None = None
        voice_metadata: Any = first.get("voice") or body.get("voice")
        if isinstance(message, dict):
            message_metadata = message.get("metadata", {})
            if isinstance(message.get("voice"), dict):
                voice_metadata = message["voice"]
            elif isinstance(message_metadata, dict) and isinstance(
                message_metadata.get("voice"), dict
            ):
                voice_metadata = message_metadata["voice"]
            content = message.get("content")
            if isinstance(content, str):
                response_text = content.strip()
            elif isinstance(content, list):
                parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                response_text = "".join(parts).strip() or None
        if response_text is None:
            text = first.get("text")
            response_text = text.strip() if isinstance(text, str) else None
        # Hermes 声音偏好默认在当前阶段延续；旧会话常省略范围字段。
        # 显式 false 仍表示单句要求。其他 provider / 播报 API 保持单句默认。
        directive = VoiceDirective.from_mapping(
            voice_metadata, default_persist=cls._protocol == 'hermes_native'
        )
        if cls._protocol == 'hermes_native' and response_text:
            # 原生 API 透传最终文字；模型以 JSON 信封携带单句语音信息。
            # 普通文字仍可直接播报，不要求修改 Hermes API 或增加代理服务。
            candidate = response_text
            if candidate.startswith('```json\n') and candidate.endswith('\n```'):
                candidate = candidate[8:-4]
            try:
                envelope = json.loads(candidate)
            except (ValueError, TypeError):
                envelope = None
            if (isinstance(envelope, dict) and set(envelope) <= {'reply', 'voice'}
                    and isinstance(envelope.get('reply'), str) and envelope['reply'].strip()):
                response_text = envelope['reply'].strip()
                directive = VoiceDirective.from_mapping(
                    envelope.get('voice'), default_persist=True
                ) or directive
        return response_text, directive

    @classmethod
    async def _wait_and_play_response(cls, run_id: str):
        try:
            response_text = await cls._wait_response(run_id)
            if response_text:
                await cls._play_response_with_tts(
                    response_text,
                    tts_speaker=cls._response_tts_speakers.get(run_id),
                    voice_directive=cls._response_voice_directives.get(run_id),
                )
            else:
                logger.warning(f"[OpenAI] No response text received for run {run_id}")
        finally:
            cls._response_tts_speakers.pop(run_id, None)
            cls._response_voice_directives.pop(run_id, None)

    @classmethod
    async def _play_response_with_tts(
        cls,
        text: str,
        tts_speaker: str | None = None,
        playback_token: int | None = None,
        voice_directive: VoiceDirective | None = None,
    ):
        """Synthesize text and play it through the speaker."""
        from core.services.tts.router import TTSRouter

        resolved_tts_speaker = tts_speaker or cls.get_tts_speaker_for_session_key()
        return await TTSRouter.play(
            text,
            configured_provider=cls._tts_provider,
            tts_speaker=resolved_tts_speaker,
            voice_directive=voice_directive,
            tts_speed=cls._tts_speed,
            output_mode=cls._tts_output_mode,
            playback_token=playback_token,
            log_prefix="OpenAI",
        )

    @classmethod
    async def _play_openai_compatible_tts(
        cls,
        text: str,
        tts_speaker: str | None = None,
        provider: str = "openai",
    ):
        """Compatibility wrapper for the shared TTS router."""
        from core.services.tts.router import TTSRouter

        await TTSRouter.play(
            text,
            configured_provider=provider,
            tts_speaker=tts_speaker,
            tts_speed=cls._tts_speed,
            log_prefix="OpenAI",
        )

"""OpenAIManager streaming seam tests; no Hermes, HA or audio connection."""

import sys
import types
import asyncio
import unittest
from unittest import mock
from unittest.mock import AsyncMock


class _FakeTransport:
    def __init__(self, events):
        self.events = events
        self.calls = []
        self.base_url = "http://hermes/v1"
        self.last_headers = {"X-Hermes-Session-Id": "remote-next"}

    async def stream(self, payload, headers):
        self.calls.append((payload, headers))
        if isinstance(self.events, BaseException):
            raise self.events
        for event in self.events:
            yield event


class OpenAIStreamingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sys.modules.setdefault("open_xiaoai_server", types.SimpleNamespace())
        sys.modules.setdefault(
            "aiohttp",
            types.SimpleNamespace(ClientSession=object, ClientTimeout=object),
        )
        sys.modules.setdefault("requests", types.SimpleNamespace(post=None, get=None))
        from core.openai import OpenAIManager
        from core.services.hermes_stream import TextDelta, TurnFinished

        self.manager = OpenAIManager
        self.manager._initialized = True
        self.manager._enabled = True
        self.manager._protocol = "hermes_native"
        self.manager._base_url = "http://hermes/v1"
        self.manager._model = "hermes"
        self.manager._session_key = "agent:xiaoai-doubao:test"
        self.manager._session_header = "X-Hermes-Session-Key"
        self.manager._api_key = "test-key"
        self.manager._system_prompt = ""
        self.manager._history_store = None
        self.manager._sessions = {}
        self.manager._session_voice_directives = {}
        self.manager._extra_body = {}
        self.manager._temperature = None
        self.manager._max_tokens = None
        self.manager._chat_streaming_enabled = True
        self.manager._stream_generation = 0
        self.events = [
            TextDelta("你好，世界。"),
            TextDelta("这是第二句"),
            TurnFinished(),
        ]
        self.transport = _FakeTransport(self.events)
        self.manager._hermes_stream_transport = self.transport

    async def asyncTearDown(self):
        from core.openai import OpenAIManager
        OpenAIManager._hermes_stream_transport = None
        OpenAIManager._chat_streaming_enabled = False

    async def test_streams_one_request_and_emits_sentences_in_order(self):
        from core.openai import OpenAIManager

        spoken = []
        response, directive = await OpenAIManager.chat_turn_stream(
            "打个招呼", on_sentence=spoken.append
        )

        self.assertEqual(response, "你好，世界。这是第二句")
        self.assertIsNone(directive)
        self.assertEqual(spoken, ["你好，世界。", "这是第二句"])
        self.assertEqual(len(self.transport.calls), 1)
        payload, headers = self.transport.calls[0]
        self.assertTrue(payload["stream"])
        self.assertEqual(headers["X-Hermes-Session-Key"], "agent:xiaoai-doubao:test")
        self.assertEqual(headers["X-Hermes-Session-Id"], "agent:xiaoai-doubao:test")
        self.assertEqual(OpenAIManager._last_stream_timings["streaming"], 1.0)

    async def test_unpunctuated_tail_is_flushed_once(self):
        from core.openai import OpenAIManager
        from core.services.hermes_stream import TextDelta, TurnFinished

        self.transport.events = [TextDelta("尾巴"), TurnFinished()]
        spoken = []
        response, _ = await OpenAIManager.chat_turn_stream("尾巴", on_sentence=spoken.append)
        self.assertEqual(response, "尾巴")
        self.assertEqual(spoken, ["尾巴"])

    async def test_tool_progress_is_forwarded_only_to_diagnostic_callback(self):
        from core.openai import OpenAIManager
        from core.services.hermes_stream import TextDelta, ToolProgress, TurnFinished

        self.transport.events = [ToolProgress({"status": "running"}), TextDelta("完成。"), TurnFinished()]
        spoken, progress = [], []
        response, _ = await OpenAIManager.chat_turn_stream(
            "执行任务", on_sentence=spoken.append, on_tool_progress=progress.append
        )
        self.assertEqual(response, "完成。")
        self.assertEqual(spoken, ["完成。"])
        self.assertEqual(progress, [{"status": "running"}])

    async def test_non_streaming_endpoint_falls_back_without_second_stream_request(self):
        from core.openai import OpenAIManager
        from core.services.hermes_stream import HermesStreamFallback

        self.manager._request_chat_completion = AsyncMock(return_value=("完整回复。", None))
        self.transport.events = HermesStreamFallback({"choices": []})
        spoken = []
        response, _ = await OpenAIManager.chat_turn_stream("继续", on_sentence=spoken.append)
        self.assertEqual(response, "完整回复。")
        self.assertEqual(spoken, ["完整回复。"])
        self.manager._request_chat_completion.assert_awaited_once_with("继续", None)

    async def test_native_json_voice_envelope_is_decoded_before_silent_output(self):
        from core.openai import OpenAIManager
        from core.services.hermes_stream import TextDelta, TurnFinished

        self.transport.events = [
            TextDelta('{"reply":"四川回复。","voice":{"explicit_dialect":"sichuan"}}'),
            TurnFinished(),
        ]
        spoken = []
        response, directive = await OpenAIManager.chat_turn_stream(
            "用四川话", on_sentence=spoken.append
        )
        self.assertEqual(response, "四川回复。")
        self.assertEqual(spoken, ["四川回复。"])
        self.assertEqual(directive.explicit_dialect, "sichuan")

    async def test_malformed_stream_falls_back_before_any_sentence_is_emitted(self):
        from core.openai import OpenAIManager
        from core.services.hermes_stream import StreamError

        self.transport.events = [StreamError("invalid_json")]
        self.manager._request_chat_completion = AsyncMock(return_value=("回退回复。", None))
        spoken = []
        response, _ = await OpenAIManager.chat_turn_stream("回退", on_sentence=spoken.append)
        self.assertEqual(response, "回退回复。")
        self.assertEqual(spoken, ["回退回复。"])
        self.manager._request_chat_completion.assert_awaited_once_with("回退", None)

    async def test_http_error_before_text_uses_complete_response_fallback(self):
        from core.openai import OpenAIManager
        from core.services.hermes_stream import HermesHTTPError

        self.transport.events = HermesHTTPError(503)
        self.manager._request_chat_completion = AsyncMock(return_value=("HTTP回退。", None))
        response, _ = await OpenAIManager.chat_turn_stream("服务不可用", on_sentence=lambda _text: None)
        self.assertEqual(response, "HTTP回退。")
        self.manager._request_chat_completion.assert_awaited_once_with("服务不可用", None)

    async def test_cancelled_turn_does_not_emit_late_delta(self):
        from core.openai import OpenAIManager
        from core.services.hermes_stream import TextDelta, TurnFinished

        ready = asyncio.Event()

        async def delayed_stream(_payload, _headers):
            yield TextDelta("旧回复。")
            ready.set()
            await asyncio.Event().wait()
            yield TextDelta("迟到回复。")
            yield TurnFinished()

        self.transport.stream = delayed_stream
        spoken = []
        task = asyncio.create_task(OpenAIManager.chat_turn_stream("打断", on_sentence=spoken.append))
        await ready.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(spoken, ["旧回复。"])

    async def test_conversation_controller_queue_does_not_repeat_aggregate_reply(self):
        from core.openai_conversation import OpenAIConversationController

        played = []

        class Backend:
            _chat_streaming_enabled = True
            _protocol = "hermes_native"
            _session_key = "agent:xiaoai-doubao:test"

            async def chat_turn_stream(self, _text, _tools, *, on_sentence):
                await on_sentence("第一句。", None)
                await on_sentence("第二句", None)
                return "第一句。第二句", None

            async def _play_response_with_tts(self, text, **_kwargs):
                played.append(text)
                return True

        fake_server = types.SimpleNamespace(
            begin_playback_session=lambda: 7,
            stop_tts_playback=lambda _token: None,
        )
        with mock.patch.dict(sys.modules, {"open_xiaoai_server": fake_server}):
            controller = OpenAIConversationController.__new__(OpenAIConversationController)
            controller.backend = Backend()
            controller._cfg = lambda _key, default=None: default
            controller._turn_tools = None
            controller._turn_voice = None
            controller._feedback_succeeded = False
            controller._stream_queue = None
            controller._streaming_played = False
            controller._playback_token = None
            response = await controller._request_response("你好")
            await controller._play_tts(response)

        self.assertEqual(response, "第一句。第二句")
        self.assertEqual(played, ["第一句。", "第二句"])
        self.assertTrue(controller._feedback_succeeded)


if __name__ == "__main__":
    unittest.main()

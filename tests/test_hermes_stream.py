"""Hermes SSE protocol tests; no network, HA or speaker access."""

import unittest


class HermesSSEParserTest(unittest.TestCase):
    def test_text_delta_progress_and_done_are_normalized(self):
        from core.services.hermes_stream import (
            HermesSSEParser,
            TextDelta,
            ToolProgress,
            TurnFinished,
        )

        parser = HermesSSEParser()
        events = []
        events.extend(parser.feed(b": keep-alive\r\n\r\n"))
        events.extend(parser.feed(
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            b'event: hermes.tool.progress\n'
            b'data: {"status":"running","tool":"ha_state"}\n\n'
            b'data: [DONE]\n\n'
        ))

        self.assertEqual(events, [
            TextDelta("hello"),
            ToolProgress({"status": "running", "tool": "ha_state"}),
            TurnFinished("done"),
        ])
        self.assertEqual(parser.finish(), [])

    def test_split_utf8_and_sse_lines_preserve_content_parts(self):
        from core.services.hermes_stream import HermesSSEParser, TextDelta

        parser = HermesSSEParser()
        payload = 'data: {"choices":[{"delta":{"content":[{"type":"text","text":"流"},{"type":"text","text":"式"}]}}]}\n\n'
        raw = payload.encode("utf-8")
        events = []
        for byte in raw:
            events.extend(parser.feed(bytes([byte])))
        self.assertEqual(events, [TextDelta("流式")])

    def test_malformed_json_is_an_error_and_terminal_is_idempotent(self):
        from core.services.hermes_stream import HermesSSEParser, StreamError, TurnFinished

        parser = HermesSSEParser()
        events = parser.feed(b"data: {not-json}\n\n")
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], StreamError)

        events = parser.feed(b"data: [DONE]\n\n" b"data: [DONE]\n\n")
        self.assertEqual(events, [TurnFinished("done")])
        self.assertEqual(parser.finish(), [])

    def test_unterminated_stream_reports_error_only_when_no_finish_seen(self):
        from core.services.hermes_stream import HermesSSEParser, StreamError

        parser = HermesSSEParser()
        parser.feed('data: {"choices":[{"delta":{"content":"尾巴"}}]}\n'.encode())
        events = parser.finish()
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], StreamError)
        self.assertEqual(events[0].code, "stream_ended_without_finish")


class HermesStreamTransportTest(unittest.IsolatedAsyncioTestCase):
    class Response:
        def __init__(self, *, content_type="text/event-stream", chunks=(), body=None, status=200):
            self.status = status
            self.headers = {"Content-Type": content_type}
            self._chunks = list(chunks)
            self._body = body

            class Content:
                def __init__(self, chunks):
                    self.chunks = chunks

                async def iter_chunked(self, _size):
                    for chunk in self.chunks:
                        yield chunk

            self.content = Content(self._chunks)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self, **_kwargs):
            return self._body

    class Session:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = []
            self.closed = False

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return self.responses.pop(0)

        async def close(self):
            self.closed = True

    async def test_reuses_session_and_emits_events_from_two_streams(self):
        from core.services.hermes_stream import HermesStreamTransport, TextDelta, TurnFinished

        session = self.Session([
            self.Response(chunks=['data: {"choices":[{"delta":{"content":"一"}}]}\n\ndata: [DONE]\n\n'.encode()]),
            self.Response(chunks=['data: {"choices":[{"delta":{"content":"二"}}]}\n\ndata: [DONE]\n\n'.encode()]),
        ])
        transport = HermesStreamTransport("http://hermes/v1", session=session)
        first = [event async for event in transport.stream({"stream": True}, {"X-Test": "one"})]
        second = [event async for event in transport.stream({"stream": True}, {"X-Test": "two"})]
        self.assertEqual(first, [TextDelta("一"), TurnFinished("done")])
        self.assertEqual(second, [TextDelta("二"), TurnFinished("done")])
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1]["allow_redirects"], False)
        self.assertIs(transport.session, session)
        await transport.close()

    async def test_non_streaming_json_is_returned_as_fallback_and_cached(self):
        from core.services.hermes_stream import HermesStreamFallback, HermesStreamTransport

        session = self.Session([self.Response(content_type="application/json", body={"choices": []})])
        transport = HermesStreamTransport("http://hermes/v1", session=session)
        with self.assertRaises(HermesStreamFallback) as context:
            [event async for event in transport.stream({"stream": True}, {})]
        self.assertEqual(context.exception.body, {"choices": []})
        self.assertFalse(transport.streaming_supported)
        await transport.close()

    async def test_cached_unsupported_capability_does_not_make_a_second_request(self):
        from core.services.hermes_stream import HermesStreamFallback, HermesStreamTransport

        session = self.Session([self.Response(content_type="application/json", body={"choices": []})])
        transport = HermesStreamTransport("http://hermes/v1", session=session)
        with self.assertRaises(HermesStreamFallback):
            [event async for event in transport.stream({"stream": True}, {})]
        with self.assertRaises(HermesStreamFallback):
            [event async for event in transport.stream({"stream": True}, {})]
        self.assertEqual(len(session.calls), 1)
        await transport.close()


if __name__ == "__main__":
    unittest.main()

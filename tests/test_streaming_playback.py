"""Sentence aggregation and ordered playback queue tests."""

import asyncio
import unittest


class SentenceAggregatorTest(unittest.TestCase):
    def test_emits_complete_sentences_and_flushes_tail_once(self):
        from core.services.streaming_playback import SentenceAggregator

        aggregator = SentenceAggregator()
        self.assertEqual(aggregator.feed("你好，世界。下一"), ["你好，世界。"])
        self.assertEqual(aggregator.feed("句！"), ["下一句！"])
        self.assertEqual(aggregator.finish(), [])

    def test_keeps_english_decimal_and_does_not_emit_empty_chunks(self):
        from core.services.streaming_playback import SentenceAggregator

        aggregator = SentenceAggregator()
        self.assertEqual(aggregator.feed("Version 2.0 is ready. Next"), ["Version 2.0 is ready."])
        self.assertEqual(aggregator.finish(), ["Next"])

    def test_empty_deltas_and_whitespace_are_ignored(self):
        from core.services.streaming_playback import SentenceAggregator

        aggregator = SentenceAggregator()
        self.assertEqual(aggregator.feed("   "), [])
        self.assertEqual(aggregator.feed("好的\n"), ["好的"])
        self.assertEqual(aggregator.finish(), [])


class OrderedPlaybackQueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_consumes_sentences_in_order(self):
        from core.services.streaming_playback import OrderedPlaybackQueue

        spoken = []
        queue = OrderedPlaybackQueue(lambda sentence: spoken.append(sentence))
        await queue.start()
        await queue.put("第一句")
        await queue.put("第二句")
        await queue.finish()
        self.assertEqual(spoken, ["第一句", "第二句"])

    async def test_cancel_clears_unplayed_sentences_and_rejects_late_items(self):
        from core.services.streaming_playback import OrderedPlaybackQueue

        spoken = []
        gate = asyncio.Event()

        async def consume(sentence):
            spoken.append(sentence)
            if sentence == "第一句":
                await gate.wait()

        queue = OrderedPlaybackQueue(consume)
        await queue.start()
        await queue.put("第一句")
        await queue.put("第二句")
        await asyncio.sleep(0)
        await queue.cancel()
        gate.set()
        await queue.wait_closed()
        self.assertEqual(spoken, ["第一句"])
        with self.assertRaises(asyncio.CancelledError):
            await queue.put("迟到回复")


if __name__ == "__main__":
    unittest.main()

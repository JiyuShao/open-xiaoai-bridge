"""从静默文字入口验证对话；不连接真实服务或设备。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DoubaoTextChatTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "sessions.sqlite3"

    def chat(self, text, *options):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.doubao_text_chat", "--message", text,
             "--state", str(self.state), *options],
            cwd=ROOT,
            env={"PATH": os.environ["PATH"], "PYTHONUTF8": "1"},
            capture_output=True, text=True, timeout=10,
        )
        records = [json.loads(line.split("[DoubaoText] ", 1)[1])
                   for line in result.stdout.splitlines() if "[DoubaoText] {" in line]
        self.assertEqual(len(records), 1, result.stdout + result.stderr)
        return result, records[0]

    def test_text_request_returns_a_silent_mock_reply(self):
        process, reply = self.chat("你好")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(reply["profile"], "xiaoai-doubao")
        self.assertEqual(reply["display_name"], "小爱豆包")
        self.assertEqual(reply["evidence"], "mock")
        self.assertEqual(reply["reply"], "模拟回复：你好")
        self.assertEqual(reply["history_messages"], 0)
        self.assertEqual(reply["hardware_loaded"], False)

    def test_default_conversation_survives_process_restart(self):
        _, first = self.chat("我叫小明")
        process, second = self.chat("接着聊")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(second["history_messages"], 2)
        self.assertEqual(second["session_key"], first["session_key"])

    def test_new_conversation_does_not_inherit_context_or_remote_session(self):
        _, first = self.chat("接下来用四川话")
        process, fresh = self.chat("开始新话题", "--new-session")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(fresh["history_messages"], 0)
        self.assertNotEqual(fresh["session_key"], first["session_key"])
        _, resumed = self.chat("继续")
        self.assertEqual(resumed["history_messages"], 2)
        self.assertEqual(resumed["session_key"], fresh["session_key"])

    def test_failed_turn_is_not_saved_or_replayed_on_recovery(self):
        self.chat("已确认的上文")
        for failure in ("empty", "http-error", "timeout", "unavailable"):
            with self.subTest(failure=failure):
                process, failed = self.chat("失败请求不应重发", "--mock-failure", failure)
                self.assertNotEqual(process.returncode, 0)
                self.assertIsNone(failed["reply"])
                self.assertTrue(failed["error"])
                self.assertLessEqual(failed["request_count"], 1)
                self.assertFalse(failed["hardware_loaded"])
        process, recovered = self.chat("恢复后的新问题")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(recovered["history_messages"], 2)
        self.assertEqual(recovered["request_count"], 1)


if __name__ == "__main__":
    unittest.main()

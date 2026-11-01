import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class HermesReasoningContractTest(unittest.IsolatedAsyncioTestCase):
    def test_profiles_declare_low_default(self):
        for name in ("xiaoai-doubao.json", "xiaoai-doubao-readonly.json"):
            with self.subTest(profile=name):
                profile = json.loads((ROOT / "profiles" / name).read_text())
                self.assertEqual("low", profile["reasoning_effort"])
                self.assertIn("current session", profile["reasoning_scope"])

    def test_reasoning_docs_record_old_api_boundary(self):
        text = (ROOT / "docs" / "hermes-reasoning.md").read_text()
        self.assertIn("agent.reasoning_effort", text)
        self.assertIn("model_options.reasoning_effort", text)
        self.assertIn("不发送", text)

    async def test_explicit_commands_are_intercepted_without_network(self):
        sys.modules.setdefault(
            "aiohttp",
            types.SimpleNamespace(ClientSession=object, ClientTimeout=object),
        )
        sys.modules.setdefault("requests", types.SimpleNamespace(post=None, get=None))
        from core.openai import OpenAIManager

        with patch("aiohttp.ClientSession", side_effect=AssertionError("network")):
            reply, voice = await OpenAIManager._request_chat_completion("/reasoning high")
        self.assertIn("没有修改", reply)
        self.assertIsNone(voice)

    def test_only_explicit_commands_are_parsed(self):
        from core.services.hermes_reasoning import ReasoningCommand

        self.assertEqual("high", ReasoningCommand.from_text("/reasoning high").level)
        self.assertEqual("low", ReasoningCommand.from_text("把思考级别调到低").level)
        self.assertTrue(ReasoningCommand.from_text("/reasoning reset").reset)
        self.assertTrue(ReasoningCommand.from_text("/reasoning --global").global_scope_requested)
        self.assertIsNone(ReasoningCommand.from_text("这个问题很复杂，请认真想想"))


if __name__ == "__main__":
    unittest.main()

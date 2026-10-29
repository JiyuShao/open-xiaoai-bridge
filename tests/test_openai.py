import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OpenAIHeadersTest(unittest.TestCase):
    def setUp(self):
        sys.modules.setdefault("open_xiaoai_server", types.SimpleNamespace())
        sys.modules.setdefault(
            "aiohttp",
            types.SimpleNamespace(ClientSession=object, ClientTimeout=object),
        )
        sys.modules.setdefault("requests", types.SimpleNamespace(post=None, get=None))
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        sys.modules.pop("core.openai", None)
        self.manager = importlib.import_module("core.openai").OpenAIManager
        self.manager._api_key = ""
        self.manager._session_key = "agent:default:open-xiaoai-bridge"

    def test_default_sends_hermes_session_header(self):
        """Default config targets Hermes: session_key goes out as the header."""
        self.assertEqual(
            "agent:default:open-xiaoai-bridge",
            self.manager._headers()["X-Hermes-Session-Key"],
        )

    def test_empty_session_header_disables_it(self):
        """Setting session_header empty keeps requests header-free (plain OpenAI)."""
        self.manager._session_header = ""
        self.assertEqual({"Content-Type": "application/json"}, self.manager._headers())

    def test_session_header_sent_when_configured(self):
        self.manager._session_header = "X-Hermes-Session-Key"
        headers = self.manager._headers()
        self.assertEqual(
            "agent:default:open-xiaoai-bridge",
            headers["X-Hermes-Session-Key"],
        )


class OpenAIVoiceMetadataTest(unittest.TestCase):
    def setUp(self):
        module = importlib.import_module("core.openai")
        self.manager = module.OpenAIManager
        self.manager._session_voice_directives = {}

    def test_extracts_voice_metadata_without_adding_it_to_spoken_text(self):
        text, directive = self.manager._extract_response_details({
            "choices": [{
                "message": {
                    "content": "你好，今天过得怎么样？",
                    "metadata": {
                        "voice": {
                            "context_text": "请用关心的语气回答",
                            "explicit_language": "zh",
                            "explicit_dialect": "sichuan",
                            "session_preference": True,
                        }
                    },
                }
            }]
        })
        self.assertEqual("你好，今天过得怎么样？", text)
        self.assertEqual("sichuan", directive.explicit_dialect)
        self.assertTrue(directive.persist)

    def test_invalid_voice_metadata_does_not_discard_text(self):
        text, directive = self.manager._extract_response_details({
            "choices": [{
                "message": {
                    "content": "继续用默认声音",
                    "voice": {"explicit_dialect": "unknown"},
                }
            }]
        })
        self.assertEqual("继续用默认声音", text)
        self.assertIsNone(directive)

    def test_memory_session_reset_clears_voice_preference(self):
        self.manager._history_store = None
        self.manager._session_key = "agent:xiaoai-doubao:test"
        self.manager._session_voice_directives[self.manager._session_key] = (
            self.manager._extract_response_details({
                "choices": [{
                    "message": {
                        "content": "x",
                        "voice": {"explicit_language": "en"},
                    }
                }]
            })[1]
        )
        self.manager.reset_session()
        self.assertNotIn(
            "agent:xiaoai-doubao:test", self.manager._session_voice_directives
        )

    def test_session_header_omitted_when_session_key_empty(self):
        self.manager._session_header = "X-Hermes-Session-Key"
        self.manager._session_key = ""
        self.assertNotIn("X-Hermes-Session-Key", self.manager._headers())

    def test_bearer_auth_and_session_header_coexist(self):
        self.manager._api_key = "secret"
        self.manager._session_header = "X-Hermes-Session-Key"
        headers = self.manager._headers()
        self.assertEqual("Bearer secret", headers["Authorization"])
        self.assertEqual(
            "agent:default:open-xiaoai-bridge",
            headers["X-Hermes-Session-Key"],
        )


if __name__ == "__main__":
    unittest.main()

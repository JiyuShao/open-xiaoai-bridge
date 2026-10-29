import json
import unittest

from core.services.tts.doubao import DoubaoTTS


class DoubaoPayloadTest(unittest.TestCase):
    def test_payload_forwards_explicit_language_and_dialect(self):
        tts = DoubaoTTS(
            app_id="app",
            access_key="key",
            speaker="zh_female_vv_uranus_bigtts",
            resource_id="seed-tts-2.0",
        )
        payload = tts._build_payload(
            "你好",
            context_texts=["请用自然、温柔的语气回答"],
            explicit_language="en",
            explicit_dialect="sichuan",
        )
        additions = json.loads(payload["req_params"]["additions"])
        self.assertEqual("en", additions["explicit_language"])
        self.assertEqual("sichuan", additions["explicit_dialect"])
        self.assertEqual(
            ["请用自然、温柔的语气回答"], payload["req_params"]["context_texts"]
        )


if __name__ == "__main__":
    unittest.main()

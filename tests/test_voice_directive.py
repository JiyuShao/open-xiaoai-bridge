import unittest

from core.services.tts.voice import VoiceDirective


class VoiceDirectiveTest(unittest.TestCase):
    def test_accepts_supported_language_dialect_and_context(self):
        directive = VoiceDirective.from_mapping({
            "context_text": "请用温柔、自然的语气回答",
            "explicit_language": "zh",
            "explicit_dialect": "sichuan",
            "persist": True,
        })
        self.assertEqual(
            directive,
            VoiceDirective(
                "请用温柔、自然的语气回答", "zh", "sichuan", None, True
            ),
        )
        self.assertEqual(directive.context_texts(), ["请用温柔、自然的语气回答"])

    def test_invalid_metadata_is_dropped_without_failing_reply(self):
        directive = VoiceDirective.from_mapping({
            "explicit_language": "not a language",
            "explicit_dialect": "unknown",
            "context_text": "   ",
        })
        self.assertIsNone(directive)

    def test_dialect_is_dropped_for_non_chinese_language(self):
        directive = VoiceDirective.from_mapping({
            "explicit_language": "en",
            "explicit_dialect": "sichuan",
        })
        self.assertEqual("en", directive.explicit_language)
        self.assertIsNone(directive.explicit_dialect)

    def test_one_turn_directive_does_not_persist(self):
        directive = VoiceDirective.from_mapping({
            "explicit_language": "en",
            "session_preference": True,
        })
        self.assertFalse(directive.for_one_turn().persist)


if __name__ == "__main__":
    unittest.main()

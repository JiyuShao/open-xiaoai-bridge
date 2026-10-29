import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from core.services.tts.voice import VoiceDirective

sys.modules.setdefault("open_xiaoai_server", types.SimpleNamespace())

router_module = importlib.import_module("core.services.tts.router")
TTSRouter = router_module.TTSRouter


class TTSRouterProviderTest(unittest.TestCase):
    def test_explicit_provider_is_shared_by_all_backends(self):
        for provider in ("xiaoai", "doubao", "openai", "mlx_audio"):
            self.assertEqual(
                provider,
                TTSRouter.resolve_provider(provider, "xiaoai"),
            )

    def test_missing_provider_preserves_legacy_speaker_selection(self):
        self.assertEqual("xiaoai", TTSRouter.resolve_provider(None, "xiaoai"))
        self.assertEqual("doubao", TTSRouter.resolve_provider(None, None))
        self.assertEqual("doubao", TTSRouter.resolve_provider(None, "alloy"))


class BackendTTSProviderConfigTest(unittest.TestCase):
    def test_openclaw_reads_tts_provider_without_changing_existing_speaker_keys(self):
        module = importlib.import_module("core.openclaw")
        manager = module.OpenClawManager
        config = {
            "tts_provider": "mlx_audio",
            "tts_speaker": "xiaoai",
            "agent_tts_speakers": {"assistant": "xiaoai"},
        }
        config_manager = types.SimpleNamespace(
            get_app_config=lambda *_args: config,
            add_reload_listener=lambda *_args: None,
        )
        previous_listener_state = manager._reload_listener_registered
        try:
            manager._reload_listener_registered = False
            with patch.object(
                module.ConfigManager, "instance", return_value=config_manager
            ):
                manager.reload_from_config(enabled=True)
            self.assertEqual("mlx_audio", manager._tts_provider)
            self.assertEqual("xiaoai", manager._tts_speaker)
            self.assertEqual({"assistant": "xiaoai"}, manager._agent_tts_speakers)
        finally:
            manager._reload_listener_registered = previous_listener_state

    def test_qwenpaw_reads_tts_provider_without_changing_existing_speaker_keys(self):
        module = importlib.import_module("core.qwenpaw")
        manager = module.QwenPawManager
        config = {
            "tts_provider": "openai",
            "tts_speaker": "sage",
            "session_tts_speakers": {"agent:default:main": "sage"},
        }
        config_manager = types.SimpleNamespace(
            get_app_config=lambda *_args: config,
            add_reload_listener=lambda *_args: None,
        )
        previous_listener_state = manager._reload_listener_registered
        try:
            manager._reload_listener_registered = False
            with patch.object(
                module.ConfigManager, "instance", return_value=config_manager
            ):
                manager.reload_from_config(enabled=True)
            self.assertEqual("openai", manager._tts_provider)
            self.assertEqual("sage", manager._tts_speaker)
            self.assertEqual(
                {"agent:default:main": "sage"}, manager._session_tts_speakers
            )
        finally:
            manager._reload_listener_registered = previous_listener_state


class BackendTTSRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_record_mode_never_calls_native_playback(self):
        records = []
        directive = VoiceDirective(
            context_text="请温柔回答",
            explicit_language="zh",
            explicit_dialect="sichuan",
        )
        with patch.object(
            router_module.open_xiaoai_server,
            "tts_play",
            new=AsyncMock(),
            create=True,
        ) as tts_play:
            await TTSRouter.play(
                "你好",
                configured_provider="doubao",
                tts_speaker=None,
                voice_directive=directive,
                output_mode="record",
                request_sink=records.append,
            )
        tts_play.assert_not_awaited()
        self.assertEqual("zh_female_vv_uranus_bigtts", records[0]["speaker"])
        self.assertEqual("sichuan", records[0]["explicit_dialect"])

    async def test_record_sink_failure_does_not_fallback_to_native_playback(self):
        speaker = unittest.mock.MagicMock()
        speaker.play = AsyncMock()

        def failing_sink(_record):
            raise RuntimeError("recording failed")

        with patch("core.ref.get_speaker", return_value=speaker):
            await TTSRouter.play(
                "你好",
                configured_provider="doubao",
                tts_speaker=None,
                output_mode="record",
                request_sink=failing_sink,
            )
        speaker.play.assert_not_awaited()

    async def test_doubao_forwards_voice_directive_to_native_path(self):
        config = {
            "app_id": "test-app",
            "access_key": "test-key",
            "default_speaker": "zh_female_vv_uranus_bigtts",
            "stream": False,
        }
        config_manager = types.SimpleNamespace(
            get_app_config=lambda *_args: config,
        )
        directive = VoiceDirective(
            context_text="请用温柔、自然的语气回答",
            explicit_language="zh",
            explicit_dialect="sichuan",
            emotion="warm",
        )
        with patch.object(
            router_module.ConfigManager, "instance", return_value=config_manager
        ):
            with patch.object(
                router_module.open_xiaoai_server,
                "tts_play",
                new=AsyncMock(),
                create=True,
            ) as tts_play:
                await TTSRouter.play(
                    "你好",
                    configured_provider="doubao",
                    tts_speaker=None,
                    voice_directive=directive,
                )
        kwargs = tts_play.await_args.kwargs
        self.assertEqual("zh", kwargs["explicit_language"])
        self.assertEqual("sichuan", kwargs["explicit_dialect"])
        self.assertEqual(["请用温柔、自然的语气回答"], kwargs["context_texts"])
        self.assertEqual("warm", kwargs["emotion"])

    async def test_openclaw_delegates_to_shared_router(self):
        module = importlib.import_module("core.openclaw")
        manager = module.OpenClawManager
        previous = (
            manager._tts_provider,
            manager._tts_speaker,
            manager._tts_speed,
            manager._session_key,
            manager._initialized,
        )
        manager._tts_provider = "mlx_audio"
        manager._tts_speaker = "xiaoai"
        manager._tts_speed = 1.1
        manager._session_key = "agent:assistant:main"
        manager._initialized = True
        try:
            with patch.object(
                router_module.TTSRouter, "play", new=AsyncMock()
            ) as play:
                await manager._play_response_with_tts("你好", playback_token=7)
            play.assert_awaited_once_with(
                "你好",
                configured_provider="mlx_audio",
                tts_speaker="xiaoai",
                tts_speed=1.1,
                playback_token=7,
                log_prefix="OpenClaw",
            )
        finally:
            (
                manager._tts_provider,
                manager._tts_speaker,
                manager._tts_speed,
                manager._session_key,
                manager._initialized,
            ) = previous

    async def test_qwenpaw_delegates_to_shared_router(self):
        module = importlib.import_module("core.qwenpaw")
        manager = module.QwenPawManager
        previous = (
            manager._tts_provider,
            manager._tts_speaker,
            manager._tts_speed,
        )
        manager._tts_provider = "openai"
        manager._tts_speaker = "sage"
        manager._tts_speed = 0.9
        try:
            with patch.object(
                router_module.TTSRouter, "play", new=AsyncMock()
            ) as play:
                await manager._play_response_with_tts("你好", playback_token=8)
            play.assert_awaited_once_with(
                "你好",
                configured_provider="openai",
                tts_speaker="sage",
                tts_speed=0.9,
                playback_token=8,
                log_prefix="QwenPaw",
            )
        finally:
            manager._tts_provider, manager._tts_speaker, manager._tts_speed = previous


if __name__ == "__main__":
    unittest.main()

"""混合唤醒的软件边界：模式解析、统一入口和假设备传输。"""

import asyncio
import importlib
import json
import types
from pathlib import Path
import tempfile
import wave
import sys
import os
import unittest
from unittest import mock


class WakeModeTest(unittest.TestCase):
    def setup_config(self, environment):
        import main
        with mock.patch.dict(os.environ, environment, clear=True):
            return main.setup_config(), dict(os.environ)

    def test_new_native_mode_and_old_flag_select_the_same_profile(self):
        old_flags, old_env = self.setup_config({"NATIVE_WAKE_ENABLE": " true "})
        new_flags, new_env = self.setup_config({"WAKE_MODE": " NaTiVe "})
        self.assertEqual(old_flags, new_flags)
        self.assertTrue(new_flags["enable_native_wake"])
        self.assertEqual(old_env["CONFIG_PATH"], new_env["CONFIG_PATH"])

    def test_mixed_requires_only_openai_and_selects_mixed_profile(self):
        flags, env = self.setup_config({"WAKE_MODE": "mixed", "OPENAI_ENABLE": "1", "API_SERVER_ENABLE": "1"})
        self.assertTrue(flags["enable_mixed_wake"])
        self.assertFalse(flags["enable_native_wake"])
        self.assertTrue(env["CONFIG_PATH"].endswith("config.mixed-wake.py"))

    def test_ambiguous_or_unsupported_modes_fail_before_config_loading(self):
        for environment in (
            {"WAKE_MODE": "typo"},
            {"WAKE_MODE": "mixed", "NATIVE_WAKE_ENABLE": "1", "OPENAI_ENABLE": "1"},
            {"WAKE_MODE": "mixed"},
            {"WAKE_MODE": "mixed", "OPENAI_ENABLE": "1", "XIAOZHI_ENABLE": "1"},
            {"WAKE_MODE": "mixed", "OPENAI_ENABLE": "1", "OPENCLAW_ENABLED": "1"},
            {"WAKE_MODE": "mixed", "OPENAI_ENABLE": "1", "AUDIO_INPUT_ENABLE": "0"},
        ):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                self.setup_config(environment)


class MixedRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_words_switch_sessions_without_overlapping_vad_hooks(self):
        from tests.test_native_wake import application
        with application(mixed=True) as (app, xiaoai, transport):
            from core.ref import set_kws, set_vad
            from core.wakeup_session import EventManager
            kws = mock.Mock(paused=False)
            kws.pause.side_effect = lambda: setattr(kws, "paused", True)
            kws.resume.side_effect = lambda: setattr(kws, "paused", False)
            set_kws(kws)
            listening = asyncio.Event()
            vad = mock.Mock()
            vad.resume.side_effect = lambda target: listening.set() if target == "speech" else None
            set_vad(vad)
            runtime = importlib.import_module("open_xiaoai_server")
            runtime.start_recording = mock.AsyncMock()
            runtime.stop_recording = mock.AsyncMock()
            runtime.stop_playing = mock.AsyncMock()
            runtime.start_playing = mock.AsyncMock()
            original = EventManager.on_speech
            with mock.patch.object(app, "loop", asyncio.get_running_loop()):
                self.assertFalse(await app.handle_wakeup("未知", "kws"))
                self.assertFalse(await app.handle_wakeup("豆包豆包", "xiaoai"))
                self.assertTrue(await app.handle_wakeup("豆包豆包", "kws"))
                await asyncio.wait_for(listening.wait(), 1)
                self.assertFalse(kws.paused)
                self.assertFalse(await app.handle_wakeup("豆包豆包", "kws"))
                # VAD 刚把结果排队时，唤醒切换可能先取消同一个 Future。
                with mock.patch.object(asyncio.get_running_loop(), "call_exception_handler") as errors:
                    switched = asyncio.create_task(app.handle_wakeup("理想同学", "kws"))
                    EventManager.on_silence()
                    self.assertTrue(await switched)
                    await asyncio.sleep(0)
                    errors.assert_not_called()
                self.assertEqual(EventManager.on_speech, original)
                self.assertFalse(kws.paused)
                commands = [c.args[0] for c in transport.await_args_list]
                self.assertEqual(sum("pnshelper event_notify" in c for c in commands), 1)
                listening.clear()
                self.assertTrue(await app.handle_wakeup("豆包豆包", "kws"))
                await asyncio.wait_for(listening.wait(), 1)
                await xiaoai.on_event(json.dumps({"event": "instruction", "data": {"NewLine": {
                    "header": {"namespace": "SpeechRecognizer", "name": "RecognizeResult"},
                    "payload": {"results": [{"text": ""}], "is_vad_begin": False},
                }}}))
                self.assertEqual(EventManager.on_speech, original)
                self.assertFalse(kws.paused)
                transport.reset_mock()
                await xiaoai.on_event(json.dumps({"event": "instruction", "data": {"NewLine": {
                    "header": {"namespace": "SpeechRecognizer", "name": "RecognizeResult"},
                    "payload": {"results": [{"text": "明天七点叫我"}], "is_final": True},
                }}}))
                transport.assert_not_awaited()
                self.assertEqual(app.hook_events, [])

    async def test_native_event_interrupts_playback_but_custom_words_do_not(self):
        from tests.test_native_wake import application
        class Response:
            status = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def raise_for_status(self):
                pass
            async def json(self, **kwargs):
                return {"choices": [{"message": {"content": "模拟回复"}}]}
        requests = []
        class Session:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def post(self, url, **kwargs):
                requests.append(kwargs["json"])
                return Response()
        with application(mixed=True) as (app, xiaoai, transport):
            from core.ref import set_kws, set_vad
            from core.wakeup_session import EventManager
            from core.openai import OpenAIManager
            import aiohttp
            config = app.config.get_app_config()
            config["openai"]["history_path"] = str(app.config.get_config_path().parent / "sessions.sqlite3")
            config["tts"]["doubao"].update(app_id="test", access_key="test")
            OpenAIManager.initialize_from_config(enabled=True)
            runtime = importlib.import_module("open_xiaoai_server")
            for name in ("start_recording", "stop_recording", "start_playing", "stop_playing"):
                setattr(runtime, name, mock.AsyncMock())
            runtime.begin_playback_session = lambda: 123
            runtime.stop_tts_playback = mock.Mock()
            playing = asyncio.Event()
            async def tts(*args, **kwargs):
                playing.set()
                await asyncio.Event().wait()
            runtime.tts_play = tts
            kws = mock.Mock(paused=False)
            kws.pause.side_effect = lambda: setattr(kws, "paused", True)
            kws.resume.side_effect = lambda: setattr(kws, "paused", False)
            set_kws(kws)
            vad = mock.Mock()
            def emit_speech(target):
                loop = asyncio.get_running_loop()
                if target == "speech":
                    loop.call_soon(EventManager.on_speech, b"test PCM")
                else:
                    loop.call_soon(EventManager.on_silence)
            vad.resume.side_effect = emit_speech
            set_vad(vad)
            sys.modules["core.services.audio.asr"] = types.SimpleNamespace(
                ASRService=types.SimpleNamespace(asr=lambda *a, **kw: "你好"))
            with mock.patch.object(app, "loop", asyncio.get_running_loop()), mock.patch.object(aiohttp, "ClientSession", Session):
                try:
                    self.assertTrue(await app.handle_wakeup("豆包豆包", "kws"))
                    await asyncio.wait_for(playing.wait(), 2)
                    self.assertTrue(kws.paused)
                    self.assertFalse(await app.handle_wakeup("理想同学", "kws"))
                    await xiaoai.on_event(json.dumps({"event": "instruction", "data": {"NewLine": {
                        "header": {"namespace": "SpeechRecognizer", "name": "RecognizeResult"},
                        "payload": {"results": [{"text": ""}], "is_vad_begin": False},
                    }}}))
                    runtime.stop_tts_playback.assert_called_once_with(123)
                    self.assertFalse(kws.paused)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]["messages"][-1]["content"], "你好")
                    self.assertFalse(any("pnshelper event_notify" in c.args[0] for c in transport.await_args_list))
                finally:
                    await EventManager.interrupt_mixed()
                    await OpenAIManager.close()


class MixedFileAudioTest(unittest.TestCase):
    def test_file_frames_run_through_kws_and_collect_both_routes_without_device_calls(self):
        from tests.test_native_wake import application, load_module
        class Samples:
            def __init__(self, data):
                self.data = data
            def astype(self, _dtype):
                return self
            def __truediv__(self, _scale):
                return self
        class Stream:
            def accept_waveform(self, _rate, samples):
                self.marker = samples.data[0]
        class Spotter:
            def __init__(self, **_kwargs):
                pass
            def create_stream(self):
                return Stream()
            def is_ready(self, stream):
                return bool(stream.marker)
            def decode_stream(self, stream):
                pass
            def get_result(self, stream):
                word = {1: "理想同学", 2: "豆包豆包"}.get(stream.marker)
                stream.marker = 0
                return word
            def reset_stream(self, stream):
                stream.marker = 0
        with application(mixed=True) as (app, _, transport), tempfile.TemporaryDirectory() as directory:
            app.config.get_app_config()["kws"] = {"highpass_hz": 0, "preroll_ms": 0}
            sys.modules.update({
                "numpy": types.SimpleNamespace(frombuffer=lambda data, **kw: Samples(data), int16=object(), float32=object()),
                "sherpa_onnx": types.SimpleNamespace(KeywordSpotter=Spotter),
                "core.services.audio.vad.silero": types.SimpleNamespace(Silero=types.SimpleNamespace(vad=lambda *a: 1.0)),
            })
            root = Path(directory)
            for name, marker in (("native", 1), ("doubao", 2), ("negative", 0)):
                with wave.open(str(root / (name + ".wav")), "wb") as audio:
                    audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    audio.writeframes(bytes([marker, 0]) * 512)
            manifest = root / "cases.json"
            manifest.write_text(json.dumps([
                {"file": "native.wav", "expected": ["native"]},
                {"file": "doubao.wav", "expected": ["openai"]},
                {"file": "negative.wav", "expected": []},
            ]))
            script = load_module("mixed_audio_check", "scripts/check_mixed_wake_audio.py")
            results = script.check_cases(manifest)
            self.assertEqual([r["words"] for r in results], [["理想同学"], ["豆包豆包"], []])
            self.assertTrue(all(r["passed"] for r in results))
            transport.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

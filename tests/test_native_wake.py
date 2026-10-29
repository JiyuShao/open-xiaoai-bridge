"""原生唤醒的软件边界测试；所有设备传输和音频模型均为替身。"""

import asyncio
from contextlib import contextmanager
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from core.native_wake import (
    native_wake_enabled, validate_native_wake_environment,
)

ROOT = Path(__file__).resolve().parents[1]
ENV = {"NATIVE_WAKE_ENABLE": "1", "OPEN_XIAOAI_TOKEN": "offline-test-token"}


class Config:
    def get_app_config(self, path=None, default=None):
        return {
            "wakeup.keywords": ["你好管家"],
            "native_wake.timeout_seconds": 0.01,
        }.get(path, default)


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def config_stub():
    config = Config()
    manager = types.SimpleNamespace(instance=lambda: config)
    with mock.patch.dict(sys.modules, {
        "core.utils.config": types.SimpleNamespace(ConfigManager=manager),
    }):
        yield config


@contextmanager
def application(*, native=True, mixed=False, transport=None):
    """应用、事件、配置及音箱控制均使用真实代码，仅替换设备传输和数值运行时。"""
    from core.ref import GLOBAL_STATES, set_xiaoai

    transport = transport or mock.AsyncMock(return_value=json.dumps({
        "stdout": '{"code": 0}', "exit_code": 0,
    }))
    with tempfile.TemporaryDirectory() as temporary, \
         mock.patch.dict(sys.modules), mock.patch.dict(GLOBAL_STATES, {}, clear=True):
        path = Path(temporary) / "config.py"
        path.write_text('''
async def before_wakeup(speaker, text, source, app):
    app.hook_events.append((text, source))
    app.set_openclaw_session_key("hook-custom-session")
    return None
APP_CONFIG = {"wakeup": {"keywords": ["你好管家"], "before_wakeup": before_wakeup},
              "native_wake": {"timeout_seconds": 0.01, "cooldown_seconds": 0.01}}
''')
        if mixed:
            path.write_text((ROOT / "config.mixed-wake.py").read_text().replace('os.environ.get("HERMES_BASE_URL", "")', '"http://127.0.0.1:1/v1"'))
        for name in list(sys.modules):
            if name.startswith("core.") and name != "core.ref":
                sys.modules.pop(name)
        sys.modules.update({
            "numpy": types.SimpleNamespace(),
            "open_xiaoai_server": types.SimpleNamespace(run_shell=transport),
        })
        with mock.patch.dict(os.environ, {**ENV, "CONFIG_PATH": str(path)}, clear=True):
            module = importlib.import_module("core.app")
            app = module.MainApp(enable_xiaozhi=False, enable_native_wake=native and not mixed,
                                 enable_mixed_wake=mixed, enable_openai=mixed)
            app.hook_events = []
            set_xiaoai(module.XiaoAI)
            try:
                yield app, module.XiaoAI, transport
            finally:
                app.loop.close()


class ApplicationDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_word_uses_real_speaker_without_external_hook(self):
        with application() as (app, _xiaoai, transport):
            self.assertFalse(await app.handle_wakeup("你好管家", "xiaoai"))
            self.assertFalse(await app.handle_wakeup("召唤龙虾", "kws"))
            self.assertTrue(await app.handle_wakeup("你好管家", "kws"))
            transport.assert_awaited_once_with(
                'ubus call pnshelper event_notify \'{"src":0,"event":0}\'', 10000,
            )
            self.assertEqual(app.hook_events, [])

    async def test_native_commands_pass_through_even_if_environment_changes(self):
        with application() as (app, xiaoai, transport):
            with mock.patch.dict(os.environ, {"NATIVE_WAKE_ENABLE": "0"}):
                for text in ("你好管家", "召唤龙虾", "开启连续对话", "明天七点叫我"):
                    await xiaoai.on_event(json.dumps({
                        "event": "instruction", "data": {"NewLine": {
                            "header": {"namespace": "SpeechRecognizer", "name": "RecognizeResult"},
                            "payload": {"results": [{"text": text}], "is_final": True},
                        }},
                    }))
                await xiaoai.on_event(json.dumps({"event": "playing", "data": "idle"}))
            self.assertEqual(xiaoai.speaker.status, "idle")
            self.assertFalse(xiaoai.conversation.is_active())
            self.assertEqual(app.hook_events, [])
            transport.assert_not_awaited()

    async def test_kws_callback_routes_through_application(self):
        for native in (True, False):
            with self.subTest(native=native), application(native=native) as (app, _, transport):
                # 声学模型仅作为外部运行时替身；KWS 回调和分流使用真实实现。
                sys.modules.update({
                    "sherpa_onnx": types.SimpleNamespace(),
                    "onnxruntime": types.SimpleNamespace(
                        SessionOptions=mock.Mock(), InferenceSession=mock.Mock(),
                    ),
                    "numpy": types.SimpleNamespace(zeros=mock.Mock(), float32=object()),
                })
                kws = importlib.import_module("core.services.audio.kws").KWS
                with mock.patch.object(app, "loop", asyncio.get_running_loop()):
                    kws.on_message("你好管家")
                    for _ in range(20):
                        if transport.await_count or app.hook_events:
                            break
                        await asyncio.sleep(0.001)
                    await asyncio.sleep(0)
                if native:
                    transport.assert_awaited_once()
                    self.assertEqual(app.hook_events, [])
                else:
                    self.assertEqual(app.hook_events, [("你好管家", "kws")])
                    transport.assert_not_awaited()

    async def test_legacy_dispatch_still_calls_configured_hook(self):
        with application(native=False) as (app, _xiaoai, transport):
            await app.handle_wakeup("你好管家", "kws")
            self.assertEqual(app.hook_events, [("你好管家", "kws")])
            transport.assert_not_awaited()

    async def test_overlapping_and_cooldown_events_do_not_repeat_rpc(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def call(*_args, **_kwargs):
            started.set()
            await release.wait()
            return json.dumps({"stdout": '{"code": 0}'})

        with application(transport=mock.AsyncMock(side_effect=call)) as (app, _, transport):
            task = asyncio.create_task(app.handle_wakeup("你好管家", "kws"))
            await started.wait()
            self.assertFalse(await app.handle_wakeup("你好管家", "kws"))
            release.set()
            self.assertTrue(await task)
            self.assertFalse(await app.handle_wakeup("你好管家", "kws"))
            self.assertEqual(transport.await_count, 1)
            await asyncio.sleep(0.02)
            self.assertTrue(await app.handle_wakeup("你好管家", "kws"))
            self.assertEqual(transport.await_count, 2)

    async def test_rejected_failed_and_timed_out_calls_are_not_retried(self):
        async def blocked(*_args, **_kwargs):
            await asyncio.Event().wait()

        for kwargs in (
            {"return_value": json.dumps({"stdout": '{"code": 1}'})},
            {"side_effect": ConnectionError("offline")},
            {"side_effect": blocked},
        ):
            with self.subTest(kwargs=kwargs), \
                 application(transport=mock.AsyncMock(**kwargs)) as (app, _, transport):
                self.assertFalse(await app.handle_wakeup("你好管家", "kws"))
                self.assertFalse(await app.handle_wakeup("你好管家", "kws"))
                self.assertEqual(transport.await_count, 1)


class NativeStartupTest(unittest.TestCase):
    def test_environment_rejects_mixed_backends_and_disabled_audio(self):
        with mock.patch.dict(os.environ, ENV, clear=True):
            self.assertTrue(native_wake_enabled())
            validate_native_wake_environment()
            for key in ("XIAOZHI_ENABLE", "OPENCLAW_ENABLE", "OPENCLAW_ENABLED",
                        "OPENAI_ENABLE", "QWENPAW_ENABLE", "API_SERVER_ENABLE"):
                with mock.patch.dict(os.environ, {key: "1"}):
                    with self.assertRaises(ValueError):
                        validate_native_wake_environment()
            with mock.patch.dict(os.environ, {"AUDIO_INPUT_ENABLE": "0"}):
                with self.assertRaises(ValueError):
                    validate_native_wake_environment()

    def test_profiles_are_explicit_and_do_not_override_selected_keywords(self):
        with mock.patch.dict(os.environ, ENV, clear=True):
            upstream = load_module("legacy_profile_test", "config.py")
            self.assertNotEqual(upstream.APP_CONFIG["wakeup"]["keywords"], ["你好管家"])
            native = load_module("native_profile_test", "config.native-wake.py")
            self.assertEqual(native.APP_CONFIG["wakeup"]["keywords"], ["理想同学"])

    def test_keyword_generation_enabled_without_external_backends(self):
        with mock.patch.dict(os.environ, ENV, clear=True):
            module = load_module("native_keywords_test", "core/services/audio/kws/keywords.py")
            self.assertEqual(module.should_generate_keywords(), (True, ""))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(module.should_generate_keywords()[0])

    def test_native_keywords_are_written_without_external_backend_config(self):
        with config_stub(), mock.patch.dict(os.environ, ENV, clear=True), \
             tempfile.TemporaryDirectory() as temporary:
            module = load_module("native_keyword_generation_test", "core/services/audio/kws/keywords.py")
            model_dir = Path(temporary)
            for name in ("tokens.txt", "bpe.model"):
                (model_dir / name).write_text("test fixture")
            tokenize = mock.Mock(return_value=[["你", "好", "管", "家"]])
            with mock.patch.object(module, "get_model_file_path", side_effect=lambda name: str(model_dir / name)), \
                 mock.patch.dict(sys.modules, {"sherpa_onnx": types.SimpleNamespace(text2token=tokenize)}):
                self.assertEqual(module.main(), 0)
            self.assertEqual(tokenize.call_args.args, (["你好管家"],))
            self.assertIn("@你好管家", (model_dir / "keywords.txt").read_text())

    def test_config_loading_is_passive_until_xiaozhi_is_initialized(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.py"
            path.write_text('APP_CONFIG = {"wakeup": {"keywords": ["自选词"]}}')
            with mock.patch.dict(os.environ, {"CONFIG_PATH": str(path)}, clear=True):
                module = load_module("native_config_test", "core/utils/config.py")
                with mock.patch.object(module.ConfigManager, "get_local_ip", return_value="127.0.0.1"), \
                     mock.patch.object(module.requests, "post") as post, \
                     mock.patch.object(module.ConfigManager, "update_config_file") as write:
                    post.return_value.status_code = 200
                    post.return_value.json.return_value = {"mqtt": {"endpoint": "test"}}
                    config = module.ConfigManager.instance()
                    config.reload_app_config()
                    self.assertEqual(config.get_app_config("wakeup.keywords"), ["自选词"])
                    post.assert_not_called()
                    write.assert_not_called()
                    config.initialize_xiaozhi()
                    self.assertEqual(config.get_config("MQTT_INFO"), {"endpoint": "test"})
                    post.assert_called_once()
                    config.initialize_xiaozhi()
                    post.assert_called_once()

    def test_check_is_offline_and_reports_missing_prerequisites(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--check"], cwd=ROOT,
            env={"PATH": os.environ["PATH"], "NATIVE_WAKE_ENABLE": "1"}, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("OPEN_XIAOAI_TOKEN", result.stdout)


if __name__ == "__main__":
    unittest.main()

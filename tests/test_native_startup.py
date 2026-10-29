"""通过真实启动入口验证原生模式，仅替换声学运行时与设备传输。"""

import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import time
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeStartupIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("core", "scripts"):
            shutil.copytree(ROOT / name, self.root / name,
                            ignore=shutil.ignore_patterns("models", "__pycache__"))
        for name in ("main.py", "config.py", "config.native-wake.py", "config.mixed-wake.py"):
            shutil.copy2(ROOT / name, self.root / name)
        models = self.root / "core/models"
        models.mkdir()
        for name in ("silero_vad.onnx", "encoder.onnx", "decoder.onnx",
                     "joiner.onnx", "tokens.txt", "bpe.model"):
            (models / name).write_bytes(b"offline fixture")
        self.env = {"PATH": os.environ["PATH"], "NATIVE_WAKE_ENABLE": " true ",
                    "OPEN_XIAOAI_TOKEN": "private-test-token",
                    "CONFIG_PATH": str(self.root / "config.native-wake.py")}

    def fake_runtime(self):
        runtime = self.root / "fake-runtime"
        runtime.mkdir()
        (runtime / "sitecustomize.py").write_text(r'''
import atexit, asyncio, json, os, sys, threading, types
from pathlib import Path

def record(event, **data):
    with open(os.environ["STARTUP_TRACE"], "a") as stream:
        stream.write(json.dumps({"event": event, **data}) + "\n")

def tokenize(texts, **kwargs):
    record("keywords", words=texts)
    if os.environ.get("FAKE_TOKENS_EMPTY"):
        return []
    return [list(text) for text in texts]

class Spotter:
    def __init__(self, **kwargs):
        record("kws-model")
        if os.environ.get("FAKE_KWS_FAIL"):
            raise RuntimeError("bad KWS model")
    def create_stream(self):
        return object()

async def server():
    record("server-start")
    if os.environ.get("FAKE_SERVER_FAIL"):
        raise RuntimeError("bind failed")
    try:
        await asyncio.Event().wait()
    finally:
        record("server-stop")

sys.modules["numpy"] = types.SimpleNamespace(zeros=lambda *a, **k: [], float32=object())
def asr_model(**kwargs):
    record("asr-model")
    if os.environ.get("FAKE_ASR_FAIL"):
        raise RuntimeError("bad ASR model")
    return object()

def vad_model(*args, **kwargs):
    record("vad-model")
    if os.environ.get("FAKE_VAD_FAIL"):
        raise RuntimeError("bad VAD model")
    return object()

sys.modules["sherpa_onnx"] = types.SimpleNamespace(text2token=tokenize, KeywordSpotter=Spotter,
    OfflineRecognizer=types.SimpleNamespace(from_sense_voice=asr_model))
sys.modules["onnxruntime"] = types.SimpleNamespace(
    __file__=__file__, SessionOptions=types.SimpleNamespace,
    InferenceSession=vad_model,
)
sys.modules["open_xiaoai_server"] = types.SimpleNamespace(
    decode_audio=lambda *a, **k: b"", register_fn=lambda *a: None, start_server=server,
)

@atexit.register
def report():
    audio = sys.modules.get("core.services.audio.stream")
    record("exit", threads=[thread.name for thread in threading.enumerate()],
           readers=len(audio.GlobalStream.readers) if audio else 0,
           asr_loaded="core.services.audio.asr" in sys.modules)
''')
        self.env.update(PYTHONPATH=str(runtime), STARTUP_TRACE=str(self.root / "trace.jsonl"))

    def trace(self):
        path = self.root / "trace.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_entry(self, args=None, **env):
        return subprocess.run(
            [sys.executable, "main.py", *(args or [])], cwd=self.root,
            env={**self.env, **env}, text=True, capture_output=True, timeout=10,
        )

    def prepare_mixed(self):
        self.env.pop("NATIVE_WAKE_ENABLE")
        self.env.update(WAKE_MODE="mixed", OPENAI_ENABLE="1", HERMES_BASE_URL="http://127.0.0.1:1/v1",
                        CONFIG_PATH=str(self.root / "config.mixed-wake.py"))
        folder = self.root / "core/models/sherpa-onnx-sense-voice-test"
        folder.mkdir()
        for name in ("model.int8.onnx", "tokens.txt"):
            (folder / name).write_bytes(b"offline fixture")

    def test_mixed_rejects_invalid_routes_and_missing_resources_before_device(self):
        self.prepare_mixed()
        self.fake_runtime()
        profile = self.root / "config.mixed-wake.py"
        original = profile.read_text()
        for source in (
            original.replace('"豆包豆包": "openai"', '"豆包豆包": "xiaozhi"'),
            original.replace('"keywords": ["理想同学", "豆包豆包"]', '"keywords": [["broken"]]'),
            original.replace('"asr": {"model": "sense_voice", "int8": True}', '"asr": None'),
            original + "\nAPP_CONFIG['home_assistant']={'enabled':True,'base_url':'http://user:private@127.0.0.1','token':'private-test-token','entities':{}}\n",
        ):
            profile.write_text(source)
            result = self.run_entry()
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        profile.write_text(original)
        model = self.root / "core/models/sherpa-onnx-sense-voice-test/model.int8.onnx"
        model.unlink()
        result = self.run_entry()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少本地 SenseVoice", result.stdout)
        self.assertFalse(any(e["event"] == "server-start" for e in self.trace()))

    def test_mixed_loads_models_before_device_and_releases_resources_on_failure(self):
        self.prepare_mixed()
        self.fake_runtime()
        for key in ("FAKE_ASR_FAIL", "FAKE_VAD_FAIL", "FAKE_SERVER_FAIL"):
            with self.subTest(key=key):
                (self.root / "trace.jsonl").unlink(missing_ok=True)
                result = self.run_entry(**{key: "1"})
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                events = self.trace()
                names = [e["event"] for e in events]
                if key != "FAKE_SERVER_FAIL":
                    self.assertNotIn("server-start", names)
                else:
                    self.assertEqual(next(e["words"] for e in events if e["event"] == "keywords"),
                                     ["理想同学", "豆包豆包"])
                    for name in ("asr-model", "vad-model", "kws-model"):
                        self.assertLess(names.index(name), names.index("server-start"))
                self.assertEqual(events[-1]["threads"], ["MainThread"])
                self.assertEqual(events[-1]["readers"], 0)
                self.assertNotIn("Task was destroyed", result.stderr)

    def test_mode_wrappers_normalize_case_and_whitespace_like_python_entry(self):
        project_bin = self.root / ".venv/bin"
        project_bin.mkdir(parents=True)
        (project_bin / "python").symlink_to(sys.executable)
        for mode in ("native", "mixed"):
            if mode == "mixed":
                self.prepare_mixed()
            with self.subTest(mode=mode):
                result = subprocess.run(
                    ["bash", f"scripts/{mode}-wake.sh", "--check"], cwd=self.root,
                    env={**self.env, "WAKE_MODE": f" {mode.upper()} "},
                    text=True, capture_output=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_uses_the_same_mode_rules_without_loading_models(self):
        result = self.run_entry(["--check"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SHA256 encoder.onnx:", result.stdout)
        self.assertIn("不代表模型兼容或真机验收", result.stdout)
        self.assertNotIn("private-test-token", result.stdout + result.stderr)
        self.assertFalse((self.root / "core/models/keywords.txt").exists())

    def test_check_uses_project_python_even_when_system_python_is_old(self):
        binaries = self.root / "old-system"
        binaries.mkdir()
        old_python = binaries / "python3"
        old_python.write_text("#!/bin/sh\necho wrong-system-python >&2\nexit 89\n")
        old_python.chmod(0o755)
        project_bin = self.root / ".venv/bin"
        project_bin.mkdir(parents=True)
        (project_bin / "python").symlink_to(sys.executable)
        result = subprocess.run(
            ["bash", "scripts/native-wake.sh", "--check"], cwd=self.root,
            env={**self.env, "PATH": str(binaries) + os.pathsep + self.env["PATH"]},
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("wrong-system-python", result.stderr)

    def test_empty_tokenization_does_not_start_service_or_replace_old_keywords(self):
        self.fake_runtime()
        path = self.root / "core/models/keywords.txt"
        path.write_text("old words")
        result = self.run_entry(FAKE_TOKENS_EMPTY="1")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(path.read_text(), "old words")
        self.assertFalse(any(event["event"] == "server-start" for event in self.trace()))

    def test_container_copies_native_profile_and_uses_shared_startup(self):
        # 执行 Dockerfile 声明的运行文件和 CMD；不以此替代镜像构建。
        runtime = self.root / "container"
        runtime.mkdir()
        stage = (ROOT / "Dockerfile").read_text().rsplit("FROM ", 1)[1]
        for line in stage.splitlines():
            if line.startswith("COPY ") and "--from" not in line:
                parts = shlex.split(line)[1:]
                for name in parts[:-1]:
                    source = ROOT / name
                    target = runtime / (name if parts[-1] in (".", "./") else parts[-1])
                    if source.is_dir():
                        shutil.copytree(source, target, ignore=shutil.ignore_patterns("models", "__pycache__"))
                    else:
                        shutil.copy2(source, target)
        shutil.copytree(self.root / "core/models", runtime / "core/models")
        cmd = json.loads(next(line[4:] for line in stage.splitlines() if line.startswith("CMD ")))
        if cmd[0] == "/app/.venv/bin/python":
            cmd[0] = sys.executable
        result = subprocess.run(
            [*cmd, "--check"], cwd=runtime,
            env={**self.env, "NATIVE_WAKE_ENABLE": " YES ", "CONFIG_PATH": ""},
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SHA256 encoder.onnx:", result.stdout)

    def test_configuration_load_errors_do_not_expose_private_values(self):
        (self.root / "config.native-wake.py").write_text('raise ValueError("private-test-token")')
        result = self.run_entry(["--check"])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("private-test-token", result.stdout + result.stderr)

    def test_missing_models_and_invalid_config_never_reach_device_service(self):
        self.fake_runtime()
        model = self.root / "core/models/tokens.txt"
        model.unlink()
        result = self.run_entry()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少模型: core/models/tokens.txt", result.stdout)
        model.write_text("fixture")
        for config in (
            'APP_CONFIG = {"wakeup": {"keywords": []}}',
            'APP_CONFIG = {"wakeup": {"keywords": ["你好管家"]}, "native_wake": {"timeout_seconds": 0}}',
            'APP_CONFIG = {"wakeup": {"keywords": ["Hello"]}}',
            'APP_CONFIG = {',
        ):
            with self.subTest(config=config):
                (self.root / "config.native-wake.py").write_text(config)
                result = self.run_entry()
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(event["event"] in ("keywords", "kws-model", "server-start")
                             for event in self.trace()))

    def test_native_mode_rejects_api_backends_and_disabled_audio(self):
        self.fake_runtime()
        for key, value in (
            ("API_SERVER_ENABLE", " YES "), ("OPENCLAW_ENABLED", " ON "),
            ("OPENAI_ENABLE", "TRUE"), ("AUDIO_INPUT_ENABLE", " false "),
        ):
            with self.subTest(key=key):
                result = self.run_entry(**{key: value})
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(event["event"] == "server-start" for event in self.trace()))

    def test_model_and_service_failures_exit_with_resources_released(self):
        self.fake_runtime()
        for key in ("FAKE_KWS_FAIL", "FAKE_SERVER_FAIL"):
            with self.subTest(key=key):
                (self.root / "trace.jsonl").unlink(missing_ok=True)
                result = self.run_entry(**{key: "1"})
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                events = self.trace()
                if key == "FAKE_KWS_FAIL":
                    self.assertFalse(any(event["event"] == "server-start" for event in events))
                self.assertEqual(events[-1]["threads"], ["MainThread"])
                self.assertEqual(events[-1]["readers"], 0)
                self.assertNotIn("Task was destroyed", result.stderr)

    def test_ready_native_mode_prepares_keywords_and_stops_cleanly(self):
        self.fake_runtime()
        process = subprocess.Popen(
            [sys.executable, "main.py"], cwd=self.root, env=self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and process.poll() is None:
                if any(event["event"] == "server-start" for event in self.trace()):
                    break
                time.sleep(0.01)
            else:
                self.fail("原生启动未进入设备服务: " + str(self.trace()))
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
        events = self.trace()
        names = [event["event"] for event in events]
        self.assertLess(names.index("keywords"), names.index("kws-model"))
        self.assertLess(names.index("kws-model"), names.index("server-start"))
        self.assertIn("server-stop", names)
        report = events[-1]
        self.assertEqual(report["threads"], ["MainThread"])
        self.assertEqual(report["readers"], 0)
        self.assertFalse(report["asr_loaded"])


if __name__ == "__main__":
    unittest.main()

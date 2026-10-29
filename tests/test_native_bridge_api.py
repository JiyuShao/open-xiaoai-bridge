"""从受控 HTTP/CLI 到假设备传输，验证指令封装与审计结果。"""

import asyncio
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

sys.modules.setdefault("open_xiaoai_server", types.SimpleNamespace())

from core.ref import GLOBAL_STATES
from core.services.api_server import APIServer
from core.services.speaker import SpeakerManager


class NativeBridgeAPITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.device_result = {"stdout": '{"code":0}', "stderr": "", "exit_code": 0}

        async def fake_shell(script, timeout=10000):
            self.calls.append(script)
            return json.dumps(self.device_result)

        self.config = {"app_id": "fake-app", "access_key": "test-only-credential"}
        self.play = AsyncMock()
        self.active_tokens = set()
        self.next_token = 0
        def begin_playback():
            self.next_token += 1
            self.active_tokens.clear()
            self.active_tokens.add(self.next_token)
            return self.next_token
        def stop_playback(token):
            self.active_tokens.discard(token)
        native_module = sys.modules["open_xiaoai_server"]
        self.patches = [
            patch.dict(GLOBAL_STATES, {}, clear=True),
            patch.dict(os.environ, {"XIAOAI_OPERATION_DB": str(Path(self.temp.name) / "operations.sqlite3")}),
            patch("core.services.api_server.ConfigManager.instance", return_value=types.SimpleNamespace(
                get_app_config=lambda key, default=None: self.config if key == "tts.doubao" else default)),
            patch.object(native_module, "tts_play", self.play, create=True),
            patch.object(native_module, "tts_stream_play", self.play, create=True),
            patch.object(native_module, "stop_playing", AsyncMock(), create=True),
            patch.object(native_module, "begin_playback_session", begin_playback, create=True),
            patch.object(native_module, "stop_tts_playback", stop_playback, create=True),
        ]
        for context in self.patches:
            context.start()
        GLOBAL_STATES["xiaoai"] = types.SimpleNamespace(run_shell=fake_shell)
        SpeakerManager()
        self.server = APIServer(operation_evidence="mock")
        self.client = TestClient(TestServer(self.server.app, host="127.0.0.1"))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for context in reversed(self.patches):
            context.stop()
        self.temp.cleanup()

    async def test_special_text_remains_one_json_argument_with_raw_result(self):
        command = "打开 '客厅'\n灯; $(echo injected); `echo test`"
        response = await self.client.post("/api/xiaoai/command", json={
            "command": command, "request_id": "special-1",
        })
        result = await response.json()
        self.assertEqual(202, response.status, result)
        args = shlex.split(self.calls[0])
        self.assertEqual(["ubus", "call", "mibrain", "ai_service"], args[:4])
        self.assertEqual(5, len(args))
        self.assertEqual({"nlp": 1, "nlp_text": command}, json.loads(args[4]))
        self.assertEqual(self.device_result, result["operation"]["response"])
        self.assertEqual("accepted_only", result["operation"]["confirmation"])
        self.assertEqual("mock", result["operation"]["parameters"]["evidence"])

    async def test_transport_failure_is_unknown_not_rejected(self):
        self.device_result = {"stdout": "", "stderr": "offline", "exit_code": -1}
        response = await self.client.post("/api/xiaoai/command", json={"command": "关灯"})
        result = await response.json()
        self.assertEqual("unknown", result["operation"]["status"])

    async def test_cli_calls_only_isolated_test_endpoint(self):
        tool = Path(__file__).resolve().parents[1] / "skills/xiaoai-bridge/tools/xiaoai-bridge"
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(tool), "xiaoai_command", "查询温度", "--request-id", "cli-1",
            env={**os.environ, "OPENXIAOAI_BASE_URL": str(self.client.make_url("/"))},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        self.assertEqual(0, process.returncode, stderr.decode())
        result = json.loads(stdout)
        self.assertEqual("cli-1", result["operation"]["request_id"])
        self.assertEqual(1, len(self.calls))

    async def test_status_control_is_audited_and_credentials_are_not_recorded(self):
        response = await self.client.post("/api/xiaoai/status", json={
            "request_id": "status-1", "session_key": "test-session", "access_key": "secret-test-key",
        })
        self.assertEqual(200, response.status)
        result = await response.json()
        self.assertEqual("idle", result["operation"]["response"]["status"])
        record_response = await self.client.get("/api/xiaoai/operations/status-1")
        record = await record_response.json()
        self.assertEqual("xiaoai_status", record["operation"]["parameters"]["operation"])
        self.assertEqual("cached_status", record["operation"]["confirmation"])
        self.assertNotIn("secret-test-key", json.dumps(record))

    async def test_duplicate_id_with_different_intent_is_rejected(self):
        await self.client.post("/api/xiaoai/command", json={"command": "打开灯", "request_id": "same-id"})
        response = await self.client.post("/api/xiaoai/command", json={"command": "关闭灯", "request_id": "same-id"})
        self.assertEqual(400, response.status)
        self.assertEqual(1, len(self.calls))

    async def test_announce_and_interrupt_are_deduplicated_and_audited(self):
        for stream in (False, True):
            self.config["stream"] = stream
            payload = {"text": "收到", "request_id": f"announce-{stream}", "context_text": "温柔一点"}
            response = await self.client.post("/api/xiaoai/announce", json=payload)
            result = await response.json()
            self.assertEqual(202, response.status, result)
            self.assertEqual("accepted_only", result["operation"]["confirmation"])
            await self.client.post("/api/xiaoai/announce", json=payload)
        self.assertEqual(2, self.play.await_count)
        self.assertEqual("zh_female_vv_uranus_bigtts", self.play.call_args.kwargs["speaker"])
        for _ in range(2):
            response = await self.client.post("/api/xiaoai/interrupt", json={"request_id": "interrupt-1"})
            self.assertEqual(202, response.status)
        self.assertEqual(1, len(self.calls))
        records = await (await self.client.get("/api/xiaoai/operations")).json()
        self.assertEqual(3, len(records["operations"]))
        self.assertNotIn("test-only-credential", json.dumps(records))

    async def test_invalid_input_does_not_reach_device(self):
        for payload in ([], {"command": ""}, {"command": "打开灯", "silent": "false"},
                        {"command": "打开灯", "timeout_ms": True}, {"command": "打开灯", "request_id": []}):
            response = await self.client.post("/api/xiaoai/command", json=payload)
            self.assertEqual(400, response.status, await response.text())
        self.assertEqual([], self.calls)

    async def test_upstream_credentials_are_redacted_from_persisted_result(self):
        self.device_result["stdout"] = '{"code":0,"access_key":"upstream-secret"}'
        self.device_result["stderr"] = "Authorization: Basic dXNlcjpwYXNz access_key=secret-test-key"
        response = await self.client.post("/api/xiaoai/command", json={
            "command": "查询温度", "request_id": "redacted-1",
        })
        result = await response.json()
        self.assertEqual("accepted", result["operation"]["status"])
        self.assertNotIn("upstream-secret", json.dumps(result))
        self.assertIn("[redacted]", json.dumps(result))
        self.assertNotIn("dXNlcjpwYXNz", json.dumps(result))
        self.assertNotIn("secret-test-key", json.dumps(result))

    async def test_announce_failure_never_invokes_native_fallback(self):
        self.config.clear()
        response = await self.client.post("/api/xiaoai/announce", json={
            "text": "收到", "request_id": "announce-unavailable",
        })
        result = await response.json()
        self.assertEqual("unknown", result["operation"]["status"])
        self.assertEqual("ValueError", result["operation"]["error"])
        self.assertEqual([], self.calls)
        self.play.assert_not_awaited()

    async def test_interrupt_invalidates_inflight_announcement_token(self):
        started = asyncio.Event()
        released = asyncio.Event()
        late_audio = []

        async def delayed_play(*_args, playback_token=None, **_kwargs):
            started.set()
            try:
                await released.wait()
            except asyncio.CancelledError:
                # 模拟已派发的合成任务在 Python 取消后仍返回数据。
                await released.wait()
            if playback_token in self.active_tokens:
                late_audio.append("audio")

        self.play.side_effect = delayed_play
        announce = asyncio.create_task(self.client.post("/api/xiaoai/announce", json={
            "text": "收到", "request_id": "late-announce",
        }))
        await asyncio.wait_for(started.wait(), timeout=1)
        response = await self.client.post("/api/xiaoai/interrupt", json={"request_id": "stop-late"})
        self.assertEqual(202, response.status)
        self.assertFalse(self.active_tokens)
        released.set()
        await asyncio.wait_for(announce, timeout=1)
        self.assertIsNotNone(self.play.call_args.kwargs.get("playback_token"))
        self.assertEqual([], late_audio)

    async def test_announcement_timeout_invalidates_its_token(self):
        async def blocked_play(*_args, **_kwargs):
            await asyncio.Event().wait()

        self.play.side_effect = blocked_play
        response = await self.client.post("/api/xiaoai/announce", json={
            "text": "收到", "request_id": "announce-timeout", "timeout_ms": 100,
        })
        result = await response.json()
        self.assertEqual("unknown", result["operation"]["status"])
        self.assertEqual("timeout", result["operation"]["error"])
        self.assertFalse(self.active_tokens)


if __name__ == "__main__":
    unittest.main()

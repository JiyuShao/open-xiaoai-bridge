import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from core.services.native_bridge import NativeBridgeService
from core.services.operation_records import OperationRecords


class FakeSpeaker:
    def __init__(self, result=True, error=None, delay=0):
        self.result = result
        self.error = error
        self.delay = delay
        self.calls = []

    async def ask_xiaoai_result(self, text, silent=False):
        self.calls.append((text, silent))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return SimpleNamespace(stdout='{"code":0}' if self.result else '{"code":1}', stderr="", exit_code=0)


class NativeBridgeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "operations.sqlite3"
        self.records = OperationRecords(str(self.db_path))

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_accepts_silently_and_records_only_interface_acceptance(self):
        speaker = FakeSpeaker()
        service = NativeBridgeService(self.records, lambda: speaker)

        record = await service.submit(
            "打开客厅灯",
            session_key="agent:xiaoai-doubao:default",
            request_id="req-accepted",
        )

        self.assertEqual("accepted", record.status)
        self.assertTrue(record.accepted)
        self.assertEqual("accepted_only", record.confirmation)
        self.assertEqual("已提交，但接口只确认受理，无法确认业务完成", record.as_dict()["message"])
        self.assertEqual([("打开客厅灯", True)], speaker.calls)

    async def test_preserves_shell_special_characters(self):
        speaker = FakeSpeaker()
        service = NativeBridgeService(self.records, lambda: speaker)
        command = "把 '客厅'\n音量调到 20; echo 不应执行"

        record = await service.submit(command, request_id="req-special")

        self.assertEqual("accepted", record.status)
        self.assertEqual([(command, True)], speaker.calls)
        self.assertEqual(command, record.command)

    async def test_rejected_response_is_distinct(self):
        speaker = FakeSpeaker(result=False)
        service = NativeBridgeService(self.records, lambda: speaker)

        record = await service.submit("关闭插座", request_id="req-rejected")

        self.assertEqual("rejected", record.status)
        self.assertFalse(record.accepted)
        self.assertEqual("rejected", record.confirmation)

    async def test_timeout_is_unknown_without_retry(self):
        speaker = FakeSpeaker(delay=0.2)
        service = NativeBridgeService(self.records, lambda: speaker)

        record = await service.submit("播放音乐", request_id="req-timeout", timeout_ms=100)

        self.assertEqual("unknown", record.status)
        self.assertIsNone(record.accepted)
        self.assertEqual("timeout", record.error)
        self.assertEqual(1, len(speaker.calls))

    async def test_exception_is_unknown(self):
        speaker = FakeSpeaker(error=RuntimeError("fake transport stopped"))
        service = NativeBridgeService(self.records, lambda: speaker)

        record = await service.submit("查询设备状态", request_id="req-error")

        self.assertEqual("unknown", record.status)
        self.assertEqual("RuntimeError", record.error)
        self.assertEqual("unknown", record.confirmation)

    async def test_cancellation_marks_operation_unknown(self):
        speaker = FakeSpeaker(delay=1)
        service = NativeBridgeService(self.records, lambda: speaker)
        task = asyncio.create_task(service.submit("打开窗帘", request_id="req-cancel"))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        record = await self.records.get("req-cancel")
        self.assertIsNotNone(record)
        self.assertEqual("unknown", record.status)
        self.assertEqual("CancelledError", record.error)

    async def test_duplicate_request_id_does_not_execute_twice(self):
        speaker = FakeSpeaker()
        service = NativeBridgeService(self.records, lambda: speaker)

        first = await service.submit("打开台灯", request_id="req-duplicate")
        second = await service.submit("打开台灯", request_id="req-duplicate")

        self.assertEqual(first.status, second.status)
        self.assertEqual(1, len(speaker.calls))
        self.assertEqual(1, second.duplicate_count)

    async def test_concurrent_duplicates_submit_only_once(self):
        speaker = FakeSpeaker(delay=0.03)
        service = NativeBridgeService(self.records, lambda: speaker, evidence="mock")
        await asyncio.gather(*[
            service.submit("打开灯", request_id="req-concurrent") for _ in range(4)
        ])
        record = await service.get("req-concurrent")
        self.assertEqual(1, len(speaker.calls))
        self.assertEqual("accepted", record.status)
        self.assertEqual(3, record.duplicate_count)

    async def test_unknown_result_is_not_replayed_after_reopening(self):
        speaker = FakeSpeaker(error=ConnectionError("offline"))
        first_service = NativeBridgeService(self.records, lambda: speaker, evidence="mock")
        await first_service.submit("打开灯", request_id="req-recover")
        speaker.error = None
        reopened = NativeBridgeService(OperationRecords(str(self.db_path)), lambda: speaker, evidence="mock")
        duplicate = await reopened.submit("打开灯", request_id="req-recover")
        self.assertEqual("unknown", duplicate.status)
        self.assertEqual(1, len(speaker.calls))
        fresh = await reopened.submit("打开灯", request_id="req-intentional")
        self.assertEqual("accepted", fresh.status)
        self.assertEqual(2, len(speaker.calls))

    async def test_new_request_id_executes_after_unknown(self):
        speaker = FakeSpeaker(delay=0.2)
        service = NativeBridgeService(self.records, lambda: speaker)

        first = await service.submit("设置空调 26 度", request_id="req-old", timeout_ms=100)
        speaker.delay = 0
        second = await service.submit("设置空调 26 度", request_id="req-new")

        self.assertEqual("unknown", first.status)
        self.assertEqual("accepted", second.status)
        self.assertEqual(2, len(speaker.calls))

    async def test_records_survive_reopening_database(self):
        speaker = FakeSpeaker()
        service = NativeBridgeService(self.records, lambda: speaker)
        await service.submit("查询温度", request_id="req-persist")

        reopened = OperationRecords(str(self.db_path))
        record = await reopened.get("req-persist")
        self.assertIsNotNone(record)
        self.assertEqual("查询温度", record.command)
        self.assertEqual("agent:xiaoai-doubao:default", record.session_key)

    async def test_reopening_marks_inflight_operation_unknown_without_replay(self):
        await self.records.create_pending(
            "req-restart", "agent:xiaoai-doubao:default", "打开灯", {"silent": True}
        )

        reopened = OperationRecords(str(self.db_path))
        record = await reopened.get("req-restart")
        self.assertIsNotNone(record)
        self.assertEqual("unknown", record.status)
        self.assertEqual("process_restarted", record.error)

    async def test_operation_can_be_deleted_explicitly(self):
        speaker = FakeSpeaker()
        service = NativeBridgeService(self.records, lambda: speaker)
        await service.submit("查询灯状态", request_id="req-delete")

        self.assertTrue(await service.delete("req-delete"))
        self.assertIsNone(await service.get("req-delete"))


if __name__ == "__main__":
    unittest.main()

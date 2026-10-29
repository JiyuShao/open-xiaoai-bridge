"""原生指令与控制操作的受控入口，统一持久化、去重和未知结果处理。"""

import asyncio
from dataclasses import dataclass
import json
import re
from typing import Any, Awaitable, Callable, Literal, Protocol
import uuid

from core.services.operation_records import OperationRecord, OperationRecords


class NativeCommandResult(Protocol):
    stdout: str
    stderr: str
    exit_code: int


class NativeSpeaker(Protocol):
    async def ask_xiaoai_result(self, text: str, silent: bool = False) -> NativeCommandResult: ...


@dataclass(frozen=True)
class OperationOutcome:
    status: Literal["accepted", "rejected", "completed", "unknown"]
    accepted: bool | None = None
    response: Any = None
    error: str | None = None
    confirmation: str = "unknown"


class NativeBridgeService:
    """一次请求先落库再调用设备；一个服务实例独占一个记录库。"""

    _REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    MAX_COMMAND_LENGTH = 1000

    def __init__(
        self, records: OperationRecords, speaker_provider: Callable[[], NativeSpeaker | None],
        *, evidence: str = "device_interface",
    ):
        self.records = records
        self.speaker_provider = speaker_provider
        self.evidence = evidence

    async def submit(
        self,
        command: str,
        *,
        session_key: str = "agent:xiaoai-doubao:default",
        request_id: str | None = None,
        silent: bool = True,
        timeout_ms: int = 10000,
    ) -> OperationRecord:
        if not isinstance(command, str) or not command.strip() or len(command) > self.MAX_COMMAND_LENGTH or "\x00" in command:
            raise ValueError("command 必须是 1 到 1000 字的非空文本，且不含空字符")
        if not isinstance(silent, bool):
            raise ValueError("silent 必须是布尔值")

        async def invoke() -> OperationOutcome:
            speaker = self.speaker_provider()
            if speaker is None:
                return OperationOutcome("rejected", False, error="speaker_unavailable", confirmation="rejected")
            result = await speaker.ask_xiaoai_result(command, silent=silent)
            response = {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code}
            try:
                payload = json.loads(result.stdout)
                code = payload.get("code") if isinstance(payload, dict) else None
            except (ValueError, TypeError):
                code = None
            if result.exit_code != 0 or type(code) is not int:
                return OperationOutcome("unknown", response=response, error="transport_or_invalid_response")
            accepted = code == 0
            return OperationOutcome(
                "accepted" if accepted else "rejected", accepted, response,
                confirmation="accepted_only" if accepted else "rejected",
            )

        return await self.run_operation(
            "xiaoai_command", command, {"silent": silent}, invoke,
            session_key=session_key, request_id=request_id, timeout_ms=timeout_ms,
        )

    async def run_operation(
        self,
        operation: str,
        command: str,
        parameters: dict[str, Any],
        invoke: Callable[[], Awaitable[OperationOutcome]],
        *,
        session_key: str = "agent:xiaoai-doubao:default",
        request_id: str | None = None,
        timeout_ms: int = 10000,
    ) -> OperationRecord:
        """仅供服务器已注册的语义动作调用，HTTP 请求不能指定回调或 shell。"""
        if not isinstance(session_key, str) or not session_key.strip() or len(session_key) > 256:
            raise ValueError("session_key 必须是 1 到 256 字的非空文本")
        if request_id is None:
            request_id = str(uuid.uuid4())
        if not isinstance(request_id, str) or not self._REQUEST_ID.fullmatch(request_id):
            raise ValueError("request_id 格式无效")
        if type(timeout_ms) is not int or not 100 <= timeout_ms <= 120000:
            raise ValueError("timeout_ms 必须在 100 到 120000 之间")

        arguments = {**parameters, "operation": operation, "timeout_ms": timeout_ms, "evidence": self.evidence}
        reservation = asyncio.create_task(self.records.create_pending(request_id, session_key, command, arguments))
        try:
            record, duplicate = await asyncio.shield(reservation)
        except asyncio.CancelledError:
            # SQLite 线程可能仍在提交；等到预留结果确定后记录取消，且不发送设备请求。
            record, duplicate = await reservation
            if not duplicate:
                await self.records.update(request_id, status="unknown", error="CancelledError", confirmation="unknown")
            raise
        if duplicate:
            return record

        try:
            outcome = await asyncio.wait_for(invoke(), timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            outcome = OperationOutcome("unknown", error="timeout")
        except asyncio.CancelledError:
            await self.records.update(request_id, status="unknown", error="CancelledError", confirmation="unknown")
            raise
        except Exception as exc:
            # 错误仅保留类型，异常消息可能含有上游凭据或请求头。
            outcome = OperationOutcome("unknown", error=type(exc).__name__)
        return await self.records.update(
            request_id, status=outcome.status, accepted=outcome.accepted,
            response=outcome.response, error=outcome.error, confirmation=outcome.confirmation,
        )

    async def get(self, request_id: str) -> OperationRecord | None:
        return await self.records.get(request_id)

    async def delete(self, request_id: str) -> bool:
        return await self.records.delete(request_id)

    async def list(self, limit: int = 50, session_key: str | None = None) -> list[OperationRecord]:
        return await self.records.list_records(limit=max(1, min(limit, 100)), session_key=session_key)

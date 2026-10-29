"""持久化保存原生小爱操作及其可观测结果。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import json
import re
from pathlib import Path
import sqlite3
import threading
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_response(value: Any) -> Any:
    """保留设备协议返回结构，隐藏常见鉴权字段及 Bearer 凭据。"""
    secrets = {"access_key", "api_key", "app_key", "secret", "password", "authorization", "token", "access_token", "refresh_token"}
    if isinstance(value, dict):
        return {
            key: "[redacted]" if str(key).lower().replace("-", "_") in secrets else _redact_response(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_response(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            redacted = _redact_response(parsed)
            if redacted != parsed:
                return json.dumps(redacted, ensure_ascii=False)
        value = re.sub(r"(?i)((?:bearer|basic)\s+)[A-Za-z0-9_.~+/=-]+", r"\1[redacted]", value)
        return re.sub(
            r"(?i)((?:access[_-]?key|api[_-]?key|app[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret)\s*[=:]\s*)[^\s,;]+",
            r"\1[redacted]", value,
        )
    return value


@dataclass(frozen=True)
class OperationRecord:
    request_id: str
    created_at: str
    updated_at: str
    session_key: str
    command: str
    parameters: dict[str, Any]
    status: str
    accepted: bool | None
    response: Any
    error: str | None
    confirmation: str
    duplicate_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "session_key": self.session_key,
            "command": self.command,
            "parameters": self.parameters,
            "status": self.status,
            "accepted": self.accepted,
            "response": self.response,
            "error": self.error,
            "confirmation": self.confirmation,
            "duplicate_count": self.duplicate_count,
            "message": {
                "accepted": "已提交，但接口只确认受理，无法确认业务完成",
                "completed": "接口操作已完成",
                "rejected": "请求未受理",
                "unknown": "结果未知，未自动重试",
                "submitting": "正在提交，结果尚未确认",
            }[self.status],
        }


class OperationRecords:
    """SQLite 操作记录；请求标识是去重和恢复查询的边界。"""

    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        with self._init_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            connection = sqlite3.connect(self.path, timeout=5)
            if not self._initialized:
                try:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS operations ("
                        "request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
                        "updated_at TEXT NOT NULL, session_key TEXT NOT NULL, "
                        "command TEXT NOT NULL, parameters TEXT NOT NULL, "
                        "status TEXT NOT NULL, accepted INTEGER, response TEXT, "
                        "error TEXT, confirmation TEXT NOT NULL, duplicate_count INTEGER NOT NULL)"
                    )
                    # 一个 Bridge 进程独占一个记录库；在途状态恢复为未知，绝不重放。
                    connection.execute(
                        "UPDATE operations SET status = 'unknown', confirmation = 'unknown', "
                        "error = 'process_restarted', updated_at = ? WHERE status = 'submitting'",
                        (_now(),),
                    )
                    connection.commit()
                    self._initialized = True
                except BaseException:
                    connection.close()
                    raise
            return connection

    async def create_pending(
        self,
        request_id: str,
        session_key: str,
        command: str,
        parameters: dict[str, Any],
    ) -> tuple[OperationRecord, bool]:
        return await asyncio.to_thread(
            self._create_pending, request_id, session_key, command, parameters
        )

    def _create_pending(
        self,
        request_id: str,
        session_key: str,
        command: str,
        parameters: dict[str, Any],
    ) -> tuple[OperationRecord, bool]:
        connection = self._connect()
        try:
            now = _now()
            inserted = connection.execute(
                "INSERT OR IGNORE INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    now,
                    now,
                    session_key,
                    command,
                    json.dumps(parameters, ensure_ascii=False),
                    "submitting",
                    None,
                    None,
                    None,
                    "pending",
                    0,
                ),
            ).rowcount
            if inserted:
                connection.commit()
                return self._read_one(connection, request_id), False
            existing = self._read_one(connection, request_id)
            if (existing.session_key, existing.command, existing.parameters) != (session_key, command, parameters):
                raise ValueError("request_id 已用于其他请求，请为新的操作使用新标识")
            connection.execute(
                "UPDATE operations SET duplicate_count = duplicate_count + 1, updated_at = ? WHERE request_id = ?",
                (now, request_id),
            )
            connection.commit()
            return self._read_one(connection, request_id), True
        finally:
            connection.close()

    async def update(self, request_id: str, **changes: Any) -> OperationRecord:
        return await asyncio.to_thread(self._update, request_id, changes)

    def _update(self, request_id: str, changes: dict[str, Any]) -> OperationRecord:
        allowed = {"status", "accepted", "response", "error", "confirmation"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不支持的操作记录字段: {sorted(unknown)}")
        assignments = [f"{key} = ?" for key in changes]
        values = [
            json.dumps(_redact_response(changes[key]), ensure_ascii=False) if key == "response" else changes[key]
            for key in changes
        ]
        assignments.append("updated_at = ?")
        values.extend([_now(), request_id])
        connection = self._connect()
        try:
            changed = connection.execute(
                f"UPDATE operations SET {', '.join(assignments)} WHERE request_id = ?",
                values,
            ).rowcount
            if not changed:
                raise KeyError(request_id)
            connection.commit()
            return self._read_one(connection, request_id)
        finally:
            connection.close()

    async def get(self, request_id: str) -> OperationRecord | None:
        return await asyncio.to_thread(self._get, request_id)

    def _get(self, request_id: str) -> OperationRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM operations WHERE request_id = ?", (request_id,)
            ).fetchone()
            return self._from_row(row) if row else None
        finally:
            connection.close()

    async def list_records(self, limit: int = 50, session_key: str | None = None) -> list[OperationRecord]:
        return await asyncio.to_thread(self._list, limit, session_key)

    async def delete(self, request_id: str) -> bool:
        return await asyncio.to_thread(self._delete, request_id)

    def _delete(self, request_id: str) -> bool:
        connection = self._connect()
        try:
            deleted = connection.execute(
                "DELETE FROM operations WHERE request_id = ? AND status != 'submitting'", (request_id,)
            ).rowcount
            connection.commit()
            return bool(deleted)
        finally:
            connection.close()

    def _list(self, limit: int, session_key: str | None) -> list[OperationRecord]:
        connection = self._connect()
        try:
            if session_key:
                rows = connection.execute(
                    "SELECT * FROM operations WHERE session_key = ? ORDER BY created_at DESC LIMIT ?",
                    (session_key, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM operations ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [self._from_row(row) for row in rows]
        finally:
            connection.close()

    def _read_one(self, connection: sqlite3.Connection, request_id: str) -> OperationRecord:
        row = connection.execute(
            "SELECT * FROM operations WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return self._from_row(row)

    @staticmethod
    def _from_row(row: tuple[Any, ...]) -> OperationRecord:
        return OperationRecord(
            request_id=row[0],
            created_at=row[1],
            updated_at=row[2],
            session_key=row[3],
            command=row[4],
            parameters=json.loads(row[5]),
            status=row[6],
            accepted=None if row[7] is None else bool(row[7]),
            response=json.loads(row[8]) if row[8] is not None else None,
            error=row[9],
            confirmation=row[10],
            duplicate_count=row[11],
        )

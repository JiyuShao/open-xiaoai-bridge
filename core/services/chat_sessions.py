"""可恢复的聊天上下文；只保存成功对话，不执行或重放请求。"""

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import threading
import uuid


@dataclass(frozen=True)
class ChatSession:
    key: str
    remote_key: str
    messages: list[dict[str, str]]


class ChatSessions:
    """客户端是聊天历史的唯一来源，远端标识用于隔离新会话。"""

    def __init__(self, path: str):
        self.path = Path(path).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.touch(mode=0o600, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(key TEXT PRIMARY KEY, remote_key TEXT NOT NULL, messages TEXT NOT NULL)"
            )
            connection.commit()
            return connection
        except BaseException:
            connection.close()
            raise

    async def load(self, key: str) -> ChatSession:
        """恢复已有上下文；第一次使用时创建新的远端会话标识。"""
        return await asyncio.to_thread(self._load, key)

    def _load(self, key: str) -> ChatSession:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO sessions VALUES (?, ?, ?)",
                    (key, f"{key}:{uuid.uuid4().hex}", "[]"),
                )
                row = connection.execute(
                    "SELECT remote_key, messages FROM sessions WHERE key = ?", (key,),
                ).fetchone()
            if row is None:
                raise RuntimeError("会话未创建")
            messages = json.loads(row[1])
            if not isinstance(messages, list) or any(
                not isinstance(message, dict)
                or message.get("role") not in ("user", "assistant")
                or not isinstance(message.get("content"), str)
                for message in messages
            ):
                raise ValueError("会话历史格式无效")
            return ChatSession(key, row[0], messages)
        finally:
            connection.close()

    async def save(self, session: ChatSession, messages: list[dict[str, str]]) -> None:
        """原子保存；旧请求不得覆盖已重建或被其他请求更新的会话。"""
        cancelled = threading.Event()
        commit_lock = threading.Lock()
        worker = asyncio.create_task(
            asyncio.to_thread(self._save, session, messages, cancelled, commit_lock)
        )
        try:
            await worker
        except asyncio.CancelledError:
            # asyncio 无法停止已经等待 SQLite 文件锁的线程。先标记取消，
            # 让它在拿到锁后回滚，不在调用方超时后提交本轮。与线程中的
            # “检查取消并提交”共用锁，避免检查和提交之间插入取消。
            if commit_lock.acquire(blocking=False):
                cancelled.set()
                commit_lock.release()
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                # 工作线程仍受 shield 保护；吞掉重复取消后再传播原始取消。
                try:
                    await worker
                except Exception:
                    pass
            except Exception:
                # 保留调用方的取消/超时结果。线程已收到回滚通知，线程异常
                # 不能被误当成一条对话回复。
                pass
            raise

    def _save(
        self,
        session: ChatSession,
        messages: list[dict[str, str]],
        cancelled: threading.Event | None = None,
        commit_lock=None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if cancelled and cancelled.is_set():
                connection.rollback()
                return
            changed = connection.execute(
                "UPDATE sessions SET messages = ? "
                "WHERE key = ? AND remote_key = ? AND messages = ?",
                (json.dumps(messages, ensure_ascii=False), session.key,
                 session.remote_key, json.dumps(session.messages, ensure_ascii=False)),
            ).rowcount
            if not changed:
                connection.rollback()
                raise RuntimeError("会话已更新，本轮回复未保存；请求不会自动重发")
            if commit_lock is None:
                commit_lock = threading.Lock()
            with commit_lock:
                if cancelled and cancelled.is_set():
                    connection.rollback()
                    return
                connection.commit()
        finally:
            connection.close()

    async def reset(self, key: str) -> None:
        """开启新会话，同时更换远端记忆标识。"""
        await asyncio.to_thread(self._reset, key)

    def _reset(self, key: str) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "remote_key = excluded.remote_key, messages = excluded.messages",
                    (key, f"{key}:{uuid.uuid4().hex}", "[]"),
                )
        finally:
            connection.close()

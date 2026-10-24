"""Main application controller.

This module manages the main application flow, coordinating between:
- XiaoAI (Xiaomi speaker service)
- XiaoZhi (AI conversation service)
- OpenClaw (External integration)
- OpenAI (OpenAI-compatible chat service)
- QwenPaw (QwenPaw personal agent workstation)
- Audio system (VAD, KWS, Codec)
"""

import asyncio
import os
import threading
import time

from core.native_wake import NativeWakeController
from core.xiaozhi import XiaoZhi
from core.xiaoai import XiaoAI
from core.ref import get_kws, get_speaker, set_xiaozhi, set_app
from core.utils.config import ConfigManager
from core.utils.logger import logger
from core.services.protocols.typing import (
    DeviceState,
    EventType,
)
from core.openclaw import OpenClawManager
from core.openai import OpenAIManager
from core.qwenpaw import QwenPawManager
from core.services.api_server import APIServer


class MainApp:
    """Main application controller."""

    _instance = None

    @classmethod
    def instance(
        cls,
        enable_xiaozhi: bool = True,
        enable_openclaw: bool = False,
        enable_openai: bool = False,
        enable_qwenpaw: bool = False,
        enable_native_wake: bool = False,
    ):
        """Get singleton instance.

        Args:
            enable_xiaozhi: Whether to enable XiaoZhi AI connection (default: True)
            enable_openclaw: Whether to enable OpenClaw connection (default: False)
            enable_qwenpaw: Whether to enable QwenPaw connection (default: False)
        """
        if cls._instance is None:
            cls._instance = MainApp(
                enable_xiaozhi=enable_xiaozhi,
                enable_openclaw=enable_openclaw,
                enable_openai=enable_openai,
                enable_qwenpaw=enable_qwenpaw,
                enable_native_wake=enable_native_wake,
            )
        return cls._instance

    def __init__(
        self,
        enable_xiaozhi: bool = True,
        enable_openclaw: bool = False,
        enable_openai: bool = False,
        enable_qwenpaw: bool = False,
        enable_native_wake: bool = False,
    ):
        """Initialize the main application.

        Args:
            enable_xiaozhi: Whether to enable XiaoZhi AI connection
            enable_openclaw: Whether to enable OpenClaw connection
            enable_qwenpaw: Whether to enable QwenPaw connection
        """
        if MainApp._instance is not None:
            raise Exception("MainApp is singleton, use instance() to get instance")
        self._enable_native_wake = enable_native_wake
        if self._enable_native_wake and any((
            enable_xiaozhi, enable_openclaw, enable_openai, enable_qwenpaw,
        )):
            raise ValueError("原生唤醒模式不能启用外部 AI 后端")

        MainApp._instance = self

        # Config
        self.config = ConfigManager.instance()
        self._native_wake = NativeWakeController(self.config) if enable_native_wake else None

        # Feature flags
        self._enable_xiaozhi = enable_xiaozhi
        self._enable_openclaw = enable_openclaw
        self._enable_openai = enable_openai
        self._enable_qwenpaw = enable_qwenpaw

        # Device state
        self.device_state = DeviceState.IDLE
        self.current_text = ""
        self.current_emotion = "neutral"

        # Event loop and threads
        self.loop = asyncio.new_event_loop()
        self.loop_thread = None
        self.main_loop_thread = None
        self._xiaoai_future = None
        self.config_watch_thread = None
        self.shutdown_requested = False
        self.running = False

        # Task queue
        self.main_tasks = []
        self.mutex = threading.Lock()

        # Events
        self.events = {
            EventType.SCHEDULE_EVENT: threading.Event(),
            EventType.AUDIO_INPUT_READY_EVENT: threading.Event(),
        }

        # XiaoZhi instance (protocol layer)
        self.xiaozhi = None

        # API Server
        self.api_server = None
        self._enable_api_server = False

        XiaoAI.intercept_native_commands = not enable_native_wake
        set_app(self)

    async def handle_wakeup(self, text: str, source: str):
        """在进入外部对话会话前，统一分流关键词事件。"""
        if self._native_wake is not None:
            return await self._native_wake.wakeup(get_speaker(), text, source)
        from core.wakeup_session import EventManager
        return await EventManager.wakeup(text, source)

    @property
    def protocol(self):
        """Access XiaoZhi protocol for backward compatibility."""
        if self.xiaozhi:
            return self.xiaozhi.protocol
        return None

    def run(self, enable_api_server: bool = False):
        """启动失败时释放已创建的原生模式资源，并把错误交回入口。"""
        try:
            self._run(enable_api_server)
        except BaseException:
            self.shutdown()
            raise

    def _run(self, enable_api_server: bool = False):
        """Start the main application.

        Args:
            enable_api_server: Whether to start the HTTP API Server
        """
        if self._enable_native_wake and enable_api_server:
            raise ValueError("原生唤醒模式不启用 HTTP API")
        if self._enable_native_wake and not os.environ.get("OPEN_XIAOAI_TOKEN", "").strip():
            raise ValueError("原生唤醒模式需要设置 OPEN_XIAOAI_TOKEN")
        self._enable_api_server = enable_api_server

        # Check audio input status
        audio_input_enabled = os.environ.get(
            "AUDIO_INPUT_ENABLE", "true"
        ).strip().lower() in ("true", "1", "yes", "on")
        
        if not audio_input_enabled and self._enable_native_wake:
            raise ValueError("原生唤醒模式需要 AUDIO_INPUT_ENABLE=1")

        if not audio_input_enabled and self._enable_xiaozhi:
            raise RuntimeError(
                "Audio input is disabled (AUDIO_INPUT_ENABLE=false) but XiaoZhi is enabled. "
                "Either enable audio input or disable XiaoZhi."
            )
        
        if not audio_input_enabled:
            local_asr_backends = []
            if (
                self._enable_openclaw
                and self.config.get_app_config("openclaw.input_mode", "local_asr")
                == "local_asr"
            ):
                local_asr_backends.append("OpenClaw")
            if (
                self._enable_openai
                and self.config.get_app_config("openai.input_mode", "local_asr")
                == "local_asr"
            ):
                local_asr_backends.append("OpenAI")
            if (
                self._enable_qwenpaw
                and self.config.get_app_config("qwenpaw.input_mode", "local_asr")
                == "local_asr"
            ):
                local_asr_backends.append("QwenPaw")
            if local_asr_backends:
                raise RuntimeError(
                    "Audio input is disabled (AUDIO_INPUT_ENABLE=false) but "
                    f"{', '.join(local_asr_backends)} uses 'local_asr' mode. "
                    "Either enable audio input, or set input_mode='xiaoai_asr' in config."
                )

        if self._enable_native_wake:
            # 导入与同步模型加载均先于设备服务；失败时不开放监听。
            from core.services.audio.kws import KWS
            KWS.start()

        # Create event loop thread
        self.loop_thread = threading.Thread(target=self._run_event_loop)
        self.loop_thread.daemon = True
        self.loop_thread.start()

        self._start_config_watcher()

        time.sleep(0.1)

        # Initialize XiaoAI service
        self._xiaoai_future = asyncio.run_coroutine_threadsafe(
            XiaoAI.init_xiaoai(event_loop=self.loop if self._enable_native_wake else None),
            self.loop,
        )

        if self._enable_xiaozhi:
            # Create XiaoZhi instance
            self.xiaozhi = XiaoZhi.instance()
            self.xiaozhi.set_app(self)
            set_xiaozhi(self.xiaozhi)

            # Initialize XiaoZhi connection
            asyncio.run_coroutine_threadsafe(self._init_xiaozhi(), self.loop)

        # Initialize OpenClaw if enabled
        if self._enable_openclaw:
            OpenClawManager.initialize_from_config()
            asyncio.run_coroutine_threadsafe(OpenClawManager.connect(), self.loop)
        if self._enable_openai:
            OpenAIManager.initialize_from_config()
            asyncio.run_coroutine_threadsafe(OpenAIManager.connect(), self.loop)
        if self._enable_qwenpaw:
            QwenPawManager.initialize_from_config()
            asyncio.run_coroutine_threadsafe(QwenPawManager.connect(), self.loop)

        # Start API Server if enabled
        if self._enable_api_server:
            host = os.environ.get("API_SERVER_HOST", "127.0.0.1")
            port = int(os.environ.get("API_SERVER_PORT", 9092))
            self.api_server = APIServer(host=host, port=port)
            asyncio.run_coroutine_threadsafe(self.api_server.start(), self.loop)

        # Start main loop thread
        self.main_loop_thread = threading.Thread(target=self._main_loop)
        self.main_loop_thread.daemon = True
        self.main_loop_thread.start()

        # Start audio services
        if (
            self._enable_native_wake
            or self._enable_xiaozhi
            or self._enable_openclaw
            or self._enable_openai
            or self._enable_qwenpaw
        ):
            # Check audio input via env var (same as Rust), default True
            # Supports: "true"/"false", "1"/"0", "yes"/"no", "on"/"off"
            audio_input_enabled = os.environ.get(
                "AUDIO_INPUT_ENABLE", "true"
            ).strip().lower() in ("true", "1", "yes", "on")
            if audio_input_enabled:
                from core.services.audio.kws import KWS
                if not self._enable_native_wake:
                    from core.services.audio.vad import VAD
                    VAD.start()
                # KWS 内部已含 Silero 门控；原生模式无需独立 VAD 会话线程。
                if not self._enable_native_wake:
                    KWS.start()
                logger.info("[MainApp] Audio input enabled (KWS started)")
            else:
                logger.info("[MainApp] Audio input disabled (VAD/KWS not started)")

            # Pre-warm local ASR only when an enabled backend is configured to use it.
            if audio_input_enabled and (
                (
                    self._enable_openclaw
                    and self.config.get_app_config("openclaw.input_mode", "local_asr")
                    == "local_asr"
                )
                or (
                    self._enable_openai
                    and self.config.get_app_config(
                        "openai.input_mode", "local_asr"
                    )
                    == "local_asr"
                )
                or (
                    self._enable_qwenpaw
                    and self.config.get_app_config(
                        "qwenpaw.input_mode", "local_asr"
                    )
                    == "local_asr"
                )
            ):
                from core.services.audio.asr import ASRService

                threading.Thread(
                    target=ASRService.ensure_loaded,
                    daemon=True,
                    name="asr-warmup",
                ).start()

    def _run_event_loop(self):
        """Run asyncio event loop in separate thread."""
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def _start_config_watcher(self):
        """Start config file watcher thread."""
        if self.config_watch_thread and self.config_watch_thread.is_alive():
            return

        self.config_watch_thread = threading.Thread(
            target=self._watch_config_file,
            daemon=True,
        )
        self.config_watch_thread.start()

    def _watch_config_file(self):
        """Poll config file for changes and hot-reload."""
        config_path = self.config.get_config_path()
        last_mtime = None

        while True:
            if self.shutdown_requested:
                break

            try:
                current_mtime = os.path.getmtime(config_path)
                if last_mtime is None:
                    last_mtime = current_mtime
                elif current_mtime != last_mtime:
                    last_mtime = current_mtime
                    self.config.reload_app_config()
                    logger.info(f"[Config] Reloaded runtime config from {config_path}")
            except Exception as exc:
                logger.warning(f"[Config] Failed to reload config: {exc}")

            time.sleep(1)

    async def _init_xiaozhi(self):
        """Initialize XiaoZhi connection and audio."""
        self.device_state = DeviceState.CONNECTING
        await self.xiaozhi.connect()
        self.xiaozhi.init_audio()

    def _main_loop(self):
        """Main application loop."""
        self.running = not self.shutdown_requested

        while self.running:
            for event_type, event in self.events.items():
                if event.is_set():
                    event.clear()

                    if event_type == EventType.AUDIO_INPUT_READY_EVENT:
                        if self.xiaozhi:
                            self.xiaozhi.handle_input_audio()
                    elif event_type == EventType.SCHEDULE_EVENT:
                        self._process_scheduled_tasks()

            time.sleep(0.01)

    def _process_scheduled_tasks(self):
        """Process scheduled tasks."""
        with self.mutex:
            tasks = self.main_tasks.copy()
            self.main_tasks.clear()

        for task in tasks:
            try:
                task()
            except Exception as exc:
                logger.error(
                    f"[MainApp] Scheduled task failed: {type(exc).__name__}: {exc}"
                )

    def schedule(self, callback):
        """Schedule task to main loop."""
        with self.mutex:
            if "abort_speaking" in str(callback):
                if any("abort_speaking" in str(task) for task in self.main_tasks):
                    return
            self.main_tasks.append(callback)
        self.events[EventType.SCHEDULE_EVENT].set()

    # State management

    def set_chat_message(self, role, message):
        """Set chat message."""
        self.current_text = message

    def set_emotion(self, emotion):
        """Set emotion."""
        self.current_emotion = emotion

    def alert(self, title, message):
        """Show alert."""
        logger.warning(f"[Alert] {title}: {message}")

    # Shutdown

    def shutdown(self):
        """Shutdown the application."""
        if self.shutdown_requested:
            return
        self.shutdown_requested = True
        self.running = False

        if self._enable_native_wake and get_kws() is not None:
            get_kws().stop()

        if self.xiaozhi:
            self.xiaozhi.shutdown()

        if self.api_server:
            asyncio.run_coroutine_threadsafe(
                self.api_server.stop(), self.loop
            )

        # Close OpenClaw connection if connected
        if self._enable_openclaw and OpenClawManager.is_connected():
            asyncio.run_coroutine_threadsafe(
                OpenClawManager.close(), self.loop
            )
        if self._enable_openai and OpenAIManager.is_enabled():
            asyncio.run_coroutine_threadsafe(
                OpenAIManager.close(), self.loop
            )
        if self._enable_qwenpaw and QwenPawManager.is_enabled():
            asyncio.run_coroutine_threadsafe(
                QwenPawManager.close(), self.loop
            )

        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=1.0)

        if self.config_watch_thread and self.config_watch_thread.is_alive():
            self.config_watch_thread.join(timeout=1.5)
        if self.main_loop_thread and self.main_loop_thread.is_alive():
            self.main_loop_thread.join(timeout=1.0)
        if self.loop_thread is None and not self.loop.is_closed():
            self.loop.close()

    def check_service(self):
        """设备服务异常或意外结束时，让进程失败而非空转。"""
        if self._xiaoai_future is not None and self._xiaoai_future.done():
            self._xiaoai_future.result()
            if not self.shutdown_requested:
                raise RuntimeError("小爱音箱服务已意外结束")

    # Public API

    async def send_text(self, text):
        """Send text to XiaoZhi."""
        if self.xiaozhi and self.xiaozhi.is_connected():
            await self.xiaozhi.send_text(text)

    async def send_to_openclaw(self, text: str, wait_response: bool = False) -> str | None:
        """Send message to OpenClaw (for skill-based autonomous playback).

        Automatically appends rule_prompt_for_skill if configured.
        Returns run_id or response text on success, None on failure.
        """
        try:
            from core.openclaw import OpenClawManager
            full_text = text
            if OpenClawManager._rule_prompt_for_skill:
                full_text = text + "\n" + OpenClawManager._rule_prompt_for_skill
            return await OpenClawManager.send(full_text, wait_response=wait_response)
        except Exception as e:
            logger.error(f"[MainApp] 发送消息到 OpenClaw 失败: {type(e).__name__}: {e}")
            return None

    async def send_to_openclaw_and_play_reply(self, text: str, wait_response: bool = False) -> str | None:
        """Send message to OpenClaw and play the reply via TTS.

        Automatically appends rule_prompt if configured.
        Returns run_id or response text on success, None on failure.
        """
        try:
            from core.openclaw import OpenClawManager
            full_text = text
            if OpenClawManager._rule_prompt:
                full_text = text + "\n" + OpenClawManager._rule_prompt
            return await OpenClawManager.send_and_play_reply(full_text, wait_response=wait_response)
        except Exception as e:
            logger.error(f"[MainApp] 发送消息到 OpenClaw 失败: {type(e).__name__}: {e}")
            return None

    def set_openclaw_session_key(self, session_key: str):
        """Override the OpenClaw session key at runtime.

        Call this before sending a message or triggering a wakeup to route
        the conversation to a different session.

        Args:
            session_key: New session key (e.g. "agent:user123:my-app").
        """
        OpenClawManager.set_session_key(session_key)

    async def send_to_openai(self, text: str, wait_response: bool = False) -> str | None:
        """Send message to the OpenAI-compatible service."""
        try:
            full_text = text
            if OpenAIManager._rule_prompt_for_skill:
                full_text = text + "\n" + OpenAIManager._rule_prompt_for_skill
            return await OpenAIManager.send(full_text, wait_response=wait_response)
        except Exception as e:
            logger.error(f"[MainApp] 发送消息到 OpenAI 兼容服务失败: {type(e).__name__}: {e}")
            return None

    async def send_to_openai_and_play_reply(
        self,
        text: str,
        wait_response: bool = False,
    ) -> str | None:
        """Send message to the OpenAI-compatible service and play the reply."""
        try:
            full_text = text
            if OpenAIManager._rule_prompt:
                full_text = text + "\n" + OpenAIManager._rule_prompt
            return await OpenAIManager.send_and_play_reply(
                full_text,
                wait_response=wait_response,
            )
        except Exception as e:
            logger.error(f"[MainApp] 发送消息到 OpenAI 兼容服务失败: {type(e).__name__}: {e}")
            return None

    def set_openai_session_key(self, session_key: str):
        """Override the OpenAI-compatible service session key at runtime."""
        OpenAIManager.set_session_key(session_key)

    async def send_to_qwenpaw(self, text: str, wait_response: bool = False) -> str | None:
        """Send message to QwenPaw."""
        try:
            full_text = text
            if QwenPawManager._rule_prompt_for_skill:
                full_text = text + "\n" + QwenPawManager._rule_prompt_for_skill
            return await QwenPawManager.send(full_text, wait_response=wait_response)
        except Exception as e:
            logger.error(f"[MainApp] 发送消息到 QwenPaw 失败: {type(e).__name__}: {e}")
            return None

    async def send_to_qwenpaw_and_play_reply(
        self,
        text: str,
        wait_response: bool = False,
    ) -> str | None:
        """Send message to QwenPaw and play the reply."""
        try:
            full_text = text
            if QwenPawManager._rule_prompt:
                full_text = text + "\n" + QwenPawManager._rule_prompt
            return await QwenPawManager.send_and_play_reply(
                full_text,
                wait_response=wait_response,
            )
        except Exception as e:
            logger.error(f"[MainApp] 发送消息到 QwenPaw 失败: {type(e).__name__}: {e}")
            return None

    def set_qwenpaw_session_key(self, session_key: str):
        """Override the QwenPaw session key at runtime."""
        QwenPawManager.set_session_key(session_key)

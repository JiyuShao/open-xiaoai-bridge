"""
HTTP API Server for XiaoZhi
Provides endpoints to play text/audio remotely
"""

import asyncio
import json
import os
import tempfile
from collections.abc import Coroutine
from typing import Any

import open_xiaoai_server
from aiohttp import BodyPartReader, web
from core.ref import get_app, get_speaker, get_xiaoai
from core.services.tts.doubao import DoubaoTTS
from core.services.tts.voice import VoiceDirective
from core.services.native_bridge import NativeBridgeService, OperationOutcome
from core.services.operation_records import OperationRecords
from core.utils.config import ConfigManager
from core.utils.logger import logger


class APIServer:
    """HTTP API Server to control XiaoZhi speaker remotely"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080, *, operation_evidence: str = "device_interface"):
        self.host = host
        self.port = port
        self.config = ConfigManager.instance()
        self.app = web.Application()
        self.runner = None
        self.site = None
        self._announcement_tokens: set[int] = set()
        operation_db = os.environ.get(
            "XIAOAI_OPERATION_DB",
            self.config.get_app_config("xiaoai.operation_db", "app/xiaoai/operations.sqlite3"),
        )
        app = get_app()
        self.native_bridge = app.native_bridge if app is not None else NativeBridgeService(
            OperationRecords(operation_db), lambda: get_speaker(), evidence=operation_evidence
        )
        self._setup_routes()

    def _setup_routes(self):
        """Setup API routes"""
        self.app.router.add_post("/api/play/text", self.handle_play_text)
        self.app.router.add_post("/api/play/url", self.handle_play_url)
        self.app.router.add_post("/api/play/file", self.handle_play_file)
        self.app.router.add_get("/api/status", self.handle_get_status)
        self.app.router.add_post("/api/wakeup", self.handle_wakeup)
        self.app.router.add_post("/api/interrupt", self.handle_stop)
        self.app.router.add_get("/api/health", self.handle_health)
        # TTS endpoints
        self.app.router.add_post("/api/tts/doubao", self.handle_tts_doubao)
        self.app.router.add_get("/api/tts/doubao_voices", self.handle_tts_voices)
        self.app.router.add_post("/api/xiaoai/command", self.handle_xiaoai_command)
        self.app.router.add_post(
            "/api/xiaoai/{operation:announce|status|interrupt|wakeup}", self.handle_xiaoai_control
        )
        self.app.router.add_get("/api/xiaoai/operations", self.handle_xiaoai_operations)
        self.app.router.add_get(
            "/api/xiaoai/operations/{request_id}", self.handle_xiaoai_operation
        )
        self.app.router.add_delete(
            "/api/xiaoai/operations/{request_id}", self.handle_delete_xiaoai_operation
        )

    def _create_background_task(
        self,
        coro: Coroutine[Any, Any, Any],
        name: str,
    ) -> asyncio.Task:
        """Create a background task and log any unhandled exception."""
        task = asyncio.create_task(coro)

        def _log_task_result(done_task: asyncio.Task):
            try:
                done_task.result()
            except Exception as exc:
                logger.error(f"[APIServer] Background task failed ({name}): {exc}")

        task.add_done_callback(_log_task_result)
        return task

    async def start(self):
        """Start the HTTP server"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        logger.info(f"[APIServer] HTTP server started at http://{self.host}:{self.port}")

    async def stop(self):
        """Stop the HTTP server"""
        self._cancel_announcements()
        if self.runner:
            await self.runner.cleanup()
            logger.info("[APIServer] HTTP server stopped")

    # ============ Handlers ============

    async def handle_xiaoai_command(self, request: web.Request) -> web.Response:
        """经受控接口提交一条自然语言原生指令，并返回持久化记录。"""
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)
        if not isinstance(data, dict):
            return web.json_response(
                {"success": False, "error": "JSON body must be an object"}, status=400
            )

        try:
            record = await self.native_bridge.submit(
                data.get("command", ""),
                session_key=data.get("session_key", "agent:xiaoai-doubao:default"),
                request_id=data.get("request_id"),
                silent=data.get("silent", True),
                timeout_ms=data.get("timeout_ms", 10000),
            )
        except ValueError as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception as exc:
            logger.error(f"[APIServer] Native XiaoAI command failed: {type(exc).__name__}")
            return web.json_response(
                {"success": False, "error": type(exc).__name__}, status=500
            )

        status = 202 if record.status == "accepted" else 200
        return web.json_response(
            {"success": record.status == "accepted", "operation": record.as_dict()},
            status=status,
        )

    async def handle_xiaoai_control(self, request: web.Request) -> web.Response:
        """仅接受已注册的播报、状态、打断和调度唤醒动作，并记录实际结果。"""
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            action = request.match_info["operation"]
            command = data.get("text", "") if action == "announce" else action
            parameters: dict[str, Any] = {}
            if action == "announce":
                if not isinstance(command, str) or not command.strip() or len(command) > 1000:
                    raise ValueError("text 必须是 1 到 1000 字的非空文本")
                speed = data.get("speed", 1.0)
                if type(speed) not in (int, float) or not 0.8 <= speed <= 2.0:
                    raise ValueError("speed 必须在 0.8 到 2.0 之间")
                speaker_id = data.get("speaker_id")
                if speaker_id is not None and (not isinstance(speaker_id, str) or len(speaker_id) > 128):
                    raise ValueError("speaker_id 无效")
                # 参数白名单不会把工具请求中的凭据或其他字段写入操作记录。
                voice = VoiceDirective.from_mapping(data)
                parameters = {
                    "speaker_id": speaker_id, "speed": speed,
                    "context_texts": voice.context_texts() if voice else None,
                    "emotion": voice.emotion if voice else None,
                    "explicit_language": voice.explicit_language if voice else None,
                    "explicit_dialect": voice.explicit_dialect if voice else None,
                }
            elif action == "wakeup":
                if type(data.get("silent", True)) is not bool:
                    raise ValueError("silent 必须是布尔值")
                parameters = {"silent": data.get("silent", True)}

            async def invoke() -> OperationOutcome:
                speaker = get_speaker()
                if speaker is None:
                    return OperationOutcome("rejected", False, error="speaker_unavailable", confirmation="rejected")
                if action == "status":
                    playing = await speaker.get_playing()
                    return OperationOutcome("completed", True, {"status": playing, "cached": True}, confirmation="cached_status")
                if action == "interrupt":
                    self._cancel_announcements()
                    await speaker.stop_device_audio()
                    xiaoai = get_xiaoai()
                    if xiaoai and hasattr(xiaoai, "stop_conversation"):
                        xiaoai.stop_conversation()
                    return OperationOutcome("accepted", True, {"control_returned": True}, confirmation="accepted_only")
                if action == "wakeup":
                    accepted = await speaker.wake_up(awake=True, silent=parameters["silent"])
                    return OperationOutcome(
                        "accepted" if accepted else "unknown", True if accepted else None,
                        {"control_returned": accepted}, confirmation="accepted_only" if accepted else "unknown",
                    )
                await self._announce_xiaoai(command, parameters)
                return OperationOutcome("accepted", True, {"playback_call_returned": True}, confirmation="accepted_only")

            record = await self.native_bridge.run_operation(
                f"xiaoai_{action}", command, parameters, invoke,
                session_key=data.get("session_key", "agent:xiaoai-doubao:default"),
                request_id=data.get("request_id"), timeout_ms=data.get("timeout_ms", 10000),
            )
        except (ValueError, json.JSONDecodeError) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception as exc:
            logger.error(f"[APIServer] Controlled operation failed: {type(exc).__name__}")
            return web.json_response({"success": False, "error": type(exc).__name__}, status=500)
        return web.json_response(
            {"success": record.status in ("accepted", "completed"), "operation": record.as_dict()},
            status=202 if record.status == "accepted" else 200,
        )

    async def _announce_xiaoai(self, text: str, parameters: dict[str, Any]) -> None:
        """使用服务端豆包凭据进行阻塞播报，不接受 Agent 提供凭据或文件路径。"""
        config = self.config.get_app_config("tts.doubao", {})
        if not config.get("app_id") or not config.get("access_key"):
            raise ValueError("Doubao TTS credentials not configured")
        speaker_id = parameters["speaker_id"] or config.get("default_speaker", "zh_female_vv_uranus_bigtts")
        tts = DoubaoTTS(app_id=config["app_id"], access_key=config["access_key"],
                        resource_id=config.get("resource_id"), speaker=speaker_id)
        play = open_xiaoai_server.tts_stream_play if config.get("stream", False) else open_xiaoai_server.tts_play
        self._cancel_announcements()
        token = open_xiaoai_server.begin_playback_session()
        self._announcement_tokens.add(token)
        try:
            await play(
                text, app_id=config["app_id"], access_key=config["access_key"], resource_id=tts.resource_id,
                speaker=speaker_id, speed=parameters["speed"], format=tts.resolve_audio_format(text), sample_rate=24000,
                emotion=parameters["emotion"], context_texts=parameters["context_texts"], playback_token=token,
                explicit_language=parameters["explicit_language"], explicit_dialect=parameters["explicit_dialect"],
            )
            if token not in self._announcement_tokens:
                raise RuntimeError("announcement_interrupted")
        finally:
            # 只取消自己持有的令牌，不能停止后来开始的其他播报。
            open_xiaoai_server.stop_tts_playback(token)
            self._announcement_tokens.discard(token)

    def _cancel_announcements(self) -> None:
        """使受控播报令牌失效，原生异步合成即使迟到也不能继续发送音频。"""
        for token in self._announcement_tokens:
            open_xiaoai_server.stop_tts_playback(token)
        self._announcement_tokens.clear()

    async def handle_xiaoai_operations(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "50"))
        except ValueError:
            return web.json_response({"success": False, "error": "limit 无效"}, status=400)
        records = await self.native_bridge.list(
            limit=limit, session_key=request.query.get("session_key")
        )
        return web.json_response(
            {"success": True, "operations": [record.as_dict() for record in records]}
        )

    async def handle_xiaoai_operation(self, request: web.Request) -> web.Response:
        record = await self.native_bridge.get(request.match_info["request_id"])
        if record is None:
            return web.json_response({"success": False, "error": "operation_not_found"}, status=404)
        return web.json_response({"success": True, "operation": record.as_dict()})

    async def handle_delete_xiaoai_operation(self, request: web.Request) -> web.Response:
        deleted = await self.native_bridge.delete(request.match_info["request_id"])
        if not deleted:
            return web.json_response({"success": False, "error": "operation_not_found"}, status=404)
        return web.json_response({"success": True, "deleted": True})

    async def handle_play_text(self, request: web.Request) -> web.Response:
        """
        POST /api/play/text
        Play text via TTS

        Request body:
            {
                "text": "你好",           # required
                "blocking": false,        # optional, default false
                "timeout": 60000          # optional, timeout in ms
            }
        """
        try:
            data = await request.json()
            text = data.get("text")

            if not text:
                return web.json_response(
                    {"success": False, "error": "Missing required field: text"},
                    status=400
                )

            blocking = data.get("blocking", False)
            timeout = data.get("timeout", 10 * 60 * 1000)

            speaker = get_speaker()
            if not speaker:
                return web.json_response(
                    {"success": False, "error": "Speaker not initialized"},
                    status=503
                )

            # Run in background to not block the response
            if blocking:
                result = await speaker.play(text=text, blocking=True, timeout=timeout)
                return web.json_response({"success": result})
            else:
                asyncio.create_task(speaker.play(text=text, blocking=False, timeout=timeout))
                return web.json_response({"success": True, "message": "Playing text in background"})

        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            logger.error(f"[APIServer] Error playing text: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def handle_play_url(self, request: web.Request) -> web.Response:
        """
        POST /api/play/url
        Play audio from URL

        Request body:
            {
                "url": "http://example.com/audio.mp3",  # required
                "blocking": false,                       # optional, default false
                "timeout": 60000                         # optional, timeout in ms
            }
        """
        try:
            data = await request.json()
            url = data.get("url")

            if not url:
                return web.json_response(
                    {"success": False, "error": "Missing required field: url"},
                    status=400
                )

            blocking = data.get("blocking", False)
            timeout = data.get("timeout", 10 * 60 * 1000)

            speaker = get_speaker()
            if not speaker:
                return web.json_response(
                    {"success": False, "error": "Speaker not initialized"},
                    status=503
                )

            if blocking:
                result = await speaker.play(url=url, blocking=True, timeout=timeout)
                return web.json_response({"success": result})
            else:
                asyncio.create_task(speaker.play(url=url, blocking=False, timeout=timeout))
                return web.json_response({"success": True, "message": "Playing URL in background"})

        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            logger.error(f"[APIServer] Error playing URL: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def handle_play_file(self, request: web.Request) -> web.Response:
        """
        POST /api/play/file
        Upload and play audio file directly via audio buffer

        Request: multipart/form-data
            - file: audio file (required, mp3/wav/opus etc.)

        Query params:
            - blocking: true/false (optional, default false)
            - sample_rate: target sample rate in Hz (optional, default 24000, can be 48000, 44100, etc.)

        Response:
            {
                "success": true,
                "message": "File played"
            }
        """
        try:
            # Parse query params
            blocking = request.query.get("blocking", "false").lower() == "true"
            sample_rate = int(request.query.get("sample_rate", "24000"))

            reader = await request.multipart()

            # Get the file field
            field = await reader.next()
            if not isinstance(field, BodyPartReader) or field.name != "file":
                return web.json_response(
                    {"success": False, "error": "Missing required field: file"},
                    status=400
                )

            # Check filename
            filename = field.filename
            if not filename:
                return web.json_response(
                    {"success": False, "error": "No filename provided"},
                    status=400
                )

            speaker = get_speaker()
            if not speaker:
                return web.json_response(
                    {"success": False, "error": "Speaker not initialized"},
                    status=503
                )

            suffix = os.path.splitext(filename)[1] or ".mp3"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                total_size = 0
                while True:
                    chunk = await field.read_chunk(size=8192)
                    if not chunk:
                        break
                    temp_file.write(chunk)
                    total_size += len(chunk)
                temp_path = temp_file.name

            logger.info(f"[APIServer] Received file: {filename}, size: {total_size} bytes, blocking={blocking}, sample_rate={sample_rate}")
            logger.info(f"[APIServer] Saved upload to temp file: {temp_path}")

            async def play_audio():
                try:
                    success = await speaker.play_server_file(
                        temp_path,
                        blocking=True,
                        sample_rate=sample_rate,
                    )
                    if success:
                        logger.info(f"[APIServer] Finished playing: {filename}")
                    else:
                        logger.error(f"[APIServer] Error playing file: {filename}")
                    return success
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)

            if blocking:
                # Wait for playback to complete
                success = await play_audio()
                return web.json_response({
                    "success": success,
                    "message": f"Finished playing: {filename}",
                    "filename": filename,
                    "size": total_size,
                    "sample_rate": sample_rate
                })
            else:
                # Run in background
                asyncio.create_task(play_audio())
                return web.json_response({
                    "success": True,
                    "message": f"Playing file: {filename}",
                    "filename": filename,
                    "size": total_size,
                    "sample_rate": sample_rate
                })

        except Exception as e:
            logger.error(f"[APIServer] Error handling file upload: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def handle_get_status(self, request: web.Request) -> web.Response:
        """
        GET /api/status
        Get current speaker status
        """
        try:
            speaker = get_speaker()

            if not speaker:
                return web.json_response(
                    {"success": False, "error": "Speaker not initialized"},
                    status=503
                )

            status = await speaker.get_playing()

            return web.json_response({
                "success": True,
                "data": {
                    "status": status  # "playing", "paused", "idle"
                }
            })

        except Exception as e:
            logger.error(f"[APIServer] Error getting status: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def handle_wakeup(self, request: web.Request) -> web.Response:
        """
        POST /api/wakeup
        Wake up the speaker

        Request body:
            {
                "silent": false   # optional, default false (audible wakeup)
            }
        """
        try:
            data = await request.json() if request.can_read_body else {}
            silent = data.get("silent", False)

            speaker = get_speaker()
            if not speaker:
                return web.json_response(
                    {"success": False, "error": "Speaker not initialized"},
                    status=503
                )

            result = await speaker.wake_up(awake=True, silent=silent)
            return web.json_response({"success": result})

        except Exception as e:
            logger.error(f"[APIServer] Error waking up: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def handle_stop(self, request: web.Request) -> web.Response:
        """
        POST /api/interrupt
        Interrupt current playback
        """
        try:
            speaker = get_speaker()
            xiaoai = get_xiaoai()
            if not speaker:
                return web.json_response(
                    {"success": False, "error": "Speaker not initialized"},
                    status=503
                )

            await speaker.stop_device_audio()
            # 停止连续对话
            if xiaoai:
                xiaoai.stop_conversation()

            return web.json_response({"success": True})

        except Exception as e:
            logger.error(f"[APIServer] Error interrupting: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def handle_health(self, request: web.Request) -> web.Response:
        """
        GET /api/health
        Health check endpoint
        """
        return web.json_response({
            "success": True,
            "data": {
                "status": "healthy",
                "speaker_ready": get_speaker() is not None
            }
        })

    async def handle_tts_doubao(self, request: web.Request) -> web.Response:
        """
        POST /api/tts/doubao
        Synthesize text using Doubao (ByteDance Volcano) TTS and play it

        Request body:
            {
                "text": "你好",                    # required
                "app_id": "your_app_id",           # optional (uses config if not provided)
                "access_key": "your_access_key",   # optional (uses config if not provided)
                "resource_id": "your_resource_id", # optional (auto-detected based on speaker_id)
                "speaker_id": "zh_female_cancan_mars_bigtts",  # optional, default voice
                "speed": 1.0,                       # optional, 0.8-2.0
                "blocking": true,                   # optional, default false
                "emotion": "happy",                 # optional, emotion for multi-emotion speakers
                "explicit_language": "zh",          # optional, e.g. zh or en
                "explicit_dialect": "sichuan",      # optional, supported dialect hint
                "context_texts": [                   # optional, only for 2.0 speakers (only first value effective)
                    "你可以说慢一点吗？",
                    "你可以用特别痛心的语气说话吗？",
                    "你能用骄傲的语气来说话吗？"
                ]
            }
        """
        speaker_id = "<unknown>"
        resource_id_for_log = "<unknown>"
        resolved_format = "<unknown>"
        blocking = False
        use_stream = False

        try:
            data = await request.json()
            text = data.get("text")

            if not text:
                return web.json_response(
                    {"success": False, "error": "Missing required field: text"},
                    status=400
                )

            # Get credentials from request or config
            tts_config = self.config.get_app_config("tts.doubao", {})

            app_id = data.get("app_id") or tts_config.get("app_id")
            access_key = data.get("access_key") or tts_config.get("access_key")
            # resource_id is now optional - will be auto-detected based on speaker
            resource_id = data.get("resource_id") or tts_config.get("resource_id")
            resource_id_for_log = resource_id or "<auto>"

            if not all([app_id, access_key]):
                return web.json_response(
                    {"success": False, "error": "Doubao TTS credentials not configured. Provide app_id and access_key in request or config.py"},
                    status=400
                )

            speaker_id = data.get("speaker_id") or data.get("speaker") or tts_config.get("default_speaker") or "zh_female_vv_uranus_bigtts"
            speed = float(data.get("speed", 1.0))
            blocking = data.get("blocking", False)
            voice_directive = VoiceDirective.from_mapping(data)
            context_texts = voice_directive.context_texts() if voice_directive else None
            emotion = voice_directive.emotion if voice_directive else None
            explicit_language = (
                voice_directive.explicit_language if voice_directive else None
            )
            explicit_dialect = (
                voice_directive.explicit_dialect if voice_directive else None
            )

            speaker = get_speaker()
            if not speaker:
                return web.json_response(
                    {"success": False, "error": "Speaker not initialized"},
                    status=503
                )

            # Create TTS instance (auto-detects resource_id if not provided)
            tts = DoubaoTTS(
                app_id=app_id,
                access_key=access_key,
                resource_id=resource_id,
                speaker=speaker_id,
            )
            resolved_format = tts.resolve_audio_format(text)
            resource_id_for_log = tts.resource_id
            logger.info(
                f"[APIServer] Doubao TTS: speaker={speaker_id}, resource_id={tts.resource_id}, format={resolved_format}"
            )

            use_stream = tts_config.get("stream", False)
            if use_stream:
                async def play_tts_stream():
                    play_fn = (
                        open_xiaoai_server.tts_stream_play
                        if blocking
                        else open_xiaoai_server.tts_stream_play_background
                    )
                    await play_fn(
                        text,
                        app_id=app_id,
                        access_key=access_key,
                        resource_id=tts.resource_id,
                        speaker=speaker_id,
                        speed=speed,
                        format=resolved_format,
                        sample_rate=24000,
                        emotion=emotion,
                        context_texts=context_texts,
                        explicit_language=explicit_language,
                        explicit_dialect=explicit_dialect,
                    )

                if blocking:
                    await play_tts_stream()
                else:
                    await play_tts_stream()
            else:
                async def play_tts_audio():
                    play_fn = (
                        open_xiaoai_server.tts_play
                        if blocking
                        else open_xiaoai_server.tts_play_background
                    )
                    await play_fn(
                        text,
                        app_id=app_id,
                        access_key=access_key,
                        resource_id=tts.resource_id,
                        speaker=speaker_id,
                        speed=speed,
                        format=resolved_format,
                        sample_rate=24000,
                        emotion=emotion,
                        context_texts=context_texts,
                        explicit_language=explicit_language,
                        explicit_dialect=explicit_dialect,
                    )
                    logger.debug("[APIServer] Finished playing TTS audio")

                if blocking:
                    try:
                        await play_tts_audio()
                    except Exception as e:
                        return web.json_response(
                            {"success": False, "error": f"TTS playback failed: {str(e)}"},
                            status=500
                        )
                else:
                    await play_tts_audio()

            if blocking:
                return web.json_response({
                    "success": True,
                    "message": f"TTS played: {text[:50]}..." if len(text) > 50 else f"TTS played: {text}",
                    "speaker_id": speaker_id,
                })

            return web.json_response(
                {
                    "success": True,
                    "message": "TTS request accepted for background playback",
                    "speaker_id": speaker_id,
                    "accepted": True,
                    "blocking": False,
                },
                status=202,
            )

        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            logger.error(
                f"[APIServer] Doubao TTS failed: "
                f"speaker={speaker_id}, resource_id={resource_id_for_log}, "
                f"format={resolved_format}, blocking={blocking}, stream={use_stream}, "
                f"error={type(e).__name__}: {e}"
            )
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def handle_tts_voices(self, request: web.Request) -> web.Response:
        """
        GET /api/tts/doubao_voices
        Get available TTS voices for Doubao

        Query params:
            - version: "1.0", "2.0", or "all" (optional, default shows all)
        """
        try:
            tts_config = self.config.get_app_config("tts.doubao", {})
            resource_id = tts_config.get("resource_id", "")

            # Get version from query param or auto-detect from resource_id
            version = request.query.get("version", "all")

            if version == "2.0":
                voices = DoubaoTTS.VOICES_2_0
            elif version == "1.0":
                voices = DoubaoTTS.VOICES_1_0
            else:
                voices = DoubaoTTS.list_voices()
                # Add version info for all voices
                return web.json_response({
                    "success": True,
                    "data": {
                        "provider": "doubao",
                        "resource_id": resource_id,
                        "versions": {
                            "1.0": {
                                "count": len(DoubaoTTS.VOICES_1_0),
                                "description": "豆包语音合成模型1.0",
                                "voices": DoubaoTTS.VOICES_1_0
                            },
                            "2.0": {
                                "count": len(DoubaoTTS.VOICES_2_0),
                                "description": "豆包语音合成模型2.0 - 支持情感变化、指令遵循、ASMR",
                                "voices": DoubaoTTS.VOICES_2_0
                            }
                        },
                        "total_voices": len(voices)
                    }
                })

            return web.json_response({
                "success": True,
                "data": {
                    "provider": "doubao",
                    "version": version,
                    "resource_id": resource_id,
                    "voices": voices,
                    "count": len(voices)
                }
            })
        except Exception as e:
            logger.error(f"[APIServer] Error getting voices: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

"""单轮对话可调用的受控语义工具；持续播放在反馈成功后才提交。"""

from dataclasses import dataclass, field
import json
import re
from typing import Any
import uuid

from core.services.native_bridge import NativeBridgeService, OperationOutcome


@dataclass
class ConversationTools:
    bridge: NativeBridgeService
    session_key: str
    home: Any = None
    user_text: str = ""
    _mutation_sent: bool = False
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    _calls: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = field(default_factory=dict)
    _deferred: tuple[str, str] | None = None
    _finished: bool = False

    @property
    def definitions(self) -> list[dict[str, Any]]:
        native = [{"type": "function", "function": {
            "name": "xiaoai_command",
            "description": "提交明确原生请求。short 静默执行后反馈受理情况；continuous 音乐等持续播放须等反馈结束后提交，不能宣称已完成。",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string", "maxLength": 1000},
                "playback": {"type": "string", "enum": ["short", "continuous"]},
            }, "required": ["command", "playback"], "additionalProperties": False},
        }}]
        if self.home and self.home.read_only:
            return self.home.definitions
        return native + (self.home.definitions if self.home else [])

    async def invoke(self, call_id: str, name: str, arguments: str) -> dict[str, Any]:
        args = json.loads(arguments)
        if call_id in self._calls:
            old_name, old_args, result = self._calls[call_id]
            if (old_name, old_args) != (name, args):
                raise ValueError("同一工具调用标识不能改变参数")
            return result
        if self.home and (name.startswith("ha_") or name == "device_control") and isinstance(args, dict):
            mutating = name not in ("ha_entities","ha_state","ha_services","ha_history","ha_pending","ha_rules","ha_tasks")
            if mutating and self._mutation_sent:
                raise ValueError("本轮已选择控制路径，不能再次发令")
            result = await self.home.invoke(name, args, self.session_key, self.user_text)
            if mutating:
                self._mutation_sent = True
                if isinstance(result,dict) and "message" in result:
                    self.outcomes.append(result)
            wrapped = {"data": result}
            self._calls[call_id] = (name, args, wrapped)
            return wrapped
        if name != "xiaoai_command" or not isinstance(args, dict) or set(args) != {"command", "playback"}:
            raise ValueError("仅允许已注册工具及白名单参数")
        if self.home and self.home.read_only:
            raise ValueError("home_assistant_read_only")
        command, playback = args["command"], args["playback"]
        if not isinstance(command, str) or not command.strip() or len(command) > 1000 or '\x00' in command or playback not in ("short", "continuous"):
            raise ValueError("原生指令参数无效")
        if self.home and (playback != "continuous" or not re.fullmatch(r"播放(?:音乐|歌曲|一首歌|歌单)", command.strip())):
            raise ValueError("设备操作必须使用实体绑定的device_control；自由文本仅允许播放音乐")
        if self._deferred:
            raise ValueError("持续播放已准备，本轮不能再追加操作")
        if self._mutation_sent:
            raise ValueError("本轮已有设备操作，不能换路径双发")
        self._mutation_sent = True
        request_id = uuid.uuid4().hex
        if playback == "continuous":
            self._deferred = (request_id, command)
            result = {"status": "prepared", "request_id": request_id,
                      "message": "尚未提交；反馈完成后提交原生播放请求，结果未确认"}
        else:
            record = await self.bridge.submit(command, request_id=request_id, session_key=self.session_key, silent=True)
            result = record.as_dict()
        self._calls[call_id] = (name, args, result)
        self.outcomes.append(result)
        return result

    def feedback(self, model_text: str) -> str:
        """设备完成缺乏证据时使用可信回执，不能让模型把受理改写为成功。"""
        if not self.outcomes:
            return model_text
        return "；".join(dict.fromkeys(item["message"] for item in self.outcomes))

    @property
    def has_deferred_playback(self) -> bool:
        return self._deferred is not None and not self._finished

    async def finish_feedback(self, succeeded: bool) -> bool:
        """返回是否交出音箱；取消/失败不提交准备中的播放。"""
        if self._finished:
            return False
        self._finished = True
        if not self._deferred:
            return False
        request_id, command = self._deferred
        if not succeeded:
            async def cancelled():
                return OperationOutcome("rejected", False, error="cancelled_before_submit", confirmation="not_submitted")
            await self.bridge.run_operation("xiaoai_command", command, {"silent": True}, cancelled,
                                            session_key=self.session_key, request_id=request_id)
            return False
        await self.bridge.submit(command, request_id=request_id, session_key=self.session_key, silent=True)
        # 请求已发出，即使回执未知也不恢复提示/录音去干扰可能已开始的音乐。
        return True

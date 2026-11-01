"""Hermes 推理级别命令的安全边界。

Bridge 当前接入的是 Hermes Chat Completions API。该 API 没有安全的会话
reasoning 控制端点，因此只识别用户明确的切换命令并在本地拦截，绝不把
``/reasoning`` 当普通聊天文本交给 Agent，避免模型把它解释成全局配置操作。
"""

from dataclasses import dataclass
import re


LEVELS = frozenset(("none", "minimal", "low", "medium", "high", "xhigh"))
_COMMAND_RE = re.compile(
    r"^/reasoning(?:\s+(?P<arg>none|minimal|low|medium|high|xhigh|reset|show|on|hide|off|--global))?\s*$",
    re.IGNORECASE,
)
_ZH_RE = re.compile(
    r"^(?:把|将)?(?:当前|本次)?(?:思考|推理)(?:级别|等级)"
    r"(?:切换到|切到|调到|调整到|设为|设置为)?"
    r"(?P<arg>最低|低|中|高|最高|关闭|重置)"
    r"(?:级)?(?:别|度)?[。.!！?？]?$"
)
_ZH_LEVELS = {
    "最低": "minimal",
    "低": "low",
    "中": "medium",
    "高": "high",
    "最高": "xhigh",
    "关闭": "none",
    "重置": "reset",
}


@dataclass(frozen=True)
class ReasoningCommand:
    """用户明确提出的推理级别切换请求。"""

    level: str | None
    reset: bool = False
    global_scope_requested: bool = False

    @classmethod
    def from_text(cls, text: str) -> "ReasoningCommand | None":
        """仅识别完整命令；普通问题不会被当成复杂度信号。"""
        value = str(text or "").strip()
        match = _COMMAND_RE.fullmatch(value)
        if match:
            argument = (match.group("arg") or "").lower()
            if argument == "--global":
                return cls(None, global_scope_requested=True)
            if argument in {"show", "on", "hide", "off"}:
                return None
            if argument == "reset":
                return cls(None, reset=True)
            if not argument:
                return cls(None)
            return cls(argument)

        match = _ZH_RE.fullmatch(value)
        if not match:
            return None
        argument = _ZH_LEVELS[match.group("arg")]
        return cls(None, reset=True) if argument == "reset" else cls(argument)

    def response(self) -> str:
        """解释当前旧 Hermes API 的安全限制，不伪称已切换。"""
        if self.global_scope_requested:
            return "全局推理级别不会通过小爱豆包修改；请在 Hermes 控制面明确执行 /reasoning <级别>。本次没有修改。"
        if self.level is None and not self.reset:
            return "当前 Hermes API 没有安全的会话级推理查询接口；默认级别为 low，本次没有修改。"
        if self.reset:
            target = "恢复默认 low"
        else:
            target = f"切换到 {self.level}"
        return f"当前 Hermes API 没有安全的会话级推理控制接口，无法{target}；本次没有修改。请在 Hermes 控制面明确执行 /reasoning 命令。"

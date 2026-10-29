"""在 Agent 回复与 TTS provider 之间共享并校验语音指令。"""

from dataclasses import dataclass
import re
from typing import Any


_LANGUAGE_RE = re.compile(r"^[a-z]{2,8}(?:-[a-z]{2,8})?$")
_DIALECTS = frozenset(("dongbei", "shaanxi", "sichuan"))


@dataclass(frozen=True)
class VoiceDirective:
    """单句语音控制，以及可选的当前会话阶段偏好。"""

    context_text: str | None = None
    explicit_language: str | None = None
    explicit_dialect: str | None = None
    emotion: str | None = None
    persist: bool = False

    @classmethod
    def from_mapping(cls, value: Any) -> "VoiceDirective | None":
        """解析可选模型元数据；格式异常不能阻断文字回复。"""
        if not isinstance(value, dict):
            return None

        context = value.get("context_text")
        if context is None:
            contexts = value.get("context_texts")
            context = contexts[0] if isinstance(contexts, list) and contexts else None
        if not isinstance(context, str) or not context.strip():
            context = None
        else:
            context = context.strip()[:200]

        language = value.get("explicit_language")
        if not isinstance(language, str):
            language = None
        else:
            language = language.strip().lower()
            if not _LANGUAGE_RE.fullmatch(language):
                language = None

        dialect = value.get("explicit_dialect")
        if not isinstance(dialect, str):
            dialect = None
        else:
            dialect = dialect.strip().lower()
            if dialect not in _DIALECTS:
                dialect = None
        if dialect and language not in (None, "zh"):
            dialect = None

        emotion = value.get("emotion")
        if not isinstance(emotion, str) or not emotion.strip():
            emotion = None
        else:
            emotion = emotion.strip()[:40]

        persist = value.get("persist", value.get("session_preference", False))
        persist = persist is True
        if not any((context, language, dialect, emotion)):
            return None
        return cls(context, language, dialect, emotion, persist)

    def context_texts(self) -> list[str] | None:
        return [self.context_text] if self.context_text else None

    def for_one_turn(self) -> "VoiceDirective":
        return VoiceDirective(
            self.context_text,
            self.explicit_language,
            self.explicit_dialect,
            self.emotion,
            False,
        )

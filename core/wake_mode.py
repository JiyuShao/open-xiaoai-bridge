"""统一解析唤醒模式与迁移规则；此模块不导入模型、配置或设备传输。"""

import math
import os
from typing import Any, Literal
from urllib.parse import urlsplit

from core.utils.base import get_env_bool

WakeMode = Literal["legacy", "native", "mixed"]


def resolve_wake_mode() -> WakeMode:
    configured = os.environ.get("WAKE_MODE", "").strip().lower()
    old_value = os.environ.get("NATIVE_WAKE_ENABLE", "").strip()
    old_native = get_env_bool("NATIVE_WAKE_ENABLE")
    if not configured:
        return "native" if old_native else "legacy"
    if configured not in ("legacy", "native", "mixed"):
        raise ValueError("WAKE_MODE 必须是 legacy、native 或 mixed")
    if old_value and old_native != (configured == "native"):
        raise ValueError("WAKE_MODE 与旧 NATIVE_WAKE_ENABLE 冲突，请只配置 WAKE_MODE")
    if configured == "native":
        return "native"
    if configured == "mixed":
        return "mixed"
    return "legacy"


def validate_wake_environment(mode: WakeMode | None = None) -> None:
    """在配置导入、模型加载和连接设备之前拒绝不支持的组合。"""
    mode = mode or resolve_wake_mode()
    if mode == "legacy":
        return
    if not get_env_bool("AUDIO_INPUT_ENABLE", True):
        raise ValueError("原生/混合唤醒模式需要 AUDIO_INPUT_ENABLE=1")
    backends: tuple[str, ...] = ("XIAOZHI_ENABLE", "OPENCLAW_ENABLE", "OPENCLAW_ENABLED", "QWENPAW_ENABLE")
    if mode == "native":
        backends += ("OPENAI_ENABLE", "API_SERVER_ENABLE")
    conflicts = [name for name in backends if get_env_bool(name)]
    if conflicts:
        raise ValueError(f"{mode} 唤醒模式需关闭这些开关: " + ", ".join(conflicts))
    if mode == "mixed" and not get_env_bool("OPENAI_ENABLE"):
        raise ValueError("混合唤醒模式需要 OPENAI_ENABLE=1，且只支持该对话后端")


def validate_mixed_config(config: dict[str, Any]) -> None:
    """混合模式必须声明可枚举路由和本地 ASR，不借用任意 before_wakeup 钩子。"""
    sections = [config.get(name, {}) for name in ("wakeup", "openai", "asr")]
    if not all(isinstance(section, dict) for section in sections):
        raise ValueError("混合模式的 wakeup、openai、asr 必须是字典")
    wakeup, openai, asr = sections
    keywords, routes = wakeup.get("keywords"), wakeup.get("routes")
    if not isinstance(keywords, list) or not keywords or any(
        not isinstance(word, str) or not word.strip() or word != word.strip().lower()
        for word in keywords
    ) or len(set(keywords)) != len(keywords):
        raise ValueError("混合模式需要不重复的 wakeup.keywords")
    if not isinstance(routes, dict) or set(routes) != set(keywords) or any(route not in ("native", "openai") for route in routes.values()) or not all(route in routes.values() for route in ("native", "openai")):
        raise ValueError("wakeup.routes 必须覆盖全部关键词，且只分流到 native 和 openai")
    if wakeup.get("before_wakeup") is not None:
        raise ValueError("混合模式使用 wakeup.routes，不执行 before_wakeup")
    if openai.get("profile") != "xiaoai-doubao" or not str(openai.get("session_key", "")).startswith("agent:xiaoai-doubao:"):
        raise ValueError("混合模式需要 xiaoai-doubao profile 和对应 session_key")
    if not isinstance(openai.get("history_path"), str) or not openai["history_path"].strip() or openai.get("input_mode", "local_asr") != "local_asr":
        raise ValueError("混合模式需要持久化 history_path 和 local_asr 输入")
    if asr.get("model", "sense_voice") != "sense_voice":
        raise ValueError("混合模式首版仅支持本地 sense_voice ASR")
    if not isinstance(asr.get("int8", True), bool) or not isinstance(asr.get("model_dir", ""), str):
        raise ValueError("asr.int8 必须是布尔值，model_dir 必须是字符串")
    timeout = float(wakeup.get("timeout", 20))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("wakeup.timeout 必须是有限正数")
    for field, default, convert in (
        ("response_timeout", 120, int), ("history_max_messages", 20, int),
        ("tts_speed", 1.0, float),
    ):
        value = convert(openai.get(field, default))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"openai.{field} 必须为有限正数")
    if not isinstance(openai.get("model"), str) or not openai["model"].strip():
        raise ValueError("混合模式需要非空 openai.model")
    url = urlsplit(str(openai.get("base_url", "")))
    _ = url.port  # 同步拒绝非法端口，而非等第一次请求才失败。
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("混合模式需要有效的 openai.base_url，凭据须单独配置")

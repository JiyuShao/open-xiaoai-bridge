"""混合唤醒示例；启用前在私有环境设置 Hermes/TTS 凭据和音箱 Token。"""

import json
import os


def _home_entities():
    """读取私有实体白名单；默认不启用真实 HA。"""
    raw = os.environ.get("HA_ENTITIES_JSON", "").strip()
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("HA_ENTITIES_JSON 必须是实体白名单 JSON 对象")
    return value


_TRUE_VALUES = ("1", "true", "yes", "on")
_ha_enabled = os.environ.get("HA_ENABLE", "0").strip().lower() in _TRUE_VALUES
_ha_read_only = os.environ.get("HA_READ_ONLY", "1").strip().lower() in _TRUE_VALUES
_default_hermes_profile = "xiaoai-doubao-readonly" if _ha_enabled and _ha_read_only else "xiaoai-doubao"
_hermes_profile = os.environ.get("HERMES_PROFILE", _default_hermes_profile).strip()
if _hermes_profile != _default_hermes_profile:
    raise ValueError("HERMES_PROFILE 必须与 HA_ENABLE/HA_READ_ONLY 的本地工具边界一致")
_default_session_key = (
    "agent:xiaoai-doubao:readonly"
    if _ha_enabled and _ha_read_only
    else "agent:xiaoai-doubao:default"
)
_session_key = os.environ.get("HERMES_SESSION_KEY", _default_session_key).strip()
if not _session_key.startswith("agent:xiaoai-doubao:"):
    raise ValueError("HERMES_SESSION_KEY 必须使用 xiaoai-doubao 的 agent 前缀")
_system_prompt = (
    "你是小爱豆包只读助手。只能读取 Home Assistant 的实体、状态、历史和服务列表；"
    "不要控制设备、创建规则、创建定时任务、调用音频或任何写入能力。"
    if _ha_enabled and _ha_read_only
    else "你是小爱豆包。亲切、自然、简洁地回答；原生操作只确认受理时不要声称业务完成。"
)

APP_CONFIG = {
    "wakeup": {
        "keywords": ["理想同学", "豆包豆包"],
        "routes": {"理想同学": "native", "豆包豆包": "openai"},
        "timeout": 20,
    },
    "native_wake": {"cooldown_seconds": 3.0, "timeout_seconds": 5.0},
    "xiaoai": {"continuous_conversation_mode": False},
    "asr": {"model": "sense_voice", "int8": True},
    "openai": {
        "profile": _hermes_profile,
        "tools_enabled": True,
        "display_name": "小爱豆包",
        "base_url": os.environ.get("HERMES_BASE_URL", ""),
        "api_key": os.environ.get("HERMES_API_KEY", ""),
        "model": os.environ.get("HERMES_MODEL", "hermes"),
        "input_mode": "local_asr",
        "session_key": _session_key,
        # Hermes API 的 profile 是服务端全局活动配置，OpenAI 兼容请求
        # 不支持按请求切换 profile；这里的 profile 仅约束本地工具合同。
        # session_key 只隔离服务端记忆作用域，不能替代 profile 权限隔离。
        "session_header": "X-Hermes-Session-Key",
        "system_prompt": _system_prompt,
        "history_path": "app/xiaoai/chat-sessions.sqlite3",
        "history_max_messages": 20,
        "response_timeout": 120,
        "tts_provider": "doubao",
        "tts_speaker": "zh_female_vv_uranus_bigtts",
        "tts_speed": 1.0,
        "exit_keywords": ["退出", "再见", "拜拜"],
    },
    "tts": {"doubao": {
        "app_id": os.environ.get("DOUBAO_TTS_APP_ID", ""),
        "access_key": os.environ.get("DOUBAO_TTS_ACCESS_KEY", ""),
        "default_speaker": "zh_female_vv_uranus_bigtts",
    }},
    "home_assistant": {
        "enabled": _ha_enabled,
        "read_only": _ha_read_only,
        "base_url": os.environ.get("HA_BASE_URL", ""),
        "token": os.environ.get("HA_TOKEN", ""),
        "entities": _home_entities(),
    },
}

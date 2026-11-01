"""混合唤醒示例；启用前在私有环境设置 Hermes/TTS 凭据和音箱 Token。"""

import json
import os


def _home_entities():
    """读取可选实体元数据；HA_ALLOW_ALL=1 时实体清单可为空。"""
    raw = os.environ.get("HA_ENTITIES_JSON", "").strip()
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("HA_ENTITIES_JSON 必须是实体白名单 JSON 对象")
    return value


_TRUE_VALUES = ("1", "true", "yes", "on")
_led_feedback_enabled = os.environ.get("HERMES_LED_FEEDBACK_ENABLE", "1").strip().lower() in _TRUE_VALUES
_led_feedback_pattern = os.environ.get("HERMES_LED_FEEDBACK_PATTERN", "2").strip() or "2"
_led_feedback_duration = os.environ.get("HERMES_LED_FEEDBACK_DURATION", "1.0").strip() or "1.0"
_led_feedback_refresh = os.environ.get("HERMES_LED_FEEDBACK_REFRESH_SECONDS", "0.8").strip() or "0.8"
_barge_in_enabled = os.environ.get("BARGE_IN_ENABLE", "0").strip().lower() in _TRUE_VALUES
_barge_in_echo_reference = os.environ.get("BARGE_IN_ECHO_REFERENCE", "0").strip().lower() in _TRUE_VALUES
_ha_enabled = os.environ.get("HA_ENABLE", "0").strip().lower() in _TRUE_VALUES
_ha_read_only = os.environ.get("HA_READ_ONLY", "1").strip().lower() in _TRUE_VALUES
_ha_allow_all = os.environ.get("HA_ALLOW_ALL", "0").strip().lower() in _TRUE_VALUES
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
    "你是小爱豆包。本次调试的Home Assistant接口保持只读，读取实体、状态、历史和服务列表；"
    "HA控制、规则和定时任务等待单独启用，其他Hermes能力沿用现有配置。"
    if _ha_enabled and _ha_read_only
    else (
        "你是小爱豆包。亲切、自然、简洁地回答；原生操作只确认受理时不要声称业务完成。"
        + ("当前 Home Assistant 已启用完全服务模式：可读取任意实体，并调用任意已注册 domain/service；"
           "不要把 HA 服务调用改写成原生小爱请求。" if _ha_enabled and _ha_allow_all else "")
    )
)
_system_prompt += (
    "你运行在现有 Hermes 中，保留原有工具、skills、人格和记忆能力。"
    "调用音箱或HA时先加载xiaoai-bridge skill，通过其中的HTTP客户端执行。"
    "当前语音通道的session标识是" + _session_key + "，工具调用使用同一标识。"
    "普通回复由Bridge自动播报，不调用播报工具重复朗读。"
    "Hermes默认推理级别为low，不要根据问题复杂度自行切换；只有用户在Hermes控制面明确使用/reasoning命令时才改变当前session。"
    '最终回复使用JSON对象{"reply":"给用户的口语回复","voice":{}}，没有声音偏好时voice为空对象；'
    "voice可包含context_text、emotion、explicit_language、explicit_dialect、speed和布尔session_preference。"
    "speed是0.5到2.0之间的倍速；用户说快一点或慢一点时按当前要求输出。"
    "explicit_dialect使用dongbei、sichuan、shaanxi、yue、beijing、henan、tianjin或shanghai；"
    "用户说东北话、四川话、陕西话、粤语、北京话、河南话、天津话或上海话时映射到对应枚举。"
    "这些声音偏好只影响当前会话阶段。"
    "阶段声音偏好默认延续：用户说接下来、以后或持续使用某种声音时session_preference=true；"
    "用户明确说仅这一次或只读这一句时session_preference=false，不改变阶段偏好。"
    "范围字段省略时按阶段偏好处理；后续没有新要求时voice为空对象，由Bridge沿用已保存偏好。"
    "恢复普通话时输出explicit_language=zh-cn且session_preference=true，不再输出explicit_dialect。"
    "Vivi 2.0的情绪通过context_text自然语言指令控制，不能仅给emotion标签。"
    "例如平静时写轻柔平稳、语调舒缓；开心时写带笑意、轻快有活力、句末略上扬；保持当前音量。"
)
_tts_speaker = os.environ.get("DOUBAO_TTS_SPEAKER", "zh_female_vv_uranus_bigtts")
_tts_speed = float(os.environ.get("DOUBAO_TTS_SPEED", "1.0"))
# Hermes 的默认推理级别由 Hermes Agent 配置管理。Bridge 不把这个值
# 当作请求覆盖发送；这里仅把通道合同写清楚，避免 Bridge 自己按问题
# 复杂度做判断。当前生产 Hermes 配置应保持 agent.reasoning_effort=low。
_hermes_reasoning_default = "low"
_chat_streaming = os.environ.get("HERMES_CHAT_STREAMING_ENABLE", "0").strip().lower() in _TRUE_VALUES

APP_CONFIG = {
    "wakeup": {
        "keywords": ["理想同学", "豆包豆包"],
        "routes": {"理想同学": "native", "豆包豆包": "openai"},
        "timeout": 20,
        # The LED feedback path calls the vendor show_led/shut_led scripts on
        # the device. It does not send pnshelper wake events and therefore
        # does not enter the native XiaoAI listening state.
        "feedback": {
            "led": _led_feedback_enabled,
            "pattern": _led_feedback_pattern,
            "duration_seconds": _led_feedback_duration,
            "refresh_seconds": _led_feedback_refresh,
        },
        # Custom wake during TTS requires a real playback echo reference.
        # LX06 currently has none, so this remains disabled by default.
        "barge_in": {
            "enabled": _barge_in_enabled,
            "echo_reference": _barge_in_echo_reference,
        },
    },
    "native_wake": {"cooldown_seconds": 3.0, "timeout_seconds": 5.0},
    "xiaoai": {"continuous_conversation_mode": False},
    "asr": {"model": "sense_voice", "int8": True},
    # 混合唤醒不能回落到 config.py 的非流式 MP3 默认值；
    # PCM + 流式输出让 TTS 收到首块音频就开始播放。
    "vad": {
        "threshold": 0.10,
        "min_speech_duration": 250,
        "min_silence_duration": 500,
    },
    "openai": {
        "profile": _hermes_profile,
        "protocol": "hermes_native",
        "tools_enabled": True,
        "display_name": "小爱豆包",
        "base_url": os.environ.get("HERMES_BASE_URL", ""),
        "api_key": os.environ.get("HERMES_API_KEY", ""),
        "model": os.environ.get("HERMES_MODEL", "hermes"),
        "reasoning_effort": _hermes_reasoning_default,
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
        "chat_streaming": _chat_streaming,
        "tts_provider": "doubao",
        "tts_speaker": _tts_speaker,
        "tts_speed": _tts_speed,
        "exit_keywords": ["退出", "再见", "拜拜"],
    },
    "tts": {"doubao": {
        "api_key": os.environ.get("DOUBAO_TTS_API_KEY", ""),
        "default_speaker": _tts_speaker,
        "audio_format": "pcm",
        "stream": True,
    }},
    "home_assistant": {
        "enabled": _ha_enabled,
        "read_only": _ha_read_only,
        "allow_all": _ha_allow_all,
        "base_url": os.environ.get("HA_BASE_URL", ""),
        "token": os.environ.get("HA_TOKEN", ""),
        "entities": _home_entities(),
    },
}

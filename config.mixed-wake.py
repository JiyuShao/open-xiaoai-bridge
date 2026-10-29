"""混合唤醒示例；启用前在私有环境设置 Hermes/TTS 凭据和音箱 Token。"""

import os

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
        "profile": "xiaoai-doubao",
        "tools_enabled": True,
        "display_name": "小爱豆包",
        "base_url": os.environ.get("HERMES_BASE_URL", ""),
        "api_key": os.environ.get("HERMES_API_KEY", ""),
        "model": os.environ.get("HERMES_MODEL", "hermes"),
        "input_mode": "local_asr",
        "session_key": "agent:xiaoai-doubao:default",
        "session_header": "X-Hermes-Session-Key",
        "system_prompt": "你是小爱豆包。亲切、自然、简洁地回答；原生操作只确认受理时不要声称业务完成。",
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
}

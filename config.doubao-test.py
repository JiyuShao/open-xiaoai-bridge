"""小爱豆包的静默文字测试配置；不启动 Bridge 或加载音箱。"""

APP_CONFIG = {
    "openai": {
        "profile": "xiaoai-doubao",
        "display_name": "小爱豆包",
        "base_url": "http://127.0.0.1:0/v1",
        "api_key": "",
        "model": "hermes",
        "profile_header": "X-Hermes-Profile",
        "session_key": "agent:xiaoai-doubao:test",
        "session_header": "X-Hermes-Session-Key",
        "system_prompt": "你是小爱豆包。请亲切、自然、简洁地回答。",
        "history_max_messages": 20,
        "response_timeout": 2,
        "chat_streaming": False,
        "tts_provider": "doubao",
        "tts_speaker": "zh_female_vv_uranus_bigtts",
        "tts_speed": 1.0,
        "tts_output_mode": "record",
    },
}

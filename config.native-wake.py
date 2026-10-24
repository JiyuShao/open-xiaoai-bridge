"""原生唤醒配置，通过 CONFIG_PATH 显式选择；独立于上游 config.py。"""

APP_CONFIG = {
    "wakeup": {"keywords": ["你好管家"]},
    "native_wake": {
        "cooldown_seconds": 3.0,
        "timeout_seconds": 5.0,
    },
    "xiaoai": {
        "continuous_conversation_mode": False,
        "exit_command_keywords": [],
        "continuous_conversation_keywords": [],
        "max_listening_retries": 0,
        "exit_prompt": "",
    },
}

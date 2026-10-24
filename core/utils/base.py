import json
import os
import random


def get_env(key: str, default_value: str | None = None):
    return os.environ.get(key, default_value)


def get_env_bool(key: str, default: bool = False, *, alias: str | None = None) -> bool:
    """统一解析启动开关，接受大小写、首尾空白及 Rust 支持的 on。"""
    value = os.environ.get(key)
    if not value and alias is not None:
        value = os.environ.get(alias)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


def to_set(data):
    if isinstance(data, list):
        return list(set(data))
    return data


def pick_one(data: list):
    if len(data) == 0:
        return None
    return data[random.randint(0, len(data) - 1)]


def json_encode(obj, pretty=False):
    try:
        return json.dumps(obj, ensure_ascii=False, indent=4 if pretty else None)
    except Exception as _:
        return None


def json_decode(text):
    try:
        return json.loads(text)
    except Exception:
        return None

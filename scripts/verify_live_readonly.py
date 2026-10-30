"""验证真实 Hermes 与 Home Assistant 的只读连接；不调用设备、规则或播报接口。"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any
from urllib.parse import urlsplit

import aiohttp


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _normalize_hermes_url(value: str) -> tuple[str, str]:
    """允许根地址或 /v1 前缀，拒绝凭据、查询和隐藏路径。"""
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("HERMES_BASE_URL 必须是 HTTP(S) 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("HERMES_BASE_URL 不得包含凭据、查询参数或片段")
    _ = parsed.port
    path = parsed.path.rstrip("/")
    if path not in ("", "/v1"):
        raise ValueError("HERMES_BASE_URL 只允许根地址或 /v1 前缀")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin, origin + "/v1"


async def _hermes(base_url: str, api_key: str, model: str, chat_text: str | None) -> dict[str, Any]:
    root_base, api_base = _normalize_hermes_url(base_url)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=_headers(api_key)) as session:
        result: dict[str, Any] = {}
        for name, url in (("health", root_base + "/health"), ("models", api_base + "/models")):
            async with session.get(url) as response:
                result[name] = {"status": response.status, "ok": 200 <= response.status < 300}
                if response.status >= 400:
                    raise RuntimeError(f"Hermes {name} HTTP {response.status}")
        if chat_text is not None:
            payload = {
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": "这是只读连通性测试。只回复测试结果，不调用工具、不执行任何操作。"},
                    {"role": "user", "content": chat_text},
                ],
                "tools": [],
            }
            headers = {**_headers(api_key), "Content-Type": "application/json", "X-Hermes-Session-Key": "agent:xiaoai-doubao:readonly"}
            async with session.post(api_base + "/chat/completions", json=payload, headers=headers) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"Hermes chat HTTP {response.status}")
                choices = body.get("choices") if isinstance(body, dict) else None
                result["chat"] = {"status": response.status, "has_reply": bool(choices)}
        return result


async def _home_assistant(
    base_url: str,
    token: str,
    entity_ids: list[str],
    history_entity: str | None = None,
) -> dict[str, Any]:
    from core.services.home_assistant import HomeAssistantClient

    client = HomeAssistantClient(base_url, token)
    states = await client.states()
    services = await client.services()
    if not isinstance(states, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("entity_id"), str)
        for item in states
    ):
        raise RuntimeError("invalid_ha_states_response")
    if not isinstance(services, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("domain"), str)
        or not isinstance(item.get("services"), dict)
        for item in services
    ):
        raise RuntimeError("invalid_ha_services_response")
    result: dict[str, Any] = {
        "entities": len(states),
        "service_domains": len(services),
        "writes_attempted": 0,
    }
    if entity_ids:
        selected = []
        for entity_id in entity_ids:
            state = await client.state(entity_id)
            if not isinstance(state, dict) or state.get("entity_id") != entity_id:
                raise RuntimeError("ha_state_entity_mismatch")
            selected.append({
                "entity_id": state.get("entity_id"),
                "state": state.get("state"),
                "last_changed": state.get("last_changed"),
            })
        result["selected_states"] = selected
    if history_entity:
        now = datetime.now(timezone.utc)
        history = await client.history(
            history_entity,
            (now - timedelta(days=1)).isoformat(),
            now.isoformat(),
        )
        if not isinstance(history, list) or any(
            not isinstance(series, list)
            or any(
                not isinstance(item, dict)
                or item.get("entity_id") != history_entity
                for item in series
            )
            for series in history
        ):
            raise RuntimeError("invalid_ha_history_response")
        result["history_checked"] = True
        result["history_records"] = sum(len(series) for series in history)
    else:
        result["history_checked"] = False
    return result


async def run(args: argparse.Namespace) -> dict[str, Any]:
    hermes_base = os.environ.get("HERMES_BASE_URL", "").strip()
    home_base = os.environ.get("HA_BASE_URL", "").strip()
    if not hermes_base or not home_base:
        raise ValueError("需要设置 HERMES_BASE_URL 和 HA_BASE_URL；Token 只从运行时环境读取")
    entity_ids = [item.strip() for item in os.environ.get("HA_ENTITY_IDS", "").split(",") if item.strip()]
    history_entity = os.environ.get("HA_HISTORY_ENTITY", "").strip() or None
    result = {
        "evidence": "live-readonly",
        "hermes": await _hermes(
            hermes_base,
            os.environ.get("HERMES_API_KEY", ""),
            os.environ.get("HERMES_MODEL", "hermes"),
            args.chat_text,
        ),
        "home_assistant": await _home_assistant(
            home_base, os.environ.get("HA_TOKEN", ""), entity_ids, history_entity
        ),
        "writes_attempted": 0,
        "audio_played": False,
        "limitations": [
            "只验证真实服务读取与可选无工具聊天；不调用HA写接口、不调用Bridge、不播放音频",
            "设置 HA_HISTORY_ENTITY 后才会检查该实体过去24小时历史；未设置时不对历史作结论",
        ],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-text", help="可选的真实Hermes无工具连通性消息")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"evidence": "live-readonly", "success": False, "error": type(exc).__name__}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

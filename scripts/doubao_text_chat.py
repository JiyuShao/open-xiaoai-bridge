"""静默文字闭环：仅连接本进程的 Mock，不启动 Bridge 或真实 Agent。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys

from aiohttp import web

from core.utils.logger import logger


async def run(args: argparse.Namespace) -> int:
    """经现有对话通道发送文字，结果仅输出到测试日志。"""
    os.environ["CONFIG_PATH"] = str(
        Path(__file__).resolve().parents[1] / "config.doubao-test.py"
    )
    from core.openai import OpenAIManager
    from core.utils.config import ConfigManager

    received = []

    async def respond(request: web.Request) -> web.Response:
        body = await request.json()
        received.append(body)
        body["session_key"] = request.headers.get("X-Hermes-Session-Key")
        if args.mock_failure == "http-error":
            return web.json_response({"error": "模拟服务错误"}, status=503)
        if args.mock_failure == "timeout":
            await asyncio.sleep(10)
        return web.json_response({"choices": [{"message": {
            "content": "" if args.mock_failure == "empty" else
            "模拟回复：" + body["messages"][-1]["content"],
        }}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    runner = web.AppRunner(app, shutdown_timeout=0.1)
    await runner.setup()
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        await web.SockSite(runner, listener).start()
        config = ConfigManager.instance().get_app_config("openai")
        config["base_url"] = f"http://127.0.0.1:{listener.getsockname()[1]}/v1"
        config["history_path"] = str(args.state)
        OpenAIManager.initialize_from_config(enabled=True)
        if args.new_session:
            await OpenAIManager.start_new_session()
        if args.mock_failure == "unavailable":
            await runner.cleanup()
        reply = await OpenAIManager.send(args.message, wait_response=True)
        hardware_loaded = any(name in sys.modules for name in (
            "open_xiaoai_server", "core.app", "core.xiaoai",
            "core.services.speaker", "core.services.tts.router",
        ))
        logger.info(json.dumps({
            "profile": config["profile"],
            "display_name": config["display_name"],
            "evidence": "mock",
            "reply": reply,
            "history_messages": len(received[0]["messages"]) - 2 if received else None,
            "session_key": received[0]["session_key"] if received else None,
            "error": OpenAIManager.last_error,
            "request_count": len(received),
            "hardware_loaded": hardware_loaded,
        }, ensure_ascii=False), module="DoubaoText")
        return 0 if reply and not hardware_loaded else 1
    finally:
        await OpenAIManager.close()
        await runner.cleanup()
        listener.close()


def main(argv: list[str] | None = None) -> int:
    """命令行仅接受测试输入，不接受真实服务地址或凭据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--new-session", action="store_true")
    parser.add_argument("--mock-failure", choices=("empty", "http-error", "timeout", "unavailable"))
    args = parser.parse_args(argv)
    if not args.message.strip():
        parser.error("message 不能为空")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

"""通过假 Client 静默验证真实 Bridge/KWS；绝不执行收到的设备命令。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid
import wave

import websockets
from websockets.exceptions import InvalidStatus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.utils.logger import logger

WAKE_COMMAND = 'ubus call pnshelper event_notify \'{"src":0,"event":0}\''


async def check_auth(uri):
    """缺失或错误 Token 必须在握手阶段被拒绝。"""
    for headers in ({}, {"Authorization": "Bearer invalid-validation-token"}):
        try:
            async with websockets.connect(uri, additional_headers=headers):
                raise AssertionError("无效鉴权意外成功")
        except InvalidStatus as exc:
            assert exc.response.status_code == 401, exc.response.status_code
    logger.info("无 Token、错误 Token 均被拒绝（401）", module="RuntimeTest")


async def check_audio(uri, token, path, expected):
    """传输 PCM 至真实服务，并观察唤醒 RPC；成功响应仅来自替身。"""
    with wave.open(str(path)) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 16000)
        assert audio.getnframes() > 0, "测试音频为空"
        pcm = b"\0" * 16000 + audio.readframes(audio.getnframes()) + b"\0" * 48000
    ready = asyncio.Event()
    wakes = []
    async with websockets.connect(uri, additional_headers={"Authorization": f"Bearer {token}"}) as ws:
        async def receive():
            async for raw in ws:
                assert isinstance(raw, str), "原生模式意外输出播放音频"
                request = json.loads(raw).get("Request")
                assert request is not None, "意外协议消息"
                command, payload = request["command"], request.get("payload")
                if command == "start_recording":
                    assert payload["sample_rate"] == 16000 and payload["channels"] == 1
                    data = None
                    ready.set()
                elif command == "run_shell":
                    # 包括连接提示在内的 shell 请求全部只应答，不执行。
                    if payload == WAKE_COMMAND:
                        wakes.append(payload)
                    data = {"stdout": '{"code": 0}', "stderr": "", "exit_code": 0}
                else:
                    raise AssertionError(f"意外设备请求: {command}")
                await asyncio.sleep(0.01)
                await ws.send(json.dumps({"Response": {"id": request["id"], "code": 0, "data": data}}))

        reader = asyncio.create_task(receive())
        try:
            await asyncio.wait_for(ready.wait(), timeout=15)
            for offset in range(0, len(pcm), 1024):
                chunk = pcm[offset:offset + 1024].ljust(1024, b"\0")
                await ws.send(json.dumps({"id": str(uuid.uuid4()), "tag": "record", "bytes": list(chunk)}).encode())
                await asyncio.sleep(0.032)
                if reader.done():
                    await reader
            await asyncio.sleep(2)
            if reader.done():
                await reader
            assert len(wakes) == expected, f"{path.name}: 预期 {expected} 次唤醒，实际 {len(wakes)} 次"
            logger.info(f"{path.name}: {len(wakes)} 次唤醒 RPC，符合预期；未执行设备命令", module="RuntimeTest")
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="ws://127.0.0.1:14399")
    parser.add_argument("--negative", type=Path)
    parser.add_argument("--positive", type=Path)
    parser.add_argument("--cases", type=Path, help="音频清单 JSON：file（相对清单目录）、expected（0/1）")
    args = parser.parse_args()
    if args.cases:
        if args.negative or args.positive:
            parser.error("--cases 不能与 --negative/--positive 混用")
        manifest = json.loads(args.cases.read_text())
        if not isinstance(manifest, list) or not manifest:
            parser.error("音频清单必须是非空列表")
        cases = []
        for case in manifest:
            if not isinstance(case, dict) or type(case.get("expected")) is not int or case["expected"] not in (0, 1):
                parser.error("每项 expected 必须为整数 0 或 1")
            cases.append((args.cases.parent / case["file"], case["expected"]))
    else:
        if not args.negative or not args.positive:
            parser.error("提供 --cases 或同时提供 --negative 与 --positive")
        cases = [(args.negative, 0), (args.positive, 1)]
    token = os.environ.get("OPEN_XIAOAI_TOKEN")
    if not token:
        raise ValueError("必须从私有环境提供 OPEN_XIAOAI_TOKEN")
    # 模型加载完成后才开放监听，等待启动但不重试音频或唤醒请求。
    deadline = asyncio.get_running_loop().time() + 30
    while True:
        try:
            await check_auth(args.uri)
            break
        except ConnectionRefusedError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.25)
    failures = []
    for path, expected in cases:
        try:
            await check_audio(args.uri, token, path, expected)
        except AssertionError as exc:
            logger.error(str(exc), module="RuntimeTest")
            failures.append(path.name)
    if failures:
        raise AssertionError(f"{len(failures)}/{len(cases)} 项未通过: {', '.join(failures)}")
    logger.info(f"全部 {len(cases)} 项音频验证通过", module="RuntimeTest")


if __name__ == "__main__":
    asyncio.run(main())

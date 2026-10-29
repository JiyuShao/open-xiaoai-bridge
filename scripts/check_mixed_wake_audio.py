"""离线文件音频 → 真实 KWS → 路由表验证；不导入音箱服务、不调用 Agent/TTS。"""

import argparse
import json
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_cases(manifest: Path) -> list[dict]:
    """读取 16kHz 单声道 PCM16 WAV，替换派发出口为内存收集器。"""
    from core.services.audio.kws import _KWS
    from core.utils.config import ConfigManager

    cases = json.loads(manifest.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError("音频清单必须是非空列表")
    routes = ConfigManager.instance().get_app_config("wakeup.routes")
    results = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("file"), str) or not isinstance(case.get("expected"), list):
            raise ValueError("每项需要 file 和 expected 路由列表（native/openai）")
        if any(route not in ("native", "openai") for route in case["expected"]):
            raise ValueError("expected 只允许 native/openai，负例为 []")
        path = manifest.parent / case["file"]
        with wave.open(str(path)) as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000) or not audio.getnframes():
                raise ValueError("需要非空 16kHz 单声道 PCM16 WAV")
            pcm = audio.readframes(audio.getnframes()) + b"\0" * 64000
        words: list[str] = []
        # 替换派发边界，因此即使识别成功也绝不唤醒设备或启动对话。
        detector = _KWS(on_wakeup=words.append)
        try:
            detector.start()
            stream = detector.stream
            if stream is None:
                raise RuntimeError("KWS 输入流未就绪")
            for offset in range(0, len(pcm), 1024):
                stream.input(pcm[offset:offset + 1024].ljust(1024, b"\0"))
                time.sleep(0.032)
            deadline = time.monotonic() + 2
            while stream.pending_bytes and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            detector.stop()
        actual = [routes.get(word, "unknown") for word in words]
        results.append({"file": case["file"], "words": words, "routes": actual,
                        "expected": case["expected"], "passed": actual == case["expected"]})
    return results


def main():
    from main import setup_config, prepare_startup
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    args = parser.parse_args()
    flags = setup_config()
    if not flags["enable_mixed_wake"]:
        raise ValueError("此文件检查需要 WAKE_MODE=mixed 与 OPENAI_ENABLE=1")
    prepare_startup(flags)
    results = check_cases(args.cases)
    from core.utils.logger import logger
    logger.info(json.dumps({"device_calls": 0, "results": results}, ensure_ascii=False), module="MixedWakeAudio")
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""命令行与容器共用的启动入口；检查和关键词准备先于设备服务。"""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from core.utils.logger import logger

ROOT = Path(__file__).resolve().parent
main_app_instance = None


def setup_config():
    """一次解析启动开关，并把规范值提供给 Python 子进程与 Rust。"""
    from core.wake_mode import resolve_wake_mode, validate_wake_environment
    from core.utils.base import get_env_bool

    names = {
        "enable_xiaozhi": "XIAOZHI_ENABLE",
        "enable_openclaw": "OPENCLAW_ENABLE",
        "enable_openai": "OPENAI_ENABLE",
        "enable_qwenpaw": "QWENPAW_ENABLE",
        "enable_api_server": "API_SERVER_ENABLE",
    }
    # 显式的新开关优先；仅新开关为空时使用上游旧别名。
    if not os.environ.get("OPENCLAW_ENABLE"):
        os.environ["OPENCLAW_ENABLE"] = os.environ.get("OPENCLAW_ENABLED", "0")
    mode = resolve_wake_mode()
    validate_wake_environment(mode)
    flags = {name: get_env_bool(key) for name, key in names.items()}
    flags["enable_native_wake"] = mode == "native"
    flags["enable_mixed_wake"] = mode == "mixed"
    os.environ["WAKE_MODE"] = mode
    for name, key in names.items():
        os.environ[key] = "1" if flags[name] else "0"
    os.environ["AUDIO_INPUT_ENABLE"] = "1" if get_env_bool("AUDIO_INPUT_ENABLE", True) else "0"
    if flags["enable_native_wake"] and not os.environ.get("CONFIG_PATH"):
        os.environ["CONFIG_PATH"] = str(ROOT / "config.native-wake.py")
    if flags["enable_mixed_wake"] and not os.environ.get("CONFIG_PATH"):
        os.environ["CONFIG_PATH"] = str(ROOT / "config.mixed-wake.py")
    logger.info("[Main] 启用模块: " + ", ".join(name for name, enabled in flags.items() if enabled))
    return flags


def prepare_startup(flags, *, check_only=False):
    """检查原生模式的文件和配置；实际启动前重新生成词表。"""
    if flags["enable_native_wake"] or flags["enable_mixed_wake"] or check_only:
        from scripts.check_native_wake import main as check_native_wake
        if check_native_wake() != 0:
            raise ValueError("原生唤醒启动检查未通过")
    if check_only:
        return
    setup_library_path()
    if any(flags[key] for key in (
        "enable_native_wake", "enable_mixed_wake", "enable_xiaozhi", "enable_openclaw",
        "enable_openai", "enable_qwenpaw",
    )):
        subprocess.run(
            [sys.executable, str(ROOT / "core/services/audio/kws/keywords.py")],
            check=True,
        )


def setup_library_path():
    """让 Sherpa 在导入前找到 ONNX Runtime 动态库。"""
    if sys.platform not in ("darwin", "linux"):
        return
    import onnxruntime
    library = str(Path(onnxruntime.__file__).parent / "capi")
    key = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    if library not in os.environ.get(key, "").split(os.pathsep):
        os.environ[key] = library + (os.pathsep + os.environ[key] if os.environ.get(key) else "")


def main(argv=None):
    global main_app_instance
    if sys.version_info < (3, 12):
        logger.error("Bridge 需要 Python 3.12+，请使用 bash scripts/native-wake.sh [--check]", module="Main")
        return 1
    parser = argparse.ArgumentParser(description="小爱音箱接入 Open XiaoAI")
    parser.add_argument("--check", action="store_true", help="原生模式离线检查，不加载模型或启动服务")
    args = parser.parse_args(argv)
    try:
        flags = setup_config()
        prepare_startup(flags, check_only=args.check)
        if args.check:
            return 0
        from core.app import MainApp
        enable_api_server = flags.pop("enable_api_server")
        main_app_instance = MainApp.instance(**flags)
        main_app_instance.run(enable_api_server=enable_api_server)
        while not main_app_instance.shutdown_requested:
            main_app_instance.check_service()
            time.sleep(0.1)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error(f"启动失败: {exc}", module="Main")
        return 1
    finally:
        if main_app_instance is not None:
            main_app_instance.shutdown()


def setup_graceful_shutdown():
    def signal_handler(_sig, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


if __name__ == "__main__":
    setup_graceful_shutdown()
    sys.exit(main())

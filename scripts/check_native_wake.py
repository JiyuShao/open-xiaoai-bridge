"""离线部署前检查：不联网、不监听端口、不操作音箱。"""

import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.native_wake import native_wake_enabled, validate_native_wake_environment

MODEL_FILES = (
    "silero_vad.onnx", "encoder.onnx", "decoder.onnx", "joiner.onnx",
    "tokens.txt", "bpe.model",
)


def main():
    errors = []
    try:
        validate_native_wake_environment()
    except ValueError as exc:
        errors.append(str(exc))
    if not native_wake_enabled():
        errors.append("需要 NATIVE_WAKE_ENABLE=1")
    if not os.environ.get("OPEN_XIAOAI_TOKEN", "").strip():
        errors.append("需要设置 OPEN_XIAOAI_TOKEN，并在 Client 使用相同值")
    config_path = Path(os.environ.get("CONFIG_PATH") or ROOT / "config.py")
    if not config_path.is_file():
        errors.append("CONFIG_PATH 指定的配置文件不存在")
    for name in MODEL_FILES:
        path = ROOT / "core/models" / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"缺少模型: core/models/{name}")
            continue
        with path.open("rb") as model:
            digest = hashlib.file_digest(model, "sha256").hexdigest()
        print(f"SHA256 {name}: {digest}")
    for error in errors:
        print(f"[未就绪] {error}")
    if errors:
        return 1
    print("[检查通过] 本地文件与环境开关已就绪；不代表模型兼容或真机验收通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

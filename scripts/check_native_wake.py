"""离线部署前检查：不联网、不监听端口、不操作音箱。"""

import hashlib
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL_FILES = (
    "silero_vad.onnx", "encoder.onnx", "decoder.onnx", "joiner.onnx",
    "tokens.txt", "bpe.model",
)


def main():
    from core.wake_mode import resolve_wake_mode, validate_wake_environment, validate_mixed_config
    from core.utils.config_loader import get_config_path, load_config_module

    errors = []
    try:
        mode = resolve_wake_mode()
        validate_wake_environment(mode)
    except ValueError as exc:
        errors.append(str(exc))
        mode = "legacy"
    if mode == "legacy":
        errors.append("需要 WAKE_MODE=native 或 mixed（旧 NATIVE_WAKE_ENABLE=1 仍兼容）")
    if not os.environ.get("OPEN_XIAOAI_TOKEN", "").strip():
        errors.append("需要设置 OPEN_XIAOAI_TOKEN，并在 Client 使用相同值")
    config_path = get_config_path()
    try:
        module = load_config_module()
    except Exception as exc:
        # 用户配置是 Python 文件，异常文本可能含私有值，不直接回显。
        errors.append(f"配置加载失败: {type(exc).__name__} ({config_path})")
        module = None
    try:
        config = getattr(module, "APP_CONFIG", None)
        if not isinstance(config, dict):
            raise ValueError("APP_CONFIG 必须是字典")
        keywords = config.get("wakeup", {}).get("keywords")
        if not isinstance(keywords, list) or not keywords or any(
            not isinstance(word, str) or not word.strip() or word != word.strip().lower()
            for word in keywords
        ):
            raise ValueError("wakeup.keywords 需要非空词表，英文小写且不含首尾空白")
        native = config.get("native_wake", {})
        for key, default in (("cooldown_seconds", 3), ("timeout_seconds", 5)):
            value = float(native.get(key, default))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"native_wake.{key} 必须是有限正数")
        if mode == "mixed":
            validate_mixed_config(config)
    except (ValueError, TypeError, AttributeError):
        errors.append("配置无效: APP_CONFIG 需包含非空小写 wakeup.keywords 词表，native_wake 冷却和超时需为有限正数")
        if mode == "mixed":
            errors.append("混合配置需包含完整 native/openai 路由、xiaoai-doubao profile、持久化历史、有效 Hermes URL 和本地 SenseVoice ASR")
    if mode == "mixed" and isinstance(config, dict) and not errors:
        asr_config = config.get("asr", {})
        model_name = "model.int8.onnx" if asr_config.get("int8", True) else "model.onnx"
        candidates = [ROOT / "core/models" / asr_config["model_dir"]] if asr_config.get("model_dir") else list((ROOT / "core/models").glob("*sense-voice*"))
        if not any(all((folder / name).is_file() and (folder / name).stat().st_size for name in (model_name, "tokens.txt")) for folder in candidates):
            errors.append("缺少本地 SenseVoice ASR 模型和 tokens.txt")
    for name in MODEL_FILES:
        path = ROOT / "core/models" / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"缺少模型: core/models/{name}")
            continue
        with path.open("rb") as model:
            checksum = hashlib.sha256()
            for chunk in iter(lambda: model.read(1024 * 1024), b""):
                checksum.update(chunk)
            digest = checksum.hexdigest()
        print(f"SHA256 {name}: {digest}")
    for error in errors:
        print(f"[未就绪] {error}")
    if errors:
        return 1
    print("[检查通过] 本地文件与环境开关已就绪；不代表模型兼容或真机验收通过。")
    return 0


if __name__ == "__main__":
    # 保留原检查命令，但解释器选择与默认配置交给统一启动脚本。
    from core.wake_mode import resolve_wake_mode
    script = "mixed-wake.sh" if resolve_wake_mode() == "mixed" else "native-wake.sh"
    os.execvp("bash", ["bash", str(ROOT / "scripts" / script), "--check"])

import os
import re
import tempfile
from pathlib import Path


def init_project_context():
    """动态导入父模块"""
    import os
    import sys

    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../..")
    )
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


init_project_context()

from core.wake_mode import resolve_wake_mode, validate_wake_environment
from core.utils.base import get_env_bool
from core.utils.config import ConfigManager
from core.utils.file import get_model_file_path
from core.utils.logger import logger


def should_generate_keywords():
    """Return whether keyword generation should run."""
    import os

    mode = resolve_wake_mode()
    validate_wake_environment(mode)
    if mode in ("native", "mixed"):
        return True, ""

    xiaozhi_enabled = get_env_bool("XIAOZHI_ENABLE")
    openclaw_enabled = get_env_bool("OPENCLAW_ENABLE", alias="OPENCLAW_ENABLED")
    openai_enabled = get_env_bool("OPENAI_ENABLE")
    qwenpaw_enabled = get_env_bool("QWENPAW_ENABLE")

    if (
        not xiaozhi_enabled
        and not openclaw_enabled
        and not openai_enabled
        and not qwenpaw_enabled
    ):
        return False, "XIAOZHI_ENABLE, OPENCLAW_ENABLE/OPENCLAW_ENABLED, OPENAI_ENABLE and QWENPAW_ENABLE are all disabled"

    return True, ""


def get_args():
    config = ConfigManager.instance()
    tokens_type = "cjkchar+bpe"
    tokens = get_model_file_path("tokens.txt")
    bpe_model = get_model_file_path("bpe.model")
    output = get_model_file_path("keywords.txt")
    keywords = config.get_app_config("wakeup.keywords", [])
    texts = [f"{keyword.upper()}" for keyword in keywords]
    return locals()


def main():
    should_run, reason = should_generate_keywords()
    if not should_run:
        logger.debug(f"Keyword generation skipped: {reason}", module="KWS")
        return 0

    required_files = [
        get_model_file_path("tokens.txt"),
        get_model_file_path("bpe.model"),
    ]
    missing_files = [path for path in required_files if not Path(path).is_file()]
    if missing_files:
        logger.debug(
            "Keyword generation failed: missing model files: "
            f"{', '.join(missing_files)}",
            module="KWS",
        )
        return 1

    from sherpa_onnx import text2token

    args = get_args()
    encoded_texts = text2token(
        args["texts"],
        tokens=args["tokens"],
        tokens_type=args["tokens_type"],
        bpe_model=args["bpe_model"],
    )
    if len(encoded_texts) != len(args["texts"]) or any(
        not tokens or any(not isinstance(token, str) or not token.strip() for token in tokens)
        for tokens in encoded_texts
    ):
        logger.error("关键词编码为空或数量不匹配，保留原词表", module="KWS")
        return 1
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf8", dir=Path(args["output"]).parent,
            prefix=".keywords-", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            for tokens in encoded_texts:
                line = "".join(tokens)
                suffix = "" if re.match(r"^[▁A-Z\s]+$", line) else f" @{line}"
                stream.write(" ".join(tokens) + suffix + "\n")
        os.replace(temporary, args["output"])
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    logger.debug(f"Keyword file generated: {args['output']}", module="KWS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""只增加唤醒入口，不接管原生业务。"""

import asyncio
import math
import time

from core.wake_mode import resolve_wake_mode, validate_wake_environment


def native_wake_enabled() -> bool:
    return resolve_wake_mode() == "native"


def validate_native_wake_environment(*, enabled=None) -> None:
    """在创建连接和后台线程前拒绝互相矛盾的开关。"""
    if enabled is None:
        enabled = native_wake_enabled()
    if not enabled:
        return
    validate_wake_environment("native")


class NativeWakeController:
    """在应用事件循环内串行唤醒；超时结果未知，不自动重试。"""

    def __init__(self, config, *, clock=time.monotonic):
        self.config = config
        self.clock = clock
        self._busy = False
        self._next_allowed = 0.0

    async def wakeup(self, speaker, text, source) -> bool:
        from core.utils.logger import logger

        keywords = self.config.get_app_config("wakeup.keywords", [])
        if source != "kws" or text not in keywords or speaker is None:
            return False
        if self._busy or self.clock() < self._next_allowed:
            logger.debug("[NativeWake] 忽略冷却期内的重复唤醒")
            return False
        cooldown = self._positive_seconds("native_wake.cooldown_seconds", 3.0)
        timeout = self._positive_seconds("native_wake.timeout_seconds", 5.0)
        self._busy = True
        try:
            accepted = await asyncio.wait_for(
                speaker.wake_up(awake=True, silent=False), timeout=timeout,
            )
            if accepted:
                logger.info("[NativeWake] 原生唤醒请求已受理，实际反馈待设备确认")
            else:
                logger.warning("[NativeWake] 原生唤醒请求未受理")
            return bool(accepted)
        except Exception as exc:
            logger.warning(
                f"[NativeWake] 原生唤醒失败或结果未知，不自动重试: {type(exc).__name__}"
            )
            return False
        finally:
            self._next_allowed = self.clock() + cooldown
            self._busy = False

    def _positive_seconds(self, path, default):
        value = float(self.config.get_app_config(path, default))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{path} 必须是有限正数")
        return value

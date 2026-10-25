import asyncio
import math
import os
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

from core.ref import get_app, get_xiaoai, get_xiaozhi, set_kws
from core.services.audio.kws.sherpa import SherpaOnnx
from core.services.audio.stream import MyAudio
from core.services.audio.vad.silero import Silero
from core.services.protocols.typing import AudioConfig, DeviceState
from core.utils.config import ConfigManager
from core.utils.logger import logger
from core.wakeup_session import EventManager


class _HighPassFilter:
    """一阶高通滤波，抑制远场低频噪声（风噪、桌面震动、空调低频）。

    2~3 米远场录音里低频分量占比明显更高，直通 KWS 时唤醒命中率很低；
    实测 200Hz 一阶高通能把同一批远场录音的命中从 0/5 提到 3/5。
    滤波状态跨帧保留，避免每帧边界的阶跃。
    """

    def __init__(self, cutoff_hz: float, sample_rate: int = 16000) -> None:
        self.cutoff_hz = cutoff_hz
        self._alpha = 1.0 / (1.0 + 2.0 * math.pi * cutoff_hz / sample_rate)
        self._prev_input = 0.0
        self._prev_output = 0.0

    def process(self, frames: bytes) -> bytes:
        samples = np.frombuffer(frames, dtype=np.int16)
        alpha = self._alpha
        prev_input = self._prev_input
        prev_output = self._prev_output
        out = np.empty(samples.shape[0], dtype=np.float64)
        # 一阶 IIR 必须逐点递推；每帧只有 512 点，开销远小于一次 KWS 推理
        for i, sample in enumerate(samples.tolist()):
            prev_output = alpha * (prev_output + sample - prev_input)
            prev_input = sample
            out[i] = prev_output
        self._prev_input = prev_input
        self._prev_output = prev_output
        return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


class _KWS:
    def __init__(self):
        set_kws(self)
        self._running = False
        self.thread = None
        self.stream = None
        self.audio = None
        self.config_manager = ConfigManager.instance()
        self.vad_threshold = 0.10
        # 唤醒前的高通截止频率，<=0 表示关闭；远场命中率对低频噪声很敏感
        self.highpass_hz = 200.0
        self._highpass: Optional[_HighPassFilter] = None
        # VAD 触发瞬间补送的预卷帧数（触发前的音频），0 表示关闭
        self.preroll_frames = 10
        self.frame_history: deque = deque(maxlen=self.preroll_frames)

        # VAD 状态变量
        self.vad_active = False
        self.vad_speech_frames = 0  # 语音帧计数
        self.vad_silence_frames = 0  # 静音帧计数
        self.vad_min_silence_frames = 15  # 静默超过该帧数则判定为说完
        self.vad_start_time = time.time()
        
        # 设置帧大小为 512 以兼容 Silero VAD
        self.frame_size = 512
        self.sample_rate = 16000
        self.frame_duration_ms = (self.frame_size * 1000) / self.sample_rate  # 32ms per frame
        # 允许的最大积压：约 1 秒（16 kHz × 2 字节）。积压说明检测落后于实时，
        # 继续处理旧音频只会让唤醒越来越迟，超过上限就丢弃最旧的数据。
        self.max_backlog_bytes = self.sample_rate * 2

        self.apply_runtime_config()
        self.config_manager.add_reload_listener(self._on_config_reload)

    def apply_runtime_config(self):
        """同步最新 KWS 相关配置。"""
        vad_config = self.config_manager.get_app_config("vad", {})
        self.vad_threshold = vad_config.get("threshold", 0.10)
        kws_config = self.config_manager.get_app_config("kws", {})
        min_silence_ms = kws_config.get("min_silence_duration", 480)
        self.vad_min_silence_frames = int(min_silence_ms / self.frame_duration_ms)
        highpass_hz = float(kws_config.get("highpass_hz", 200))
        if highpass_hz <= 0:
            self._highpass = None
        elif self._highpass is None or self._highpass.cutoff_hz != highpass_hz:
            self._highpass = _HighPassFilter(highpass_hz, self.sample_rate)
        self.highpass_hz = highpass_hz
        preroll_ms = int(kws_config.get("preroll_ms", 320))
        preroll_frames = max(0, int(preroll_ms / self.frame_duration_ms))
        if preroll_frames != self.preroll_frames:
            self.preroll_frames = preroll_frames
            self.frame_history = deque(self.frame_history, maxlen=preroll_frames)

    def _on_config_reload(self, *_args):
        """配置重载后刷新运行时参数。"""
        self.apply_runtime_config()

    def start(self):
        # 在调用线程加载模型，让启动错误直接失败，避免后台线程静默退出。
        SherpaOnnx.start()
        self.audio = MyAudio.create()
        self.stream = self.audio.open(
            format=AudioConfig.FORMAT,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=AudioConfig.FRAME_SIZE,
            start=True,
        )

        # 启动 KWS 服务
        self.paused = False
        self._running = True
        self.thread = threading.Thread(target=self._detection_loop, daemon=True)
        self.thread.start()
        config = ConfigManager.instance()
        keywords_score = config.get_app_config("kws.keywords_score", 2.0)
        keywords_threshold = config.get_app_config("kws.keywords_threshold", 0.2)
        min_silence_ms = config.get_app_config("kws.min_silence_duration", 480)
        logger.kws_event("关键词唤醒服务启动", f"关键词:[score:{keywords_score}, threshold:{keywords_threshold}], VAD:[threshold:{self.vad_threshold}, min_silence:{min_silence_ms}ms], 高通:[{self.highpass_hz if self._highpass else 'off'}Hz], 预卷:[{self.preroll_frames * self.frame_duration_ms:.0f}ms]")

    def stop(self):
        """停止检测线程并注销输入流；允许初始化中途失败后调用。"""
        self._running = False
        self.paused = True
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.stream is not None:
            self.stream.close()
        if self.audio is not None:
            self.audio.terminate()

    def get_file_path(self, file_name: str):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(current_dir, "../../../models", file_name)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def _detection_loop(self):
        self.stream.start_stream()
        health_ok = 0
        health_bad = 0
        health_speech = 0
        health_trim = 0
        health_last = time.monotonic()
        warn_last = 0.0
        trim_warn_last = 0.0
        # 上游断流（客户端不再发送录音）时循环只会空转，靠这两个变量留出告警
        last_data = time.monotonic()
        stall_warned = False
        while self._running:
            # 处理速度跟不上到达速度时延迟会一直累积；超过上限就丢弃最旧的积压
            trimmed = self.stream.trim_input(self.max_backlog_bytes, self.frame_size * 2)
            if trimmed:
                dropped_frames = trimmed // (self.frame_size * 2)
                health_trim += dropped_frames
                self.vad_active = False
                self.vad_silence_frames = 0
                SherpaOnnx.reset()
                now_trim = time.monotonic()
                if now_trim - trim_warn_last >= 10:
                    logger.warning(
                        f"[KWS] 检测落后于实时，丢弃积压音频 {dropped_frames} 帧"
                        f"（约 {dropped_frames * self.frame_duration_ms:.0f}ms）",
                        module="KWS",
                    )
                    trim_warn_last = now_trim
            # 读取缓冲区音频数据；读取本身失败不能结束检测线程
            try:
                frames = self.stream.read(self.frame_size)
            except Exception as exc:
                logger.error(
                    f"[KWS] 读取音频流异常: {type(exc).__name__}: {exc}",
                    module="KWS",
                )
                time.sleep(0.05)
                continue
            now = time.monotonic()
            if len(frames) != self.frame_size * 2:
                # 长度不符时静默丢弃会让检测线程“看起来在跑但不工作”，这里留告警
                health_bad += 1
                idle = now - last_data
                if idle >= 10 and not stall_warned:
                    # 唤醒完全失效最常见的原因是上游不再送音频，这里必须留痕
                    logger.warning(
                        f"[KWS] 已 {idle:.0f} 秒未收到音频帧（积压 {self.stream.pending_bytes} 字节，"
                        f"空读 {health_bad} 次），唤醒检测无法工作，请检查客户端录音链路",
                        module="KWS",
                    )
                    stall_warned = True
                elif not stall_warned and idle >= 3 and now - warn_last >= 15:
                    logger.debug(
                        f"[KWS] 音频帧尚未就绪（空读 {health_bad} 次），等待上游数据",
                        module="KWS",
                    )
                    warn_last = now
                # 阻塞等待下一帧就绪：轮询 sleep 会让每次读取都晚一点，延迟逐帧累积
                self.stream.wait_input(self.frame_size, 0.05)
                continue
            health_ok += 1
            last_data = now
            stall_warned = False
            if now - health_last >= 30:
                logger.debug(
                    f"[KWS] 运行状态: 正常帧 {health_ok}，空读 {health_bad}，"
                    f"语音帧 {health_speech}，丢弃 {health_trim}，"
                    f"积压 {self.stream.pending_bytes} 字节，paused={self.paused}",
                    module="KWS",
                )
                health_ok = health_bad = health_speech = health_trim = 0
                health_last = now

            # 在说话和监听状态时，暂停 KWS
            xiaozhi = get_xiaozhi()
            if (
                not frames
                or self.paused
                or (
                    xiaozhi and xiaozhi.device_state
                    in [DeviceState.LISTENING, DeviceState.SPEAKING]
                )
            ):
                time.sleep(0.01)
                continue

            # 先去掉低频噪声再送 VAD/KWS；暂停时不滤波，省下无用的逐点计算
            if self._highpass is not None:
                frames = self._highpass.process(frames)
            # 保留最近几帧：VAD 触发比人声起点晚，触发时要补送这段预卷
            self.frame_history.append(frames)

            # 单帧处理异常同样不能结束检测线程
            try:
                if self._process_frame(frames):
                    health_speech += 1
            except Exception as exc:
                logger.error(
                    f"[KWS] 处理音频帧异常: {type(exc).__name__}: {exc}",
                    module="KWS",
                )
                time.sleep(0.01)

    def _match(self, frames) -> bool:
        """把一帧送入 KWS，命中则派发唤醒事件。"""
        result = SherpaOnnx.kws(frames)
        if not result:
            return False
        logger.wakeup(result, module="KWS")
        self.on_message(result)
        return True

    def _process_frame(self, frames) -> bool:
        """处理一帧音频：VAD 门控后送入唤醒词检测。返回该帧是否判定为语音。"""
        # 先进行 VAD 检测
        speech_prob = Silero.vad(frames, self.sample_rate) or 0
        is_speech = speech_prob >= self.vad_threshold

        if is_speech:
            # 检测到语音，立即激活
            if not self.vad_active:
                self.vad_active = True
                self.vad_start_time = time.time()
                logger.debug("检测到语音，开始 KWS 检测", module="KWS")
                # VAD 触发比人声起点晚；不补送预卷会丢掉唤醒词开头的音节
                if self.preroll_frames:
                    for history_frame in list(self.frame_history)[:-1]:
                        if self._match(history_frame):
                            self.vad_active = False
                            self.vad_silence_frames = 0
                            return is_speech

            self.vad_silence_frames = 0

            # 只在有语音时才进行 KWS 检测
            if self._match(frames):
                # 唤醒后重置状态
                self.vad_active = False
                self.vad_silence_frames = 0

        else:
            # 静音处理
            if self.vad_active:
                self.vad_silence_frames += 1

                # 在激活状态下，允许一定的静音间隙
                if self.vad_silence_frames <= self.vad_min_silence_frames:
                    # 继续将音频送入 KWS，允许短暂的静音
                    if self._match(frames):
                        self.vad_active = False
                        self.vad_silence_frames = 0
                else:
                    # 静音超过阈值，停止处理
                    duration_ms = self.vad_silence_frames * self.frame_duration_ms
                    active_duration_ms = (time.time() - self.vad_start_time) * 1000 if hasattr(self, 'vad_start_time') else -1
                    logger.debug(
                        (
                            f"检测到持续静音（{duration_ms:.0f}ms），暂停 KWS，"
                            f"本次 KWS 监听时长 {active_duration_ms:.0f}ms"
                        ),
                        module="KWS",
                    )
                    self.vad_active = False
                    self.vad_silence_frames = 0
                    # Reset Sherpa stream to discard partial recognition state,
                    # preventing leftover audio from the previous utterance
                    # from combining with the next one.
                    SherpaOnnx.reset()

        return is_speech

    def on_message(self, text: str):
        app = get_app()
        loop = app.loop if app else get_xiaoai().async_loop
        dispatch = app.handle_wakeup if app else EventManager.wakeup
        logger.debug(f"[KWS] Dispatch wakeup event: {text}")
        future = asyncio.run_coroutine_threadsafe(
            dispatch(text, "kws"),
            loop,
        )

        def _log_result(done_future):
            try:
                done_future.result()
            except Exception as exc:
                logger.error(f"[KWS] Wakeup dispatch failed: {type(exc).__name__}: {exc}")

        future.add_done_callback(_log_result)


KWS = _KWS()

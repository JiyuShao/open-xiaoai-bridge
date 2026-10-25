import threading
import uuid
from typing import Any, Callable, ClassVar, Optional


class __GlobalStream:
    def __init__(self):
        self.readers = {}
        self.on_output_data = None

    def register_reader(self, reader):
        if reader.id not in self.readers:
            self.readers[reader.id] = reader

    def unregister_reader(self, reader) -> None:
        if reader.id in self.readers:
            del self.readers[reader.id]

    def input(self, data: bytes) -> None:
        for key in self.readers:
            self.readers[key].input(data)

    def output(self, frames: bytes) -> None:
        if self.on_output_data:
            self.on_output_data(frames)


GlobalStream = __GlobalStream()


class MyStream:
    def __init__(
        self,
        rate: int,
        channels: int,
        format: int,
        input: bool = False,
        output: bool = False,
        frames_per_buffer: int = 1024,
        start: bool = True,
    ) -> None:
        self.id = uuid.uuid4()
        self._rate = rate
        self._channels = channels
        self._format = format
        self._frames_per_buffer = frames_per_buffer
        self._is_input = input
        self._is_output = output
        self._is_active = False

        self.input_bytes = bytearray()
        self._read_offset = 0
        # 录音数据到达时唤醒读者，避免消费者用 sleep 轮询（轮询会逐帧累积延迟）
        self._input_cond = threading.Condition()

        if start:
            self.start_stream()

    def close(self) -> None:
        self.stop_stream()

    def is_active(self) -> bool:
        return self._is_active

    def clear_input(self) -> None:
        """Drop all buffered input and reset the read cursor.

        Both must happen together: clearing input_bytes without resetting
        _read_offset leaves the cursor pointing past the (now shorter) buffer,
        which makes read() skip freshly-arrived audio until the buffer refills
        past the stale offset — corrupting every capture after the first.
        """
        with self._input_cond:
            self.input_bytes.clear()
            self._read_offset = 0

    def start_stream(self) -> None:
        if not self._is_active:
            self._is_active = True
            if self._is_input:
                GlobalStream.register_reader(self)

    def stop_stream(self) -> None:
        if self._is_active:
            self._is_active = False
            if self._is_input:
                GlobalStream.unregister_reader(self)
                self.clear_input()
                with self._input_cond:
                    self._input_cond.notify_all()

    def write(self, frames: bytes) -> None:
        # 发送输出音频流到扬声器
        if not self._is_output or not self._is_active:
            return
        GlobalStream.output(frames)

    def input(self, data: bytes):
        # 收到麦克风输入音频流
        if not self._is_input or not self._is_active:
            return

        if len(data) > 0:
            with self._input_cond:
                self.input_bytes.extend(data)
                self._input_cond.notify_all()

    @property
    def pending_bytes(self) -> int:
        """尚未被读走的字节数。"""
        return len(self.input_bytes) - self._read_offset

    def wait_input(self, num_frames: int, timeout: float = 0.05) -> bool:
        """阻塞等待至少 num_frames 帧可读；超时或数据流已停止返回 False。"""
        need = num_frames * 2
        with self._input_cond:
            if not self._is_active:
                return False
            return self._input_cond.wait_for(
                lambda: not self._is_active or self.pending_bytes >= need,
                timeout,
            )

    def trim_input(self, max_bytes: int, frame_bytes: int = 1024) -> int:
        """丢弃最旧的积压数据，只保留最近 max_bytes；返回丢弃的字节数。

        积压意味着检测落后于实时；与其让延迟一直累积，不如丢掉旧音频。
        丢弃长度按整帧对齐，避免把一帧从中间切开。
        """
        if frame_bytes <= 0:
            return 0
        with self._input_cond:
            pending = self.pending_bytes
            if pending <= max_bytes:
                return 0
            drop = ((pending - max_bytes) // frame_bytes) * frame_bytes
            if drop <= 0:
                return 0
            self._read_offset += drop
            if self._read_offset > 262144:
                del self.input_bytes[:self._read_offset]
                self._read_offset = 0
            return drop

    def read(self, num_frames=None, exception_on_overflow=False) -> bytes:
        if num_frames is None:
            data = bytes(self.input_bytes)
            self.input_bytes.clear()
            self._read_offset = 0
            return data

        bytes_needed = num_frames * 2
        if (
            not self._is_input
            or not self._is_active
            # 达不到预期长度时，返回空字节，等待下一次读取
            or len(self.input_bytes) - self._read_offset < bytes_needed
        ):
            return bytes([])

        # 偏移读：只取需要的部分，不删除剩余数据
        data = bytes(self.input_bytes[self._read_offset:self._read_offset + bytes_needed])
        self._read_offset += bytes_needed

        # 延迟回收：累积偏移超过阈值才批量清理已读数据
        if self._read_offset > 262144:
            del self.input_bytes[:self._read_offset]
            self._read_offset = 0

        return data


class MyAudio:
    """PyAudio 替代品，用于创建和管理音频流"""

    Stream: ClassVar[type] = MyStream

    @classmethod
    def create(cls):
        # 使用小爱音箱音频（通过 Rust 补丁）
        return MyAudio()

    @classmethod
    def get_input_device_index(cls, audio):
        return 0

    @classmethod
    def get_output_device_index(cls, audio):
        return 0

    def __init__(self) -> None:
        self._is_terminated = False

    def open(
        self,
        rate: int,
        channels: int,
        format: int,
        input: bool = False,
        output: bool = False,
        input_device_index: Optional[int] = None,
        output_device_index: Optional[int] = None,
        frames_per_buffer: int = 1024,
        start: bool = True,
        input_host_api_specific_stream_info: Optional[Any] = None,
        output_host_api_specific_stream_info: Optional[Any] = None,
        stream_callback: Optional[Callable] = None,
    ) -> MyStream:
        if self._is_terminated:
            raise RuntimeError("MyAudio instance has been terminated")

        return MyStream(
            rate=rate,
            channels=channels,
            format=format,
            input=input,
            output=output,
            frames_per_buffer=frames_per_buffer,
            start=start,
        )

    def terminate(self) -> None:
        if not self._is_terminated:
            self._is_terminated = True

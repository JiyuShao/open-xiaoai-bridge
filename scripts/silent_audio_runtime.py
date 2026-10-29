"""文件音频验收专用替身：无设备连接能力，命令只记录，从不执行。"""

import asyncio
import struct
import sys
import types


def module(name: str, **members) -> types.ModuleType:
    value = types.ModuleType(name)
    value.__dict__.update(members)
    return value


class Samples:
    def __init__(self, data):
        self.data = data
    def astype(self, _dtype):
        return self
    def __truediv__(self, _scale):
        return self


def marker(data: bytes) -> int:
    values = struct.unpack('<' + 'h' * (len(data) // 2), data)
    return max((abs(value) for value in values), default=0)


def install(events: list, transcriptions: dict[int, str], *, mock_models: bool):
    """在任何 Bridge/数值模块导入前安装明确的测试传输。"""
    async def record(name, *args, **kwargs):
        events.append({'kind': name, 'args': args, 'parameters': kwargs})
        if name == 'run_shell':
            return '{"stdout":"{\\"code\\":0}","stderr":"","exit_code":0}'
    runtime = module('open_xiaoai_server',
        decode_audio=lambda *a, **kw: b'',
        begin_playback_session=lambda: 1,
        stop_tts_playback=lambda token: events.append({'kind':'stop_token', 'token':token}),
    )
    for name in ('start_recording', 'stop_recording', 'start_playing', 'stop_playing', 'run_shell', 'tts_play', 'tts_stream_play'):
        async def action(*args, _name=name, **kwargs):
            return await record(_name, *args, **kwargs)
        setattr(runtime, name, action)
    # 传输替身故意没有 start_server/register_fn；误启动会立即失败。
    sys.modules['open_xiaoai_server'] = runtime
    if not mock_models:
        return

    class Detector:
        def vad(self, frames, sample_rate):
            return 1.0 if marker(frames) else 0.0
    sys.modules['core.services.audio.vad.silero'] = module('core.services.audio.vad.silero',
        Silero=Detector(), SileroDetector=Detector)

    class Stream:
        def __init__(self):
            self.data = b''
            self.result = types.SimpleNamespace(text='')
        def accept_waveform(self, rate, samples):
            self.data += samples.data
    class Recognizer:
        def create_stream(self):
            return Stream()
        def decode_stream(self, stream):
            stream.result.text = transcriptions.get(marker(stream.data), '')
    class Spotter:
        def __init__(self, **kwargs): pass
        def create_stream(self): return Stream()
        def is_ready(self, stream): return bool(stream.data)
        def decode_stream(self, stream): pass
        def get_result(self, stream):
            word = {100: '豆包豆包', 200:'理想同学'}.get(marker(stream.data))
            stream.data = b''
            return word
        def reset_stream(self, stream): stream.data = b''
    sys.modules['numpy'] = module('numpy',frombuffer=lambda data, **kw: Samples(data), int16=object(), float32=object())
    sys.modules['sherpa_onnx'] = module('sherpa_onnx',
        KeywordSpotter=Spotter, OfflineRecognizer=types.SimpleNamespace(from_sense_voice=lambda **kw: Recognizer()))

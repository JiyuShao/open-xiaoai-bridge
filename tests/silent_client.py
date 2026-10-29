"""假 Client 的协议收集器：shell/播放/录音请求只应答，PCM只计数。"""

import json
from typing import Any


class SilentClient:
    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.audio_bytes = 0
        self.recording = False

    def receive(self, raw: str | bytes) -> str | None:
        if isinstance(raw, bytes):
            # 原生扩展的流帧是JSON字节；音频只记录大小，不解码或播放。
            stream = json.loads(raw)
            if stream.get('tag') != 'play' or not isinstance(stream.get('bytes'), list):
                raise ValueError('未知输出音频帧')
            self.audio_bytes += len(stream['bytes'])
            return None
        request = json.loads(raw).get('Request')
        if not isinstance(request, dict):
            raise ValueError('仅接受 Request 或播放帧')
        command = request.get('command')
        if command not in ('run_shell','start_recording','stop_recording','start_play','stop_play'):
            raise ValueError('未知设备协议动作，不执行')
        self.requests.append(request)
        if command in ('start_recording','stop_recording'):
            self.recording = command == 'start_recording'
        data = {'stdout':'{"code":0}','stderr':'','exit_code':0} if command == 'run_shell' else None
        return json.dumps({'Response':{'id':request['id'],'code':0,'data':data}})

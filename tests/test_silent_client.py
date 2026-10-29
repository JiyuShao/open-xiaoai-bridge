"""假Client覆盖对话输入与播放协议，但任何shell字符串均不能成为系统调用。"""

import json
import unittest
from tests.silent_client import SilentClient


class SilentClientTest(unittest.TestCase):
    def test_device_commands_only_record_and_respond(self):
        client=SilentClient()
        for index,command in enumerate(('start_recording','run_shell','stop_recording','start_play','stop_play')):
            reply=json.loads(client.receive(json.dumps({'Request':{
                'id':str(index),'command':command,'payload':'touch /tmp/must-not-execute'}})))
            self.assertEqual(reply['Response']['code'],0)
        self.assertEqual(len(client.requests),5)
        self.assertFalse(client.recording)
        self.assertIsNone(client.receive(json.dumps({'tag':'play','bytes':[0,1,0,1]}).encode()))
        self.assertEqual(client.audio_bytes,4)
        with self.assertRaises(ValueError):
            client.receive(json.dumps({'Request':{'id':'x','command':'exec','payload':'reboot'}}))

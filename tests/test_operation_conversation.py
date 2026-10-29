"""从连续对话入口验证原生操作、可信反馈和播放交接；传输全为替身。"""

import asyncio
import importlib
import json
import sys
import types
import unittest
from unittest import mock

from tests.test_native_wake import application


class OperationConversationTest(unittest.IsolatedAsyncioTestCase):
    async def conversation(self, *, continuous=False, cancel=False):
        with application(mixed=True) as (app, xiaoai, transport):
            from core.openai import OpenAIManager
            from core.openai_conversation import OpenAIConversationController
            from core.wakeup_session import EventManager
            from core.ref import set_vad
            events, requests = [], []
            config = app.config.get_app_config()
            temporary = app.config.get_config_path().parent
            config['openai'].update(history_path=str(temporary / 'sessions.db'),
                                    tools_enabled=True, tts_output_mode='play')
            config['xiaoai']['operation_db'] = str(temporary / 'operations.db')
            config['tts']['doubao'].update(app_id='mock', access_key='mock')
            OpenAIManager.initialize_from_config(enabled=True)
            runtime = importlib.import_module('open_xiaoai_server')
            for name in ('start_recording', 'stop_recording'):
                async def record(name=name):
                    events.append(name)
                setattr(runtime, name, record)
            runtime.begin_playback_session = lambda: 7
            runtime.stop_tts_playback = mock.Mock()
            feedback = asyncio.Event()
            async def play(text, **kwargs):
                events.append(('feedback', text, kwargs.get('explicit_dialect')))
                feedback.set()
                if cancel:
                    await asyncio.Event().wait()
            runtime.tts_play = play
            async def shell(command, *_args):
                events.append(('command', command))
                return json.dumps({'stdout': '{"code":0}', 'exit_code': 0})
            transport.side_effect = shell
            turns = iter(('播放音乐' if continuous else '设置闹钟', '拜拜'))
            sys.modules['core.services.audio.asr'] = types.SimpleNamespace(
                ASRService=types.SimpleNamespace(asr=lambda *args, **kwargs: next(turns)))
            vad = mock.Mock()
            def emit(target):
                loop = asyncio.get_running_loop()
                if target == 'speech':
                    loop.call_soon(EventManager.on_speech, b'file PCM')
                else:
                    loop.call_soon(EventManager.on_silence)
            vad.resume.side_effect = emit
            set_vad(vad)
            class Response:
                status = 200
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                async def json(self, **kwargs):
                    if len(requests) == 1:
                        return {'choices': [{'message': {'content': None, 'tool_calls': [{
                            'id': 'call-1', 'type': 'function', 'function': {
                                'name': 'xiaoai_command', 'arguments': json.dumps({
                                    'command': '播放音乐' if continuous else '明天七点叫我',
                                    'playback': 'continuous' if continuous else 'short'})}}]}}]}
                    return {'choices': [{'message': {'content': '已经完成了',
                        'voice': {'explicit_language': 'zh', 'explicit_dialect': 'sichuan'}}}]}
            class Session:
                def __init__(self, **kwargs): pass
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                def post(self, url, **kwargs):
                    requests.append(kwargs['json'])
                    return Response()
            with mock.patch('aiohttp.ClientSession', Session):
                controller = OpenAIConversationController()
                task = asyncio.create_task(controller.start())
                try:
                    if cancel:
                        await asyncio.wait_for(feedback.wait(), 2)
                        controller.stop()
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    else:
                        await asyncio.wait_for(task, 2)
                    records = await app.native_bridge.list()
                    return events, requests, records
                finally:
                    if not task.done():
                        controller.stop()
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    await OpenAIManager.close()

    async def test_short_operation_result_goes_back_to_model_and_feedback_remains_truthful(self):
        events, requests, records = await self.conversation()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, 'accepted')
        self.assertEqual(len(requests), 2)
        result = json.loads(requests[-1]['messages'][-1]['content'])
        self.assertEqual(result['status'], 'accepted')
        spoken = [event for event in events if isinstance(event, tuple) and event[0] == 'feedback']
        self.assertEqual(len(spoken), 1)
        self.assertIn('无法确认', spoken[0][1])
        self.assertNotIn('已经完成了', spoken[0][1])
        self.assertEqual(spoken[0][2], 'sichuan')
        self.assertEqual(sum(isinstance(e, tuple) and e[0] == 'command' for e in events), 1)
        self.assertEqual(events[-1], 'start_recording')

    async def test_music_starts_after_feedback_and_exits_without_new_recording_or_notify(self):
        events, _, records = await self.conversation(continuous=True)
        feedback_index = next(i for i,e in enumerate(events) if isinstance(e, tuple) and e[0]=='feedback')
        command_index = next(i for i,e in enumerate(events) if isinstance(e, tuple) and e[0]=='command')
        self.assertLess(feedback_index, command_index)
        self.assertEqual(events[command_index-1], 'start_recording')
        self.assertEqual(command_index, len(events)-1)
        self.assertEqual(records[0].status, 'accepted')

    async def test_cancelling_feedback_never_submits_prepared_music(self):
        events, _, records = await self.conversation(continuous=True, cancel=True)
        self.assertFalse(any(isinstance(e,tuple) and e[0]=='command' for e in events))
        self.assertEqual(records[0].status, 'rejected')
        self.assertEqual(records[0].error, 'cancelled_before_submit')


class OperationToolBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_call_is_deduplicated_and_unknown_never_becomes_completed(self):
        import tempfile
        from pathlib import Path
        from core.services.conversation_tools import ConversationTools
        from core.services.native_bridge import NativeBridgeService
        from core.services.operation_records import OperationRecords
        speaker = types.SimpleNamespace(ask_xiaoai_result=mock.AsyncMock(return_value=types.SimpleNamespace(
            stdout='', stderr='disconnected', exit_code=-1)))
        with tempfile.TemporaryDirectory() as directory:
            bridge = NativeBridgeService(OperationRecords(str(Path(directory)/'audit.db')), lambda: speaker, evidence='mock')
            tools = ConversationTools(bridge, 'test')
            args = json.dumps({'command':'设置闹钟', 'playback':'short'})
            first = await tools.invoke('same-call', 'xiaoai_command', args)
            self.assertEqual(first, await tools.invoke('same-call', 'xiaoai_command', args))
            self.assertEqual(first['status'], 'unknown')
            self.assertIn('未知', tools.feedback('闹钟设置成功'))
            speaker.ask_xiaoai_result.assert_awaited_once()
            with self.assertRaises(ValueError):
                await tools.invoke('same-call', 'xiaoai_command', json.dumps({'command':'不同指令','playback':'short'}))
            with self.assertRaises(ValueError):
                await tools.invoke('other', 'run_shell', '{"command":"reboot"}')

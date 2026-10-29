"""文件输入子进程只使用假设备；确定性音频标记不是声学识别质量证据。"""

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]


class FileAudioConversationTest(unittest.TestCase):
    def test_multi_turn_empty_multilingual_exit_and_timeout_use_existing_pipeline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def audio(name, marker):
                with wave.open(str(root/name),'wb') as stream:
                    stream.setparams((1,2,16000,0,'NONE','not compressed'))
                    stream.writeframes(struct.pack('<h',marker)*(512 if name=='wake.wav' else 2048))
            audio('wake.wav',100)
            cases = [(300,'你好'),(400,''),(500,'Hello'),(600,'你好噻'),(700,'没事了')]
            turns = []
            for marker, text in cases:
                name=f'{marker}.wav'
                audio(name,marker)
                turns.append({'file':name,'marker':marker,'mock_text':text})
            for name, selected in (('exit',turns),('timeout',turns[:1])):
                with self.subTest(name=name):
                    manifest=root/f'{name}.json'
                    manifest.write_text(json.dumps({'wake':'wake.wav','turns':selected}))
                    output=root/name
                    result=subprocess.run([sys.executable,'-m','scripts.doubao_audio_chat',
                        '--manifest',str(manifest),'--output',str(output)],cwd=ROOT,
                        env={'PATH':os.environ['PATH']},text=True,capture_output=True,timeout=15)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    report=json.loads((output/'report.json').read_text())
                    self.assertEqual(report['evidence'],'mock-models')
                    self.assertEqual(report['device_calls_executed'],0)
                    self.assertEqual(report['readers_after_stop'],0)
                    self.assertEqual(report['transcripts'],[text for _,text in cases] if name=='exit' else ['你好'])
                    expected=['你好','Hello','你好噻'] if name=='exit' else ['你好']
                    self.assertEqual([r['messages'][-1]['content'] for r in report['model_requests']],expected)
                    self.assertEqual(len(report['tts_requests']),len(expected))
                    if name=='exit':
                        self.assertEqual(len(report['model_requests'][-1]['messages']),5)

    def test_sentence_longer_than_one_second_uses_configured_listening_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, marker, frames in (('wake.wav',100,512),('long.wav',300,24000)):
                with wave.open(str(root/name),'wb') as stream:
                    stream.setparams((1,2,16000,0,'NONE','not compressed'))
                    stream.writeframes(struct.pack('<h',marker)*frames)
            manifest=root/'cases.json'
            manifest.write_text(json.dumps({'wake':'wake.wav','turns':[{'file':'long.wav','marker':300,'mock_text':'这是一句超过一秒的中文测试'}]}))
            output=root/'report'
            result=subprocess.run([sys.executable,'-m','scripts.doubao_audio_chat','--manifest',str(manifest),
                '--output',str(output),'--listen-timeout','3'],cwd=ROOT,
                env={'PATH':os.environ['PATH']},text=True,capture_output=True,timeout=12)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads((output/'report.json').read_text())
            self.assertEqual(report['transcripts'],['这是一句超过一秒的中文测试'])
            self.assertEqual(len(report['model_requests']),1)

    def test_missing_real_models_are_reported_without_claiming_real_recognition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with wave.open(str(root/'wake.wav'),'wb') as stream:
                stream.setparams((1,2,16000,0,'NONE','not compressed'))
                stream.writeframes(b'\0'*1024)
            manifest=root/'cases.json'
            manifest.write_text(json.dumps({'wake':'wake.wav','turns':[]}))
            result=subprocess.run([sys.executable,'-m','scripts.doubao_audio_chat',
                '--manifest',str(manifest),'--output',str(root/'report'),'--asr-mode','local','--models-dir',str(root/'missing-models')],cwd=ROOT,
                env={'PATH':os.environ['PATH']},text=True,capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('没有运行实际识别',result.stdout)
            self.assertFalse((root/'report/report.json').exists())

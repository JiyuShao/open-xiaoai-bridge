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

    def test_ha_history_rules_and_tasks_share_one_file_audio_conversation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            def tool(name,**arguments):
                return {'name':name,'arguments':arguments}
            plans=[
                ('通过HA打开客厅灯',[tool('device_control',entity_id='light.living',action='turn_on',route='auto')]),
                ('客厅灯今天开了多久',[tool('ha_history',entity_id='light.living',period='今天')]),
                ('以后说晚安模式就关灯',[tool('ha_rule_propose',rule={'kind':'scene','name':'晚安模式','phrase':'晚安模式','actions':[{'entity_id':'light.living','action':'turn_off'}]})]),
                ('确认',[tool('ha_pending'),tool('ha_rule_confirm',proposal_id='$proposal:rule_save')]),
                ('两小时后提醒喝水',[tool('ha_task_propose',task={'kind':'reminder','when':'2小时后','text':'喝水'})]),
                ('确认',[tool('ha_pending'),tool('ha_task_confirm',proposal_id='$proposal:task_save')]),
                ('晚安模式',[tool('ha_rules'),tool('ha_scene_run',rule_id='$first_rule')]),
                ('不用了',[]),
            ]
            with wave.open(str(root/'wake.wav'),'wb') as stream:
                stream.setparams((1,2,16000,0,'NONE','not compressed'))
                stream.writeframes(struct.pack('<h',100)*512)
            turns=[]
            for index,(text,tools) in enumerate(plans):
                marker=300+index*100
                name=f'{index}.wav'
                with wave.open(str(root/name),'wb') as stream:
                    stream.setparams((1,2,16000,0,'NONE','not compressed'))
                    stream.writeframes(struct.pack('<h',marker)*2048)
                turns.append({'file':name,'marker':marker,'mock_text':text,'mock_tools':tools})
            manifest=root/'cases.json'
            manifest.write_text(json.dumps({'wake':'wake.wav','turns':turns,'home_assistant':True}))
            result=subprocess.run([sys.executable,'-m','scripts.doubao_audio_chat','--manifest',str(manifest),'--output',str(root/'report')],cwd=ROOT,
                env={'PATH':os.environ['PATH']},text=True,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads((root/'report/report.json').read_text())
            self.assertEqual(report['transcripts'],[text for text,_ in plans])
            self.assertEqual(report['device_calls_executed'],0)
            self.assertEqual(report['readers_after_stop'],0)
            self.assertEqual(len(report['rules']),1)
            self.assertEqual(len(report['tasks']),1)
            self.assertEqual(report['tasks'][0]['state'],'scheduled')
            posts=[call for call in report['ha_requests'] if call['method']=='POST']
            self.assertEqual(len(posts),3)  # 开灯、保存场景、执行关灯；提醒只保存未到点。
            self.assertEqual(report['recorder_plan']['recorder']['purge_keep_days'],365)
            self.assertEqual(len(report['tts_requests']),7)
            self.assertTrue(any('/api/history/' in call['path'] for call in report['ha_requests']))

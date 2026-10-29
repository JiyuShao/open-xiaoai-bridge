"""仅以文件音频和假设备运行现有 KWS/VAD/ASR/对话管线，Hermes/TTS 永远为 Mock。"""

import argparse
from contextlib import AsyncExitStack
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path)) as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000) or not audio.getnframes():
            raise ValueError('文件须为非空16kHz单声道PCM16 WAV')
        if audio.getnframes() > 16000 * 60:
            raise ValueError('单个样本最长60秒')
        return audio.readframes(audio.getnframes())


async def run(args):
    async with AsyncExitStack() as cleanup:
        return await _run(args, cleanup)


async def _run(args, cleanup):
    from aiohttp import web
    from scripts.silent_audio_runtime import install
    cases = json.loads(args.manifest.read_text())
    if not isinstance(cases, dict) or not isinstance(cases.get('turns'), list):
        raise ValueError('清单需要 wake、turns 文件列表')
    wake = read_pcm(args.manifest.parent / cases['wake'])
    turns = [(case, read_pcm(args.manifest.parent / case['file'])) for case in cases['turns']]
    transcriptions = {int(case['marker']): case['mock_text'] for case, _ in turns}
    events, requests = [], []
    from scripts.silent_home_runtime import MockHomeAssistant, resolve_arguments
    fake_home = MockHomeAssistant() if cases.get('home_assistant') is True else None
    plans=[case for case,_ in turns if case.get('mock_text') and case['mock_text'] not in ('拜拜','再见','没事了','不用了')]
    model_turn=-1
    install(events, transcriptions, mock_models=args.asr_mode == 'mock')
    async def response(request):
        nonlocal model_turn
        body = await request.json()
        requests.append(body)
        messages=body['messages']
        if messages[-1]['role']=='user':
            model_turn+=1
        current=plans[model_turn] if model_turn<len(plans) else {}
        last_user=max(index for index,message in enumerate(messages) if message['role']=='user')
        tool_messages=[message for message in messages[last_user+1:] if message['role']=='tool']
        planned=current.get('mock_tools',[])
        if len(tool_messages)<len(planned):
            selected=planned[len(tool_messages)]
            results=[json.loads(message['content']).get('data',json.loads(message['content'])) for message in tool_messages]
            arguments=resolve_arguments(selected['arguments'],results)
            return web.json_response({'choices':[{'message':{'tool_calls':[{'id':f'file-{model_turn}-{len(tool_messages)}','type':'function','function':{'name':selected['name'],'arguments':json.dumps(arguments,ensure_ascii=False)}}]}}]})
        return web.json_response({'choices':[{'message':{'content':'模拟回复：' + messages[last_user]['content']}}]})
    server = web.Application()
    server.router.add_post('/v1/chat/completions', response)
    if fake_home:
        server.router.add_route('*','/api/{tail:.*}',fake_home.handle)
    runner = web.AppRunner(server)
    await runner.setup()
    cleanup.push_async_callback(runner.cleanup)
    # 绑定回环临时端口；这个脚本不接受外部后端地址或任何凭据。
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='xiaoai-audio-') as temporary:
        tmp = Path(temporary)
        model_root = args.models_dir or ROOT / 'core/models'
        if args.asr_mode == 'mock':
            model_root = tmp / 'models'
            model_root.mkdir()
            for name in ('silero_vad.onnx','encoder.onnx','decoder.onnx','joiner.onnx','tokens.txt','bpe.model','keywords.txt'):
                (model_root/name).write_bytes(b'explicit mock model')
            folder = model_root/'sherpa-onnx-sense-voice-mock'
            folder.mkdir()
            for name in ('tokens.txt','model.int8.onnx'):
                (folder/name).write_bytes(b'explicit mock model')
        elif not model_root.is_dir():
            await runner.cleanup()
            raise FileNotFoundError('缺少真实本地模型目录；没有运行实际识别')
        config = {'wakeup':{'keywords':['理想同学','豆包豆包'], 'routes':{'理想同学':'native','豆包豆包':'openai'}, 'timeout':args.listen_timeout or (1 if args.asr_mode == 'mock' else 20)},
                  'kws':{'highpass_hz':0, 'preroll_ms':0},
                  'vad':{'threshold':0.1,'min_speech_duration':32,'min_silence_duration':64},
                  'asr':{'model':'sense_voice','int8':True},
                  'xiaoai':{'operation_db':str(args.output/'operations.db')},
                  'openai':{'profile':'xiaoai-doubao','base_url':f'http://127.0.0.1:{port}/v1','model':'mock-hermes',
                    'session_key':'agent:xiaoai-doubao:file-test','history_path':str(args.output/'sessions.db'),
                    'response_timeout':2,'tts_provider':'doubao','tts_speaker':'zh_female_vv_uranus_bigtts',
                    'tools_enabled':fake_home is not None,
                    'exit_keywords':[]},
                  'tts':{'doubao':{'app_id':'test-only','access_key':'test-only'}}}
        if fake_home:
            config['home_assistant']={'enabled':True,'base_url':f'http://127.0.0.1:{port}',
                'token':'mock-home-only','entities':fake_home.ENTITIES,'workflows_path':str(args.output/'workflows.db')}
        profile = tmp/'config.py'
        profile.write_text('APP_CONFIG = '+repr(config))
        # 此进程专用于测试；覆盖配置，绝不读取生产环境中的凭据/模型后端。
        for name in list(os.environ):
            if any(secret in name for secret in ('TOKEN','KEY','SECRET','PASSWORD')):
                os.environ.pop(name)
        os.environ.update(CONFIG_PATH=str(profile), WAKE_MODE='mixed', OPENAI_ENABLE='1')
        from core.utils import file as file_utils
        original_model_path = file_utils.get_model_file_path
        if args.asr_mode == 'mock' or args.models_dir:
            file_utils.get_model_file_path = lambda name: str(model_root/name)
        from core.app import MainApp
        from core.ref import set_xiaoai
        from core.xiaoai import XiaoAI
        from core.openai import OpenAIManager
        from core.services.audio.stream import GlobalStream
        from core.services.audio.kws import KWS
        from core.services.audio.vad import VAD
        from core.services.audio.asr import ASRService
        from core.wakeup_session import EventManager
        app = MainApp(enable_xiaozhi=False, enable_openai=True, enable_mixed_wake=True)
        unused_loop, app.loop = app.loop, asyncio.get_running_loop()
        unused_loop.close()
        set_xiaoai(XiaoAI)
        OpenAIManager.initialize_from_config(enabled=True)
        transcript_results = []
        original_asr = ASRService.asr
        def collect_asr(data, sample_rate=16000):
            text = original_asr(data, sample_rate=sample_rate)
            transcript_results.append(text)
            return text
        ASRService.asr = collect_asr
        async def feed(pcm):
            for offset in range(0, len(pcm), 1024):
                GlobalStream.input(pcm[offset:offset+1024].ljust(1024,b'\0'))
                await asyncio.sleep(0.032)
        async def wait_until(condition, seconds=3):
            async with asyncio.timeout(seconds):
                while not condition():
                    await asyncio.sleep(0.01)
        try:
            if args.asr_mode == 'local':
                from core.services.audio.kws.keywords import main as generate_keywords
                if generate_keywords() != 0:
                    raise ValueError('真实模型的双词词表准备失败')
            ASRService.ensure_loaded()
            VAD.start()
            VAD.pause()
            KWS.start()
            await feed(wake)
            await wait_until(lambda: EventManager._openai_controller is not None and EventManager._openai_controller.is_listening)
            for case, pcm in turns:
                await wait_until(lambda: not VAD.paused and VAD.target == 'speech')
                await feed(pcm + b'\0' * 8192)
                await wait_until(lambda: EventManager._openai_task is None or (not VAD.paused and VAD.target == 'speech'))
                if EventManager._openai_task is None:
                    break
            await wait_until(lambda: EventManager._openai_task is None, seconds=config['wakeup']['timeout'] + 2)
            if len(transcript_results) != len(turns):
                raise ValueError(f'文件轮次未完整消费：输入{len(turns)}，识别轮次{len(transcript_results)}；请检查监听超时与退出词位置')
        finally:
            await EventManager.interrupt_mixed()
            KWS.stop()
            VAD.stop()
            await OpenAIManager.close()
            await runner.cleanup()
            file_utils.get_model_file_path = original_model_path
        home_report={}
        if fake_home:
            from core.services.device_history import recorder_config
            home_report={'ha_requests':fake_home.calls,'ha_rules':list(fake_home.rules.values()),
                'rules':await app.home_assistant.workflows.list_rules(),'tasks':await app.home_assistant.tasks.list(),
                'operations':[record.as_dict() for record in await app.native_bridge.list()],
                'recorder_plan':recorder_config(fake_home.ENTITIES)}
        result = {**home_report,'evidence' :'mock-models' if args.asr_mode=='mock' else 'local-models-mock-hermes-tts',
                  'transcripts':transcript_results, 'model_requests':requests,
                  'tts_requests':[e for e in events if e['kind']=='tts_play'],
                  'device_calls_executed':0, 'events':events, 'readers_after_stop':len(GlobalStream.readers),
                  'real_asr_quality': 'not-tested' if args.asr_mode == 'mock' else 'inspect-transcripts',
                  'limitations':['真实Hermes/真实TTS/真人远场/音箱/听感未验收']}
        (args.output/'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--asr-mode',choices=('mock','local'),default='mock')
    parser.add_argument('--models-dir',type=Path,help='仅local模式的真实模型目录（将重建关键词文件）')
    parser.add_argument('--listen-timeout',type=int,choices=range(1,121),metavar='1..120',help='覆盖监听与整句录制超时，local默认20秒')
    args = parser.parse_args(argv)
    from core.utils.logger import logger
    try:
        result = asyncio.run(run(args))
        logger.info(json.dumps(result, ensure_ascii=False),module='SilentAudio')
        return 0
    except Exception as exc:
        logger.error(f'文件语音验证未完成: {type(exc).__name__}: {exc}',module='SilentAudio')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

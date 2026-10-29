"""隔离容器中的静默套件入口，只运行Mock测试，不接受真实服务凭据。"""

import argparse
import asyncio
import socket
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from scripts.assert_silent_isolation import check
from core.utils.logger import logger


async def recovery(output):
    from core.services.chat_sessions import ChatSessions
    from core.services.operation_records import OperationRecords
    sessions=ChatSessions(str(output/'recovery-sessions.db'))
    operations=OperationRecords(str(output/'recovery-operations.db'))
    marker=output/'recovery.json'
    current=await sessions.load('isolated-recovery')
    if not marker.exists():
        await sessions.save(current,[{'role':'user','content':'mock input'},{'role':'assistant','content':'mock output'}])
        await operations.create_pending('unknown-recovery','isolated-recovery','never replay',{'operation':'mock-only'})
        state={'remote_key':current.remote_key,'first_container':socket.gethostname()}
        marker.write_text(json.dumps(state))
        return {'stage':'created','container':socket.gethostname()}
    state=json.loads(marker.read_text())
    if current.remote_key != state['remote_key'] or len(current.messages)!=2:
        raise RuntimeError('会话绑定卷恢复失败')
    record=await operations.get('unknown-recovery')
    if record is None or record.status!='unknown' or record.error!='process_restarted':
        raise RuntimeError('未知操作恢复失败')
    if not (output/'unittest.log').is_file():
        raise RuntimeError('上次测试产物未保留')
    if socket.gethostname()==state['first_container']:
        raise RuntimeError('必须重建独立容器验证恢复')
    return {'stage':'passed','first_container':state['first_container'],
            'container':socket.gethostname(),'operation_replayed':False,'session_restored':True,'artifacts_restored':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--recovery-only',action='store_true')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    isolation=check()
    restored=asyncio.run(recovery(args.output))
    if args.recovery_only:
        (args.output/'recovery-result.json').write_text(json.dumps(restored,indent=2))
        logger.info(json.dumps(restored),module='SilentSuite')
        return 0
    # 依赖镜像的生产启动开关不能影响隔离测试；每项测试自行声明模式。
    test_env={key:value for key,value in os.environ.items() if key not in (
        'NATIVE_WAKE_ENABLE','WAKE_MODE','OPENAI_ENABLE','OPENCLAW_ENABLE','QWENPAW_ENABLE','XIAOZHI_ENABLE')}
    result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],
                          env={**test_env,'OPENCLAW_LIVE_TEST':'0'},text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240)
    (args.output/'unittest.log').write_text(result.stdout)
    summary={'time':datetime.now(timezone.utc).isoformat(),'evidence':'server-mock',
             'success':result.returncode==0,'isolation':isolation,'recovery':restored,
             'real_hermes':'not-run','real_tts':'not-run','real_speaker':'not-run',
             'production_changes':False}
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    logger.info(json.dumps(summary),module='SilentSuite')
    return result.returncode


if __name__=='__main__':
    raise SystemExit(main())

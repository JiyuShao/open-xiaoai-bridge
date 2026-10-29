"""生成365天Recorder待部署配置与本地容量基线，不连接HA。"""
import argparse
from contextlib import closing
import asyncio
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from core.services.device_history import DeviceHistory,SHANGHAI,recorder_config
from core.utils.logger import logger


async def create_plan(entities,output):
    output.mkdir(parents=True,exist_ok=True)
    config=recorder_config(entities)
    # JSON是YAML子集，可合并进HA配置；本命令绝不写入HA配置目录。
    (output/'recorder.yaml').write_text(json.dumps(config,ensure_ascii=False,indent=2))
    now=datetime.now(SHANGHAI)
    samples=[{'entity_id':entity,'state':str(index),'last_changed':(now-timedelta(days=1)+timedelta(minutes=index)).isoformat()} for index in range(1441) for entity in entities]
    class MockRecorder:
        async def history(self,entity,start,end):
            return [[item for item in samples if item['entity_id']==entity]]
    queries=[]
    history=DeviceHistory(MockRecorder(),entities)
    for entity in entities:
        result=await history.query(entity,'过去24小时',now=now)
        queries.append({'entity_id':entity,'latency_ms':result['latency_ms'],'partial':result['partial']})
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        database=Path(temporary)/'mock-recorder.db'
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute('CREATE TABLE samples (entity_id TEXT, state TEXT, last_changed TEXT)')
            connection.executemany('INSERT INTO samples VALUES (:entity_id,:state,:last_changed)',samples)
        size=database.stat().st_size
    filesystem=os.statvfs(output)
    report={'evidence':'mock-baseline','sample_rows':len(samples),'sample_database_bytes':size,
            'available_disk_bytes':filesystem.f_bavail*filesystem.f_frsize,'queries':queries,
            'production_database_bytes':None,'production_disk_bytes':None,'applied_to_ha':False,
            'limitations':['Mock样本只用于基线，不代表生产数据库增长或真实HA查询延迟','真实Recorder容量、历史保留和缺口待专用环境验证']}
    (output/'recorder-baseline.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entities',type=Path,required=True,help='允许实体到名称/统计元数据的JSON字典')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=asyncio.run(create_plan(json.loads(args.entities.read_text()),args.output))
    logger.info(json.dumps(report,ensure_ascii=False),module='RecorderPlan')


if __name__=='__main__':
    main()

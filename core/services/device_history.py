"""允许实体的历史统计；时区、缺口和数值加总由服务器确定。"""
from datetime import datetime, timedelta, time
import math
from time import perf_counter
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')


def history_range(period, now=None):
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    midnight = datetime.combine(now.date(), time(), SHANGHAI)
    ranges = {
        '今天': (midnight, now), '昨天': (midnight-timedelta(days=1), midnight),
        '过去24小时': (now-timedelta(hours=24), now), '过去7天': (now-timedelta(days=7), now),
        '本月': (midnight.replace(day=1), now), '过去30天': (now-timedelta(days=30), now),
    }
    if period not in ranges:
        raise ValueError('unsupported_history_range')
    return ranges[period]


class DeviceHistory:
    def __init__(self, client, entities):
        self.client, self.entities = client, entities

    async def query(self, entity_id, period, *, now=None):
        if entity_id not in self.entities:
            raise ValueError('entity_not_allowed')
        start, end = history_range(period, now)
        timer = perf_counter()
        raw = await self.client.history(entity_id, start.isoformat(), end.isoformat())
        latency = round((perf_counter()-timer)*1000, 2)
        samples = []
        if not isinstance(raw,list):
            raise ValueError('invalid_history_response')
        for group in raw:
            if not isinstance(group,list):
                continue
            for item in group:
                if not isinstance(item,dict) or item.get('entity_id') != entity_id:
                    continue
                try:
                    stamp = datetime.fromisoformat(item['last_changed'].replace('Z','+00:00'))
                    if stamp.tzinfo is None:
                        continue
                    if stamp < end:
                        samples.append((stamp,item.get('state'),item.get('context',{})))
                except (KeyError,ValueError,TypeError):
                    continue
        samples.sort(key=lambda row: row[0])
        meta=self.entities[entity_id]
        result={'entity_id':entity_id,'name':meta['name'],'start':start.isoformat(),'end':end.isoformat(),
                'timezone':'Asia/Shanghai','fetch_latency_ms':latency,'evidence':'ha_recorder',
                'status':'ok' if samples else 'no_records','partial':True}
        if not samples:
            return {**result,'message':'该时间范围没有可用记录','latency_ms':round((perf_counter()-timer)*1000,2)}
        # 仅保留窗口开始时的最后一个状态，以及窗口内变化；不得累计窗口外事件。
        prior=[row for row in samples if row[0] <= start]
        samples=prior[-1:]+[row for row in samples if row[0]>start]
        missing=max(0.0,(min(samples[0][0],end)-start).total_seconds())
        on_seconds=0.0
        on_count=0
        intervals=[]
        numbers=[]
        weighted=0.0
        numeric_seconds=0.0
        previous=None
        previous_state=None
        accumulated=0.0
        resets=0
        for index,(stamp,state,context) in enumerate(samples):
            left=max(stamp,start)
            right=min(samples[index+1][0] if index+1<len(samples) else end,end)
            duration=max(0.0,(right-left).total_seconds())
            if state=='on':
                on_seconds+=duration
                if stamp>=start and previous_state != 'on':
                    on_count+=1
                intervals.append({'start':left.isoformat(),'end':right.isoformat(),
                                  'source':{'user_id':context.get('user_id'),'parent_id':context.get('parent_id')} if isinstance(context,dict) else None})
            previous_state=state
            if state in ('unknown','unavailable',None):
                missing+=duration
                previous=None
                continue
            try:
                number=float(state)
                if not math.isfinite(number):
                    raise ValueError()
            except (ValueError,TypeError):
                if entity_id.startswith("sensor."):
                    missing+=duration
                    previous=None
                continue
            numbers.append(number)
            weighted+=number*duration
            numeric_seconds+=duration
            if previous is not None:
                if number>=previous:
                    accumulated+=number-previous
                else:
                    resets+=1
                    accumulated+=max(0,number)
            previous=number
        result.update(unit=meta.get('unit'),missing_seconds=missing,partial=missing>0)
        if entity_id.split('.')[0] in ('light','switch','fan','binary_sensor'):
            result.update(on_seconds=on_seconds,on_count=on_count,intervals=intervals)
        elif numbers:
            result.update(minimum=min(numbers),maximum=max(numbers),average=weighted/numeric_seconds if numeric_seconds else None)
            if meta.get('history_kind')=='energy':
                result.update(accumulated=accumulated,resets=resets,accumulation_kind='positive_deltas_with_resets')
        else:
            result.update(status='no_records',message='没有可用的数值记录')
        result['latency_ms']=round((perf_counter()-timer)*1000,2)
        return result


def recorder_config(entities):
    """生成待用户部署的Recorder配置；不写入真实HA。"""
    if not entities or any(not isinstance(entity,str) or entity.count('.')!=1 for entity in entities):
        raise ValueError('recorder_requires_explicit_entities')
    return {'recorder':{'purge_keep_days':365,'auto_purge':True,'auto_repack':True,
            'include':{'entities':sorted(entities)},'exclude':{'entity_globs':['sensor.*_rssi','sensor.*_linkquality']}}}


def history_summary(result, period):
    """后台汇报使用统计事实生成口语，避免朗读内部字段或猜测。"""
    prefix=f"{result['name']}，{period}，"
    if result['status']=='no_records':
        return prefix+'没有可用记录。'
    if 'on_seconds' in result:
        minutes=int(result['on_seconds']//60)
        content=f"开启共{minutes//60}小时{minutes%60}分钟，新增开启{result['on_count']}次。"
    elif 'minimum' in result:
        unit=result.get('unit') or ''
        content=f"最低{result['minimum']:.2f}{unit}，最高{result['maximum']:.2f}{unit}"
        if result.get('average') is not None:
            content+=f"，平均{result['average']:.2f}{unit}"
        if 'accumulated' in result:
            content+=f"，累计增加{result['accumulated']:.2f}{unit}"
        content+='。'
    else:
        content='没有可用统计。'
    return prefix+content+('该时段记录不完整，统计仅基于已有记录。' if result.get('partial') else '')

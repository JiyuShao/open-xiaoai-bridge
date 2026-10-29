"""经确认的持久定时任务；错过或不确定的设备动作绝不追赶重放。"""
import asyncio
from datetime import datetime,timedelta
import re
import uuid

from core.services.device_history import SHANGHAI
from core.services.native_bridge import OperationOutcome


def parse_schedule(text, now=None):
    now=(now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if not isinstance(text,str):
        raise ValueError('invalid_schedule')
    duration=re.fullmatch(r'(\d{1,3})\s*(小时|分钟)后',text)
    clock=re.fullmatch(r'(今天|每天)\s*((?:[01]?\d|2[0-3]))[:：]([0-5]\d)',text)
    if duration:
        amount=int(duration[1])
        if not 1<=amount<=168:
            raise ValueError('duration_out_of_range')
        due=now+timedelta(**{'hours' if duration[2]=='小时' else 'minutes':amount})
        return due,False
    if clock:
        due=now.replace(hour=int(clock[2]),minute=int(clock[3]),second=0,microsecond=0)
        daily=clock[1]=='每天'
        if due<=now:
            if not daily:
                raise ValueError('今天的该时间已过，请重新指定')
            due+=timedelta(days=1)
        return due,daily
    raise ValueError('请提供N小时后、N分钟后、今天HH:MM或每天HH:MM')


class ScheduledTasks:
    def __init__(self,home,rules,announce):
        self.home,self.rules,self.store,self.announce=home,rules,rules.store,announce
        self._lock=asyncio.Lock()

    async def list(self):
        return await self.store.call('list','task')

    async def _owned(self,identifier):
        item=await self.store.call('get',identifier) if isinstance(identifier,str) else None
        if not item or item['kind']!='task':
            raise ValueError('task_not_owned')
        return item

    async def propose(self,session,spec,task_id=None,*,now=None):
        if not isinstance(spec,dict) or set(spec)-{'kind','when','text','actions','entity_id','period'}:
            raise ValueError('task_fields_not_allowed')
        kind=spec.get('kind')
        if kind=='device' and set(spec)=={'kind','when','actions'}:
            if not isinstance(spec['actions'],list) or any(not isinstance(action,dict) or set(action)!={'entity_id','action'} for action in spec['actions']):
                raise ValueError('device_task_requires_entity_actions')
            await self.rules.validate_actions(spec['actions'])
        elif kind=='reminder' and set(spec)=={'kind','when','text'}:
            if not isinstance(spec['text'],str) or not 1<=len(spec['text'])<=500:
                raise ValueError('invalid_reminder')
        elif kind=='history_report' and set(spec)=={'kind','when','entity_id','period'}:
            from core.services.device_history import history_range
            self.home._entity(spec['entity_id'])
            history_range(spec['period'],now)
        else:
            raise ValueError('unsupported_task_kind')
        due,daily=parse_schedule(spec['when'],now)
        old=await self._owned(task_id) if task_id else None
        identifier=task_id or 'xiaoai_task_'+uuid.uuid4().hex
        payload={'id':identifier,'version':old['version'] if old else 0,'spec':spec,
                 'due':due.isoformat(),'daily':daily}
        self.home.clear_device_confirmations(session)
        proposal=await self.store.call('propose',session,'task_save',payload)
        target=spec.get('text') or spec.get('entity_id') or '、'.join(self.home.allowed[action['entity_id']]['name']+' '+action['action'] for action in spec.get('actions',[]))
        return {'status':'needs_confirmation','proposal_id':proposal,'proposal':payload,
                'message':f"请确认：{target}；Asia/Shanghai {due.isoformat()}，{'每天' if daily else '一次'}；类型{kind}。"}

    async def propose_delete(self,session,identifier):
        old=await self._owned(identifier)
        self.home.clear_device_confirmations(session)
        proposal=await self.store.call('propose',session,'task_delete',{'id':identifier,'version':old['version']})
        return {'status':'needs_confirmation','proposal_id':proposal,'message':f"请确认删除任务{identifier}：{old['spec']}"}

    async def confirm(self,session,identifier,user_text,*,now=None):
        async with self._lock:
            proposal=await self.store.call('consume',identifier,session,user_text,('task_save','task_delete'))
            if proposal['state']!='consumed':
                return {'status':'rejected','message':'未确认或已过期，任务未变更'}
            request_id='schedule:'+identifier
            if not proposal.get('new'):
                record=await self.home.bridge.get(request_id)
                return record.as_dict() if record else {'status':'unknown','message':'确认已消费，结果未知，未重试'}
            payload=proposal['payload']
            async def invoke():
                current=await self.store.call('get',payload['id'])
                if (current['version'] if current else 0)!=payload['version']:
                    return OperationOutcome('rejected',False,error='proposal_version_stale',confirmation='not_submitted')
                if current and current.get('state')=='running':
                    return OperationOutcome('rejected',False,error='task_currently_running',confirmation='not_submitted')
                if proposal['action']=='task_save':
                    if datetime.fromisoformat(payload['due']) <= (now or datetime.now(SHANGHAI)):
                        return OperationOutcome('rejected',False,error='schedule_already_past',confirmation='not_submitted')
                    if payload['spec']['kind']=='device':
                        await self.rules.validate_actions(payload['spec']['actions'])
                    await self.store.call('put',payload['id'],'task',
                        {key:value for key,value in {**payload,'state':'scheduled','session':session,'last_result':None}.items() if key not in ('id','version')},payload['version'])
                elif proposal['action']=='task_delete':
                    await self._owned(payload['id'])
                    await self.store.call('delete',payload['id'],payload['version'])
                else:
                    return OperationOutcome('rejected',False,error='unsupported_task_proposal')
                return OperationOutcome('completed',True,confirmation='persisted_schedule')
            result=await self.home.bridge.run_operation(proposal['action'],'确认定时任务变更',payload,invoke,session_key=session,request_id=request_id)
            return result.as_dict()

    @staticmethod
    def _payload(item,**changes):
        return {key:value for key,value in {**item,**changes}.items() if key not in ('id','kind','version')}

    async def recover(self, now=None):
        """启动时只标记上次在途任务失败；不补发。"""
        now=(now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        for item in await self.list():
            if item['state']=='running':
                changes={'state':'failed','last_result':'failed','last_error':'process_restarted'}
                if item['daily']:
                    due=datetime.fromisoformat(item['due'])
                    next_due=now.replace(hour=due.hour,minute=due.minute,second=due.second,microsecond=0)
                    if next_due<=now:
                        next_due+=timedelta(days=1)
                    changes.update(state='scheduled',due=next_due.isoformat())
                await self.store.call('put',item['id'],'task',self._payload(item,**changes),item['version'])

    async def tick(self,now=None):
        now=(now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        async with self._lock:
            for item in await self.list():
                due=datetime.fromisoformat(item['due'])
                if item['state']!='scheduled' or due>now:
                    continue
                # CAS持久化领取发生在任何外部调用之前；不确定时禁止重试。
                await self.store.call('put',item['id'],'task',self._payload(item,state='running'),item['version'])
                late=(now-due).total_seconds()>60
                async def execute():
                    if late:
                        return OperationOutcome('rejected',False,error='missed',confirmation='not_submitted')
                    spec=item['spec']
                    if spec['kind']=='device':
                        await self.rules.validate_actions(spec['actions'])
                        results=[]
                        for action in spec['actions']:
                            result=await self.home.control(action['entity_id'],action['action'],{},session_key=item['session'],user_text='通过HA执行已确认定时任务')
                            results.append(result)
                            if result['status']!='completed':
                                return OperationOutcome('unknown',True,results,error='task_action_not_verified')
                        return OperationOutcome('completed',True,results,confirmation='observed_state')
                    text=spec.get('text')
                    if spec['kind']=='history_report':
                        from core.services.device_history import history_summary
                        text=history_summary(await self.home.history.query(spec['entity_id'],spec['period'],now=now),spec['period'])
                    succeeded=await self.announce(text)
                    return OperationOutcome('completed' if succeeded else 'unknown',True,error=None if succeeded else 'announce_failed',confirmation='playback_transport' if succeeded else 'unknown')
                operation='scheduled_device' if item['spec']['kind']=='device' else 'xiaoai_announce'
                result=await self.home.bridge.run_operation(operation,'定时任务到点',{'task_id':item['id'],'due':item['due'],'spec':item['spec']},execute,
                    session_key=item['session'],request_id='task:'+item['id']+':'+str(int(due.timestamp())),timeout_ms=120000)
                state='missed' if late else 'completed' if result.status=='completed' else 'failed'
                changes={'state':state,'last_result':state,'last_request_id':result.request_id}
                if item['daily']:
                    next_due=now.replace(hour=due.hour,minute=due.minute,second=due.second,microsecond=0)
                    if next_due<=now:
                        next_due+=timedelta(days=1)
                    changes.update(state='scheduled',due=next_due.isoformat())
                await self.store.call('put',item['id'],'task',self._payload(item,**changes),item['version']+1)

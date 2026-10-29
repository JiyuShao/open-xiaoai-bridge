"""场景/自动化的受限提案与确认；只管理本系统创建的规则。"""
import asyncio
import hashlib
import re
import uuid

from core.services.native_bridge import OperationOutcome
from core.services.workflow_store import WorkflowStore

PREFIX='xiaoai_bridge_'


class HomeWorkflows:
    def __init__(self, home, path, announce=None):
        self.home=home
        self.announce=announce
        self.store=WorkflowStore(path)
        self._lock=asyncio.Lock()

    async def validate_actions(self, actions):
        if not isinstance(actions,list) or not 1<=len(actions)<=10:
            raise ValueError('需要1到10个明确动作')
        services={item['entity_id']:item['actions'] for item in await self.home.services()}
        for item in actions:
            if isinstance(item,dict) and set(item)=={'announce'}:
                if not isinstance(item['announce'],str) or not 1<=len(item['announce'])<=500 or self.announce is None:
                    raise ValueError('invalid_automation_announcement')
                continue
            if not isinstance(item,dict) or set(item)!={'entity_id','action'}:
                raise ValueError('action_fields_not_allowed')
            entity_id,action=item['entity_id'],item['action']
            meta=self.home._entity(entity_id)
            await self.home.state(entity_id)
            if self.home.is_high_risk(entity_id):
                raise ValueError('高风险设备不允许后台规则执行')
            if action not in services.get(entity_id,[]):
                raise ValueError('service_not_available')
        return [dict(item) for item in actions]

    async def validate_condition(self, condition, *, trigger=False):
        if not isinstance(condition,dict):
            raise ValueError('invalid_condition')
        kind=condition.get('kind')
        if kind=='time' and trigger and set(condition)=={'kind','at'} and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',condition['at']):
            return {'platform':'time','at':condition['at']+':00'}
        if kind=='event' and trigger and set(condition)=={'kind','entity_id','event'}:
            self.home._entity(condition['entity_id'])
            await self.home.state(condition['entity_id'])
            # 事件限制为配置明确允许的枚举，不接受任意HA事件或管理事件。
            if condition['event'] not in self.home.allowed[condition['entity_id']].get('events',[]):
                raise ValueError('event_not_allowed')
            return {'platform':'event','event_type':condition['event'],'event_data':{'entity_id':condition['entity_id']}}
        entity_id=condition.get('entity_id')
        self.home._entity(entity_id)
        observed=await self.home.state(entity_id)
        base={'platform' if trigger else 'condition':kind,'entity_id':entity_id}
        if kind=='state' and set(condition)=={'kind','entity_id','state'}:
            defaults={'light':('on','off'),'switch':('on','off'),'fan':('on','off'),'binary_sensor':('on','off'),'cover':('open','closed','opening','closing'),'lock':('locked','unlocked'),'person':('home','not_home'),'device_tracker':('home','not_home')}
            allowed=self.home.allowed[entity_id].get('states',defaults.get(entity_id.split('.')[0],()))
            if condition['state'] not in allowed:
                raise ValueError('unsupported_state')
            base['to' if trigger else 'state']=condition['state']
        elif kind=='numeric_state' and set(condition)=={'kind','entity_id','above'} and type(condition['above']) in (int,float):
            import math
            try:
                numeric=float(observed['state'])
            except (ValueError,TypeError):
                raise ValueError('numeric_condition_requires_available_numeric_entity') from None
            if not entity_id.startswith('sensor.') or not math.isfinite(numeric) or not math.isfinite(condition['above']):
                raise ValueError('invalid_numeric_threshold')
            base['above']=condition['above']
        else:
            raise ValueError('unsupported_condition')
        return base

    async def propose_rule(self, session, rule, rule_id=None):
        if not isinstance(rule,dict) or set(rule)-{'kind','name','phrase','actions','trigger','conditions'}:
            raise ValueError('rule_fields_not_allowed')
        if rule.get('kind') not in ('scene','automation') or not isinstance(rule.get('name'),str) or not 1<=len(rule['name'])<=80:
            raise ValueError('invalid_rule')
        old=await self.owned(rule_id) if rule_id else None
        if old and old['kind']!=rule['kind']:
            raise ValueError('rule_kind_cannot_change')
        actions=await self.validate_actions(rule.get('actions'))
        identifier=rule_id or PREFIX+uuid.uuid4().hex
        if rule['kind']=='scene':
            if not isinstance(rule.get('phrase'),str) or not 1<=len(rule['phrase'])<=80 or any(key in rule for key in ('trigger','conditions')):
                raise ValueError('scene_requires_phrase_and_no_trigger')
            if any(item.get('action') not in ('turn_on','turn_off') for item in actions):
                raise ValueError('scene_supports_only_on_off')
            if len({item['entity_id'] for item in actions})!=len(actions):
                raise ValueError('duplicate_scene_target')
            body={'id':identifier,'name':rule['name'],'entities':{item['entity_id']:{'state':'on' if item['action']=='turn_on' else 'off'} for item in actions}}
        else:
            conditions=rule.get('conditions',[])
            if not isinstance(conditions,list) or len(conditions)>10 or 'phrase' in rule:
                raise ValueError('invalid_automation_conditions')
            body={'id':identifier,'alias':rule['name'],'mode':'single',
                  'trigger':[await self.validate_condition(rule.get('trigger'),trigger=True)],
                  'condition':[await self.validate_condition(item) for item in conditions],
                  'action':[({'event':'xiaoai_bridge_announce','event_data':{'rule_id':identifier,'rule_version':old['version']+1 if old else 1,'action_index':index}} if 'announce' in item else {'service':item['entity_id'].split('.')[0]+'.'+item['action'],'target':{'entity_id':item['entity_id']}}) for index,item in enumerate(actions)]}
        payload={'id':identifier,'version':old['version'] if old else 0,'rule':dict(rule),'body':body}
        self.home.clear_device_confirmations(session)
        proposal=await self.store.call('propose',session,'rule_save',payload)
        names='、'.join('播报 '+item['announce'] if 'announce' in item else self.home.allowed[item['entity_id']]['name']+' '+item['action'] for item in actions)
        return {'status':'needs_confirmation','proposal_id':proposal,'proposal':payload,
                'message':f"请确认保存{rule['kind']}「{rule['name']}」：{names}；触发：{rule.get('phrase',rule.get('trigger'))}；条件：{rule.get('conditions',[])}。仅影响列出的设备。"}

    async def owned(self, identifier):
        if not isinstance(identifier,str) or not re.fullmatch(PREFIX+r'[0-9a-f]{32}',identifier):
            raise ValueError('rule_not_owned')
        item=await self.store.call('get',identifier)
        if not item or item['kind'] not in ('scene','automation'):
            raise ValueError('rule_not_owned')
        return item

    async def list_rules(self):
        return (await self.store.call('list','scene'))+(await self.store.call('list','automation'))

    async def propose_rule_delete(self, session, identifier):
        old=await self.owned(identifier)
        payload={'id':identifier,'version':old['version'],'kind':old['kind']}
        self.home.clear_device_confirmations(session)
        proposal=await self.store.call('propose',session,'rule_delete',payload)
        return {'status':'needs_confirmation','proposal_id':proposal,'message':f"请确认删除{old['rule']['name']}及其所有动作。"}

    async def confirm(self, session, identifier, user_text):
        async with self._lock:
            proposal=await self.store.call('consume',identifier,session,user_text,('rule_save','rule_delete'))
            if proposal['state']!='consumed':
                return {'status':'rejected','message':'提案未确认或已过期，没有执行变更'}
            request_id='workflow:'+identifier
            if not proposal.get('new'):
                prior=await self.home.bridge.get(request_id)
                return prior.as_dict() if prior else {'status':'unknown','message':'确认已消费，结果未知，不自动重试'}
            payload=proposal['payload']
            async def invoke():
                current=await self.store.call('get',payload['id'])
                if (current['version'] if current else 0)!=payload['version']:
                    return OperationOutcome('rejected',False,error='proposal_version_stale',confirmation='not_submitted')
                if proposal['action']=='rule_save':
                    await self.validate_actions(payload['rule']['actions'])
                    kind=payload['rule']['kind']
                    if kind=='automation':
                        await self.validate_condition(payload['rule']['trigger'],trigger=True)
                        for condition in payload['rule'].get('conditions',[]):
                            await self.validate_condition(condition)
                    await self.home.client.save_rule(kind,payload['id'],payload['body'])
                    observed=await self.home.client.rule(kind,payload['id'])
                    if observed!=payload['body']:
                        return OperationOutcome('unknown',True,error='rule_save_not_observed')
                    await self.store.call('put',payload['id'],kind,{'rule':payload['rule'],'body':payload['body']},payload['version'])
                elif proposal['action']=='rule_delete':
                    await self.owned(payload['id'])
                    await self.home.client.delete_rule(payload['kind'],payload['id'])
                    if await self.home.client.rule(payload['kind'],payload['id']) is not None:
                        return OperationOutcome('unknown',True,error='rule_delete_not_observed')
                    await self.store.call('delete',payload['id'],payload['version'])
                else:
                    return OperationOutcome('rejected',False,error='unsupported_proposal')
                return OperationOutcome('completed',True,confirmation='observed_configuration')
            record=await self.home.bridge.run_operation(proposal['action'],'确认规则变更',payload,invoke,session_key=session,request_id=request_id)
            return record.as_dict()

    async def run_scene(self, identifier, session):
        item=await self.owned(identifier)
        if item['kind']!='scene':
            raise ValueError('only_owned_scenes_can_run')
        await self.validate_actions(item['rule']['actions'])
        outcomes=[]
        for action in item['rule']['actions']:
            outcomes.append(await self.home.control(action['entity_id'],action['action'],{},session_key=session,user_text='通过HA运行已确认场景'))
            if outcomes[-1]['status']!='completed':
                break
        return {'status':'completed' if all(item['status']=='completed' for item in outcomes) else 'unknown',
                'message':'场景动作已验证完成' if all(item['status']=='completed' for item in outcomes) else '场景未全部完成，未自动重试','outcomes':outcomes}


    async def handle_announcement(self, event):
        """事件只引用已确认规则动作，不接收远端任意播报文本。"""
        if not isinstance(event,dict) or not isinstance(event.get('data'),dict):
            raise ValueError('invalid_rule_event')
        data=event['data']
        item=await self.owned(data.get('rule_id'))
        index=data.get('action_index')
        actions=item['rule']['actions']
        if data.get('rule_version')!=item['version'] or item['kind']!='automation' or type(index) is not int or not 0<=index<len(actions) or 'announce' not in actions[index]:
            raise ValueError('rule_announcement_not_owned')
        event_id=event.get('context',{}).get('id')
        if not isinstance(event_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',event_id):
            raise ValueError('event_requires_deduplication_id')
        async def play():
            if await self.home.client.rule('automation',item['id'])!=item['body']:
                return OperationOutcome('rejected',False,error='rule_configuration_changed',confirmation='not_submitted')
            ok=await self.announce(actions[index]['announce'])
            return OperationOutcome('completed' if ok else 'unknown',True,error=None if ok else 'announce_failed',confirmation='playback_transport' if ok else 'unknown')
        dedup=hashlib.sha256(f"{event_id}:{item['id']}:{item['version']}:{index}".encode()).hexdigest()
        record=await self.home.bridge.run_operation('xiaoai_announce','自动化提醒',{'rule_id':item['id'],'action_index':index},play,
            session_key='automation:'+item['id'],request_id='rule-event:'+dedup,timeout_ms=120000)
        return record.as_dict()

    async def listen_announcements(self):
        from core.utils.logger import logger
        while True:
            try:
                async for event in self.home.client.announcement_events():
                    try:
                        await self.handle_announcement(event)
                    except (ValueError,TypeError,KeyError):
                        logger.warning('已拒绝不匹配的自动化提醒事件',module='HomeAssistant')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f'自动化提醒订阅中断: {type(exc).__name__}；不补播旧事件',module='HomeAssistant')
            await asyncio.sleep(5)

"""HA语义边界：凭据保留在传输层，只允许配置内实体和已注册动作。"""

import json
import re
import time
import uuid
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp

from core.services.native_bridge import NativeBridgeService, OperationOutcome


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str):
        url = urlsplit(base_url)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            raise ValueError('HA需要无凭据、路径或查询参数的HTTP服务地址')
        _ = url.port
        if not isinstance(token,str) or not token.strip():
            raise ValueError('HA运行时凭据缺失')
        self.base_url = base_url.rstrip('/')
        self._token = token

    async def _request(self, method: str, path: str, body: Any = None, params=None):
        """只由本模块构造路径，拒绝重定向，不把服务正文或凭据写入异常。"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.request(method,self.base_url+path,json=body,params=params,
                    headers={'Authorization':'Bearer '+self._token},allow_redirects=False) as response:
                    if response.status >= 300:
                        raise RuntimeError(f'home_assistant_http_{response.status}')
                    return await response.json(content_type=None)
        except (aiohttp.ClientError,TimeoutError) as exc:
            raise RuntimeError('home_assistant_unavailable') from None

    async def states(self):
        return await self._request('GET','/api/states')

    async def state(self, entity_id: str):
        return await self._request('GET','/api/states/'+quote(entity_id,safe=''))

    async def services(self):
        return await self._request('GET','/api/services')

    async def history(self, entity_id, start, end):
        return await self._request('GET','/api/history/period/'+quote(start,safe=''),
                                   params={'filter_entity_id':entity_id,'end_time':end})

    async def announcement_events(self):
        """只订阅本系统提醒事件；重连不重放旧事件，拒绝跨地址重定向。"""
        trace=aiohttp.TraceConfig()
        async def reject_redirect(*args):
            raise RuntimeError('home_assistant_redirect_denied')
        trace.on_request_redirect.append(reject_redirect)
        timeout=aiohttp.ClientTimeout(total=None,sock_connect=10,sock_read=90)
        async with aiohttp.ClientSession(timeout=timeout,trace_configs=[trace]) as session:
            async with session.ws_connect(self.base_url+'/api/websocket',heartbeat=30) as ws:
                if (await ws.receive_json()) .get('type')!='auth_required':
                    raise RuntimeError('home_assistant_auth_protocol')
                await ws.send_json({'type':'auth','access_token':self._token})
                if (await ws.receive_json()).get('type')!='auth_ok':
                    raise RuntimeError('home_assistant_auth_failed')
                await ws.send_json({'id':1,'type':'subscribe_events','event_type':'xiaoai_bridge_announce'})
                subscribed=await ws.receive_json()
                if subscribed.get('id')!=1 or subscribed.get('success') is not True:
                    raise RuntimeError('home_assistant_subscription_failed')
                async for message in ws:
                    if message.type==aiohttp.WSMsgType.TEXT:
                        data=json.loads(message.data)
                        if data.get('type')=='event' and data.get('id')==1 and data.get('event',{}).get('event_type')=='xiaoai_bridge_announce':
                            yield data['event']

    async def rule(self, kind, identifier):
        try:
            return await self._request('GET',f'/api/config/{kind}/config/{identifier}')
        except RuntimeError as exc:
            if str(exc)=='home_assistant_http_404':
                return None
            raise

    async def save_rule(self, kind, identifier, body):
        return await self._request('POST',f'/api/config/{kind}/config/{identifier}',body)

    async def delete_rule(self, kind, identifier):
        return await self._request('DELETE',f'/api/config/{kind}/config/{identifier}')

    async def call_service(self, entity_id, action, data):
        domain = entity_id.split('.')[0]
        return await self._request('POST',f'/api/services/{domain}/{action}',{**data,'entity_id':entity_id})


class HomeAssistantService:
    ACTIONS = {'light':('turn_on','turn_off'), 'switch':('turn_on','turn_off'),
               'fan':('turn_on','turn_off'), 'cover':('open_cover','close_cover','stop_cover'),
               'lock':('lock','unlock'), 'climate':('turn_on','turn_off')}

    def __init__(self, client: HomeAssistantClient, entities: dict[str, dict[str, Any]], bridge: NativeBridgeService):
        if not isinstance(entities,dict) or not entities or any(
            not isinstance(key,str) or len(key)>128 or key.count('.')!=1
            or not all(part.replace('_','').isalnum() for part in key.split('.'))
            or not isinstance(value,dict) or not isinstance(value.get('name'),str)
            for key,value in entities.items()
        ):
            raise ValueError('HA需配置明确的实体和真实展示名称')
        self.workflows = None
        self.tasks = None
        self.client, self.allowed, self.bridge = client, dict(entities), bridge
        from core.services.device_history import DeviceHistory
        self.history = DeviceHistory(client, self.allowed)
        self._confirmations: dict[str, tuple[str, dict, float]] = {}

    def configure_workflows(self, path, announce):
        from core.services.ha_workflows import HomeWorkflows
        from core.services.scheduled_tasks import ScheduledTasks
        self.workflows = HomeWorkflows(self,path,announce)
        self.tasks = ScheduledTasks(self,self.workflows,announce)

    def clear_device_confirmations(self, session):
        self._confirmations={key:value for key,value in self._confirmations.items() if value[0]!=session and value[2]>=time.monotonic()}

    def _entity(self, entity_id):
        if entity_id not in self.allowed:
            raise ValueError('entity_not_allowed')
        return self.allowed[entity_id]

    def _state(self, raw):
        entity_id = raw.get('entity_id') if isinstance(raw,dict) else None
        meta = self._entity(entity_id)
        attributes = raw.get('attributes',{})
        return {'entity_id':entity_id,'name':meta['name'],'state':raw.get('state'),
                'attributes':{key:attributes[key] for key in ('unit_of_measurement','device_class','supported_features') if key in attributes},
                'last_changed':raw.get('last_changed')}

    async def entities(self):
        states = await self.client.states()
        if not isinstance(states,list):
            raise ValueError('invalid_ha_states')
        return [self._state(state) for state in states if isinstance(state,dict) and state.get('entity_id') in self.allowed]

    async def state(self, entity_id):
        self._entity(entity_id)
        raw = await self.client.state(entity_id)
        if not isinstance(raw,dict) or raw.get('entity_id') != entity_id:
            raise ValueError('ha_state_entity_mismatch')
        return self._state(raw)

    async def services(self):
        raw = await self.client.services()
        if not isinstance(raw,list):
            raise ValueError('invalid_ha_services')
        available = {item['domain']:item.get('services',{}) for item in raw if isinstance(item,dict) and 'domain' in item}
        return [{'entity_id':entity_id,'actions':[action for action in self.ACTIONS.get(entity_id.split('.')[0],())
                 if action in available.get(entity_id.split('.')[0],{})]} for entity_id in self.allowed]

    def is_high_risk(self, entity_id):
        meta=self._entity(entity_id)
        return entity_id.split('.')[0] in ('lock','climate') or meta.get('risk')=='high' or any(word in meta['name'] for word in ('燃气','热水器','取暖器','门锁'))

    async def control(self, entity_id, action, data, route='auto', *, session_key, user_text, confirmation_id=None, request_id=None):
        meta = self._entity(entity_id)
        domain = entity_id.split('.')[0]
        if action not in self.ACTIONS.get(domain,()) or route not in ('auto','native','home_assistant') or not isinstance(data,dict):
            raise ValueError('unsupported_device_action')
        if data:
            raise ValueError('首版动作不接受任意服务参数')
        # 用户显式要求HA时，模型不能把路由覆盖为native。
        explicit_ha = bool(re.search(r'home\s*assistant|(?<![a-z])ha(?![a-z])',user_text,re.I))
        explicit_native = bool(re.search(r'原生|通过小爱|用小爱',user_text))
        saved_confirmation = self._confirmations.get(confirmation_id) if confirmation_id else None
        if confirmation_id:
            if not saved_confirmation or saved_confirmation[0]!=session_key or saved_confirmation[2]<time.monotonic() or user_text.strip() not in ('确认','同意','是的','确认执行') or any(saved_confirmation[1][key]!=value for key,value in {'entity_id':entity_id,'action':action,'data':data}.items()):
                raise ValueError('confirmation_not_valid_for_request')
            selected = saved_confirmation[1]['route']
        elif explicit_ha:
            selected = 'home_assistant'
        elif explicit_native:
            selected = 'native'
        else:
            if not isinstance(meta.get('native'),bool):
                return {'status':'needs_info','message':'目标路径尚不明确，请指定原生小爱或Home Assistant'}
            selected = 'native' if meta['native'] else 'home_assistant'
        if selected == 'native' and meta.get('native') is not True:
            return {'status':'needs_info','message':'没有已知原生能力映射，请确认目标路径'}
        payload={'entity_id':entity_id,'action':action,'data':data,'route':selected}
        high_risk = self.is_high_risk(entity_id)
        if high_risk:
            if confirmation_id:
                saved = self._confirmations.get(confirmation_id)
                if not saved or saved[0] != session_key or saved[1] != payload or time.monotonic()>saved[2] or user_text.strip() not in ('确认','同意','是的','确认执行'):
                    raise ValueError('confirmation_not_valid_for_request')
                self._confirmations.pop(confirmation_id)
            else:
                self.clear_device_confirmations(session_key)
                if self.workflows:
                    await self.workflows.store.call('supersede',session_key)
                confirmation_id=uuid.uuid4().hex
                self._confirmations[confirmation_id]=(session_key,payload,time.monotonic()+300)
                return {'status':'needs_confirmation','confirmation_id':confirmation_id,'proposal':payload,
                        'message':f"请确认：对{meta['name']}执行{action}，路径{selected}。"}
        if selected == 'native':
            verb={'turn_on':'打开','turn_off':'关闭','open_cover':'打开','close_cover':'关闭','stop_cover':'停止','lock':'锁上','unlock':'解锁'}[action]
            record=await self.bridge.submit(verb+meta['name'],session_key=session_key,request_id=request_id,silent=True,allow_high_risk=high_risk,decision={**payload,"confirmed":high_risk})
            return record.as_dict()
        async def invoke():
            await self.client.call_service(entity_id,action,data)
            state=await self.state(entity_id)
            expected={'turn_on':'on','turn_off':'off','open_cover':'open','close_cover':'closed','lock':'locked','unlock':'unlocked'}.get(action)
            if expected and state['state']==expected:
                return OperationOutcome('completed',True,state,confirmation='observed_state')
            return OperationOutcome('unknown',True,state,error='target_state_not_observed',confirmation='unknown')
        record=await self.bridge.run_operation('ha_control',meta['name']+' '+action,{**payload,'confirmed':high_risk},invoke,
                                                session_key=session_key,request_id=request_id)
        return record.as_dict()

    @property
    def definitions(self):
        read = [{"type":"function","function":{
            "name":name,"description":description,"parameters":{"type":"object","properties":properties,
                "required":list(properties),"additionalProperties":False}}} for name,description,properties in (
            ('ha_entities','读取允许的实体与当前状态，无需确认',{}),
            ('ha_state','读取指定允许实体的状态，无需确认',{'entity_id':{'type':'string','enum':list(self.allowed)}}),
            ('ha_history','服务器统计设备历史；缺口不猜测',{'entity_id':{'type':'string','enum':list(self.allowed)},'period':{'type':'string','enum':['今天','昨天','过去24小时','过去7天','本月','过去30天']}}),
            ('ha_pending','读取当前session尚待确认的提案；用户说确认后先查询，再用对应确认工具提交ID',{}),
            ('ha_services','列出允许的设备动作及副作用边界；不能调用管理接口',{}),
        )]
        read.append({'type':'function','function':{'name':'device_control','description':'控制允许实体；服务端选择唯一原生/HA路径。歧义需询问，高风险需用户下一轮明确确认。',
            'parameters':{'type':'object','properties':{'entity_id':{'type':'string','enum':list(self.allowed)},
                'action':{'type':'string'},'route':{'type':'string','enum':['auto','native','home_assistant']},
                'confirmation_id':{'type':'string'}},'required':['entity_id','action','route'],'additionalProperties':False}}})
        if self.workflows:
            from core.services.home_tools import definitions
            read.extend(definitions())
        return read

    async def invoke(self, name, args, session_key, user_text=""):
        if self.workflows and (name.startswith('ha_rule') or name.startswith('ha_task') or name=='ha_scene_run'):
            from core.services.home_tools import invoke
            return await invoke(self,name,args,session_key,user_text)
        if name == 'device_control' and set(args) <= {'entity_id','action','route','confirmation_id'} and {'entity_id','action','route'} <= set(args):
            return await self.control(args['entity_id'],args['action'],{},args['route'],session_key=session_key,user_text=user_text,confirmation_id=args.get('confirmation_id'))
        if name == 'ha_pending' and args=={}:
            pending=await self.workflows.store.call('pending',session_key) if self.workflows else []
            pending.extend({'confirmation_id':identifier,'action':'device_control','proposal':payload} for identifier,(session,payload,expires) in self._confirmations.items() if session==session_key and expires>=time.monotonic())
            return pending
        if name == 'ha_history' and set(args)=={'entity_id','period'}:
            return await self.history.query(args['entity_id'],args['period'])
        if name == 'ha_entities' and args == {}:
            return await self.entities()
        if name == 'ha_state' and set(args)=={'entity_id'}:
            return await self.state(args['entity_id'])
        if name == 'ha_services' and args == {}:
            return {'services':await self.services(),'mutations_require_policy':True}
        raise ValueError('home_assistant_tool_not_allowed')

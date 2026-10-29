"""本进程HA协议替身，仅供隔离的文件音频验收，不接受外部地址。"""
from datetime import datetime,timedelta
import json

from aiohttp import web


class MockHomeAssistant:
    ENTITIES={'light.living':{'name':'客厅灯','native':False},
              'switch.mijia':{'name':'米家插座','native':True},
              'switch.heater':{'name':'取暖器','native':False,'risk':'high'},
              'sensor.temperature':{'name':'室温','native':False,'unit':'°C'}}

    def __init__(self):
        self.calls=[]
        self.rules={}
        self.states={entity:{'entity_id':entity,'state':'20' if entity.startswith('sensor.') else 'off','attributes':{}}
                     for entity in self.ENTITIES}

    async def handle(self,request):
        if request.headers.get('Authorization')!='Bearer mock-home-only':
            return web.json_response({},status=401)
        body=await request.json() if request.can_read_body else None
        path=request.path
        self.calls.append({'method':request.method,'path':path,'body':body})
        if path=='/api/states':
            return web.json_response(list(self.states.values()))
        if path.startswith('/api/states/'):
            item=self.states.get(path.rsplit('/',1)[1])
            return web.json_response(item or {},status=200 if item else 404)
        if path=='/api/services':
            return web.json_response([{'domain':domain,'services':{'turn_on':{},'turn_off':{}}} for domain in ('light','switch')])
        if path.startswith('/api/services/') and request.method=='POST':
            entity=body.get('entity_id') if isinstance(body,dict) else None
            if entity not in self.states or path.rsplit('/',1)[1] not in ('turn_on','turn_off'):
                return web.json_response({},status=400)
            self.states[entity]['state']='on' if path.endswith('/turn_on') else 'off'
            return web.json_response([])
        if path.startswith('/api/history/period/'):
            entity=request.query.get('filter_entity_id')
            if entity not in self.states:
                return web.json_response([],status=404)
            start=datetime.fromisoformat(path.split('/period/',1)[1])
            end=datetime.fromisoformat(request.query['end_time'])
            middle=start+(end-start)/2
            values=('20','22') if entity.startswith('sensor.') else ('off','on')
            return web.json_response([[{'entity_id':entity,'state':state,'last_changed':stamp.isoformat()} for state,stamp in zip(values,(start,middle))]])
        if path.startswith('/api/config/'):
            parts=path.split('/')
            if len(parts)!=6 or parts[3] not in ('scene','automation') or not parts[-1].startswith('xiaoai_bridge_'):
                return web.json_response({},status=403)
            if request.method=='POST':
                self.rules[path]=body
                return web.json_response({})
            if request.method=='DELETE':
                self.rules.pop(path,None)
                return web.json_response({})
            return web.json_response(self.rules.get(path,{}),status=200 if path in self.rules else 404)
        return web.json_response({},status=404)


def resolve_arguments(value,tool_results):
    """Mock模型只从当轮工具回执取得待确认ID，不偷读Bridge内部存储。"""
    if isinstance(value,dict):
        return {key:resolve_arguments(item,tool_results) for key,item in value.items()}
    if isinstance(value,list):
        return [resolve_arguments(item,tool_results) for item in value]
    if isinstance(value,str) and value.startswith('$proposal:'):
        action=value.split(':',1)[1]
        candidates=[item for result in tool_results for item in (result if isinstance(result,list) else []) if item.get('action')==action]
        if len(candidates)!=1:
            raise ValueError('Mock模型没有唯一的待确认提案')
        return candidates[0].get('proposal_id',candidates[0].get('confirmation_id'))
    if value=='$first_rule':
        candidates=[item for result in tool_results for item in (result if isinstance(result,list) else []) if item.get('id','').startswith('xiaoai_bridge_')]
        if len(candidates)!=1:
            raise ValueError('Mock模型没有唯一场景')
        return candidates[0]['id']
    return value

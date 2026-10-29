"""受控HA入口对假的HTTP服务；不读取真实凭据或访问家庭设备。"""

from contextlib import asynccontextmanager
import tempfile
from pathlib import Path
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer


@asynccontextmanager
async def home_environment():
    from core.services.home_assistant import HomeAssistantClient, HomeAssistantService
    from core.services.native_bridge import NativeBridgeService
    from core.services.operation_records import OperationRecords
    calls=[]
    states={'light.living':{'entity_id':'light.living','state':'off','attributes':{'friendly_name':'客厅灯','access_token':'must-not-leak'}},
            'switch.private':{'entity_id':'switch.private','state':'on','attributes':{}}}
    async def handle(request):
        calls.append((request.method,request.path,await request.json() if request.can_read_body else None))
        if request.headers.get('Authorization') != 'Bearer mock-secret':
            return web.json_response({'error':'mock-secret'},status=401)
        if request.path=='/api/states':
            return web.json_response(list(states.values()))
        if request.path.startswith('/api/states/'):
            state=states.get(request.match_info['tail'].split('/')[-1])
            return web.json_response(state if state else {},status=200 if state else 404)
        if request.method=='POST' and request.path.startswith('/api/services/'):
            body=calls[-1][2]
            entity_id=body['entity_id']
            if not states[entity_id].get('block_change'):
                states[entity_id]['state']='on' if request.path.endswith('/turn_on') else 'off'
            return web.json_response([])
        if request.path=='/api/services':
            return web.json_response([{'domain':'light','services':{'turn_on':{'name':'打开灯'},'turn_off':{'name':'关闭灯'}}},
                                     {'domain':'shell_command','services':{'reboot':{}}}])
        return web.json_response({'error':'not available'},status=404)
    app=web.Application()
    app.router.add_route('*','/{tail:.*}',handle)
    server=TestServer(app,host='127.0.0.1')
    await server.start_server()
    with tempfile.TemporaryDirectory() as temporary:
        bridge=NativeBridgeService(OperationRecords(str(Path(temporary)/'audit.db')),lambda:None,evidence='mock')
        client=HomeAssistantClient(str(server.make_url('/')),'mock-secret')
        service=HomeAssistantService(client,{'light.living':{'name':'客厅灯','native':False}},bridge)
        try:
            yield service,calls,states
        finally:
            await server.close()


class HomeAssistantBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_allowed_entities_state_and_services_without_secrets_or_admin(self):
        async with home_environment() as (service,calls,_):
            entities=await service.entities()
            self.assertEqual([e['entity_id'] for e in entities],['light.living'])
            self.assertEqual(entities[0]['name'],'客厅灯')
            state=await service.state('light.living')
            self.assertEqual(state['state'],'off')
            self.assertNotIn('must-not-leak',str(state))
            services=await service.services()
            self.assertEqual(services,[{'entity_id':'light.living','actions':['turn_on','turn_off']}])
            count=len(calls)
            with self.assertRaises(ValueError):
                await service.state('switch.private')
            self.assertEqual(len(calls),count)

    async def test_embedded_credentials_and_unregistered_endpoints_are_rejected(self):
        from core.services.home_assistant import HomeAssistantClient
        for url in ('http://user:pass@localhost:8123','http://localhost:8123?token=secret','file:///tmp/secrets'):
            with self.assertRaises(ValueError):
                HomeAssistantClient(url,'mock-secret')


class HomeAssistantControlTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_ha_and_native_routing_never_double_send(self):
        from unittest.mock import AsyncMock
        import types
        async with home_environment() as (service,calls,states):
            speaker=types.SimpleNamespace(ask_xiaoai_result=AsyncMock(return_value=types.SimpleNamespace(stdout='{"code":0}',stderr='',exit_code=0)))
            service.bridge.speaker_provider=lambda:speaker
            service.allowed['light.living']['native']=True
            native=await service.control('light.living','turn_on',{},'auto',session_key='test',user_text='打开客厅灯')
            self.assertEqual(native['status'],'accepted')
            self.assertFalse(any(call[0]=='POST' for call in calls))
            ha=await service.control('light.living','turn_off',{},'auto',session_key='test',user_text='通过HA关闭客厅灯')
            self.assertEqual(ha['status'],'completed')
            speaker.ask_xiaoai_result.assert_awaited_once()
            self.assertEqual(sum(call[0]=='POST' for call in calls),1)

    async def test_ambiguity_and_high_risk_require_bound_explicit_confirmation(self):
        async with home_environment() as (service,calls,states):
            service.allowed['light.living']['native']=None
            result=await service.control('light.living','turn_on',{},'auto',session_key='test',user_text='打开灯')
            self.assertEqual(result['status'],'needs_info')
            self.assertEqual(calls,[])
            service.allowed['light.living'].update(native=False,risk='high')
            proposal=await service.control('light.living','turn_on',{},'auto',session_key='test',user_text='打开灯')
            self.assertEqual(proposal['status'],'needs_confirmation')
            self.assertEqual(calls,[])
            with self.assertRaises(ValueError):
                await service.control('light.living','turn_off',{},'auto',session_key='other',user_text='确认',confirmation_id=proposal['confirmation_id'])

    async def test_confirmed_risky_change_is_bound_and_consumed_once(self):
        async with home_environment() as (service,calls,states):
            service.allowed['light.living'].update(native=False,risk='high')
            proposal=await service.control('light.living','turn_on',{},session_key='test',user_text='打开灯')
            result=await service.control('light.living','turn_on',{},session_key='test',user_text='确认',confirmation_id=proposal['confirmation_id'])
            self.assertEqual(result['status'],'completed')
            with self.assertRaises(ValueError):
                await service.control('light.living','turn_on',{},session_key='test',user_text='确认',confirmation_id=proposal['confirmation_id'])
            self.assertEqual(sum(call[0]=='POST' for call in calls),1)

    async def test_unobserved_state_is_unknown_and_arbitrary_service_data_is_rejected(self):
        async with home_environment() as (service,calls,states):
            states['light.living']['block_change']=True
            result=await service.control('light.living','turn_on',{},session_key='test',user_text='打开灯')
            self.assertEqual(result['status'],'unknown')
            self.assertEqual(result['confirmation'],'unknown')
            with self.assertRaises(ValueError):
                await service.control('light.living','turn_on',{'url':'http://private'},session_key='test',user_text='打开灯')
            self.assertEqual(sum(call[0]=='POST' for call in calls),1)

    async def test_agent_cannot_send_native_and_ha_for_the_same_turn(self):
        from core.services.conversation_tools import ConversationTools
        import json
        async with home_environment() as (service,calls,states):
            tools=ConversationTools(service.bridge,'test',home=service,user_text='通过HA打开客厅灯')
            names=[item['function']['name'] for item in tools.definitions]
            self.assertIn('ha_state',names)
            self.assertNotIn('run_shell',names)
            await tools.invoke('ha-one','device_control',json.dumps({'entity_id':'light.living','action':'turn_on','route':'auto'}))
            with self.assertRaises(ValueError):
                await tools.invoke('native-two','xiaoai_command',json.dumps({'command':'打开客厅灯','playback':'short'}))
            self.assertEqual(sum(call[0]=='POST' for call in calls),1)

    async def test_freeform_tool_cannot_bypass_semantic_route_or_confirmation(self):
        from core.services.conversation_tools import ConversationTools
        import json
        async with home_environment() as (service,calls,states):
            for text in ('通过HA打开客厅灯','unlock the front door','把大门打开'):
                tools=ConversationTools(service.bridge,'test',home=service,user_text=text)
                with self.assertRaises(ValueError):
                    await tools.invoke('bad','xiaoai_command',json.dumps({'command':text,'playback':'short'}))
            self.assertEqual(calls,[])

    async def test_native_decision_is_audited_and_ha_tool_retries_are_deduplicated(self):
        from core.services.conversation_tools import ConversationTools
        from unittest.mock import AsyncMock
        import types,json
        async with home_environment() as (service,calls,states):
            speaker=types.SimpleNamespace(ask_xiaoai_result=AsyncMock(return_value=types.SimpleNamespace(stdout='{"code":0}',stderr='',exit_code=0)))
            service.bridge.speaker_provider=lambda:speaker
            service.allowed['light.living']['native']=True
            record=await service.control('light.living','turn_on',{},session_key='test',user_text='开灯')
            self.assertEqual(record['parameters']['decision']['route'],'native')
            self.assertEqual(record['parameters']['decision']['entity_id'],'light.living')
            tools=ConversationTools(service.bridge,'test',home=service,user_text='通过HA关闭灯')
            args=json.dumps({'entity_id':'light.living','action':'turn_off','route':'auto'})
            first=await tools.invoke('one','device_control',args)
            self.assertEqual(await tools.invoke('one','device_control',args),first)
            self.assertEqual(sum(call[0]=='POST' for call in calls),1)

    async def test_model_route_is_not_user_authorization(self):
        from unittest.mock import AsyncMock
        import types
        async with home_environment() as (service,calls,states):
            service.allowed['light.living']['native']=None
            result=await service.control('light.living','turn_on',{},'home_assistant',session_key='test',user_text='打开灯')
            self.assertEqual(result['status'],'needs_info')
            self.assertEqual(calls,[])
            service.allowed['light.living']['native']=True
            speaker=types.SimpleNamespace(ask_xiaoai_result=AsyncMock(return_value=types.SimpleNamespace(stdout='{"code":0}',stderr='',exit_code=0)))
            service.bridge.speaker_provider=lambda:speaker
            result=await service.control('light.living','turn_on',{},'home_assistant',session_key='test',user_text='打开灯')
            self.assertEqual(result['parameters']['decision']['route'],'native')
            self.assertFalse(any(call[0]=='POST' for call in calls))


class HomeAnnouncementProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_auth_and_subscription_only_yield_bridge_events(self):
        from core.services.home_assistant import HomeAssistantClient
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        received=[]
        async def websocket(request):
            ws=web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({'type':'auth_required'})
            received.append(await ws.receive_json())
            await ws.send_json({'type':'auth_ok'})
            received.append(await ws.receive_json())
            await ws.send_json({'id':1,'type':'result','success':True})
            await ws.send_json({'id':1,'type':'event','event':{'event_type':'unrelated','data':{}}})
            await ws.send_json({'id':1,'type':'event','event':{'event_type':'xiaoai_bridge_announce','data':{'rule_id':'mock'},'context':{'id':'one'}}})
            await ws.close()
            return ws
        app=web.Application()
        app.router.add_get('/api/websocket',websocket)
        server=TestServer(app,host='127.0.0.1')
        await server.start_server()
        try:
            client=HomeAssistantClient(str(server.make_url('/')),'mock-secret')
            results=[event async for event in client.announcement_events()]
            self.assertEqual(len(results),1)
            self.assertEqual(received[1],{'id':1,'type':'subscribe_events','event_type':'xiaoai_bridge_announce'})
            self.assertNotIn('mock-secret',str(results))
        finally:
            await server.close()

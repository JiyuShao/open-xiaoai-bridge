"""规则确认、命名空间和恢复通过公开工具边界验证。"""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock
from test_home_assistant import home_environment


class RulesTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_requires_bound_confirmation_and_can_only_edit_owned_rules(self):
        from core.services.ha_workflows import HomeWorkflows
        async with home_environment() as (home,calls,states):
            home.client.save_rule=AsyncMock(return_value={})
            home.client.rule=AsyncMock()
            home.client.delete_rule=AsyncMock(return_value={})
            with tempfile.TemporaryDirectory() as directory:
                path=str(Path(directory)/'workflows.db')
                rules=HomeWorkflows(home,path)
                proposal=await rules.propose_rule('test',{'kind':'scene','name':'晚安','phrase':'晚安模式','actions':[{'entity_id':'light.living','action':'turn_off'}]})
                self.assertIn('客厅灯',proposal['message'])
                home.client.rule.return_value=proposal['proposal']['body']
                home.client.save_rule.assert_not_awaited()
                with self.assertRaises(ValueError):
                    await rules.confirm('other',proposal['proposal_id'],'确认')
                restored=HomeWorkflows(home,path)
                result=await restored.confirm('test',proposal['proposal_id'],'确认')
                self.assertEqual(result['status'],'completed')
                home.client.save_rule.assert_awaited_once()
                objects=await restored.list_rules()
                self.assertEqual(len(objects),1)
                self.assertTrue(objects[0]['id'].startswith('xiaoai_bridge_'))
                with self.assertRaises(ValueError):
                    await restored.propose_rule_delete('test','someone_elses_rule')
                deletion=await restored.propose_rule_delete('test',objects[0]['id'])
                await restored.confirm('test',deletion['proposal_id'],'不同意')
                home.client.delete_rule.assert_not_awaited()

    async def test_unavailable_services_or_dangerous_targets_cannot_be_taught(self):
        from core.services.ha_workflows import HomeWorkflows
        async with home_environment() as (home,calls,states):
            with tempfile.TemporaryDirectory() as directory:
                rules=HomeWorkflows(home,str(Path(directory)/'workflows.db'))
                for action in ('reboot','unlock'):
                    with self.assertRaises(ValueError):
                        await rules.propose_rule('test',{'kind':'scene','name':'bad','phrase':'bad','actions':[{'entity_id':'light.living','action':action}]})
                home.allowed['light.living']['risk']='high'
                with self.assertRaises(ValueError):
                    await rules.propose_rule('test',{'kind':'scene','name':'bad','phrase':'bad','actions':[{'entity_id':'light.living','action':'turn_on'}]})

    async def test_stale_entities_and_domain_invalid_states_are_not_saved(self):
        from core.services.ha_workflows import HomeWorkflows
        async with home_environment() as (home,calls,states):
            with tempfile.TemporaryDirectory() as directory:
                rules=HomeWorkflows(home,str(Path(directory)/'rules.db'))
                home.allowed['light.stale']={'name':'已移除的灯','native':False}
                with self.assertRaises(Exception):
                    await rules.propose_rule('test',{'kind':'scene','name':'bad','phrase':'bad','actions':[{'entity_id':'light.stale','action':'turn_on'}]})
                with self.assertRaises(ValueError):
                    await rules.propose_rule('test',{'kind':'automation','name':'bad','actions':[{'entity_id':'light.living','action':'turn_on'}],'trigger':{'kind':'state','entity_id':'light.living','state':'locked'}})

    async def test_automation_notification_uses_owned_message_and_deduplicates_events(self):
        from core.services.ha_workflows import HomeWorkflows
        async with home_environment() as (home,calls,states):
            announce=AsyncMock(return_value=True)
            home.client.save_rule=AsyncMock(return_value={})
            home.client.rule=AsyncMock()
            with tempfile.TemporaryDirectory() as directory:
                rules=HomeWorkflows(home,str(Path(directory)/'workflows.db'),announce)
                proposal=await rules.propose_rule('test',{'kind':'automation','name':'开灯提醒','trigger':{'kind':'state','entity_id':'light.living','state':'on'},'actions':[{'announce':'客厅灯亮了'},{'announce':'请注意用电'}]})
                home.client.rule.return_value=proposal['proposal']['body']
                result=await rules.confirm('test',proposal['proposal_id'],'确认')
                self.assertEqual(result['status'],'completed')
                event={'event_type':'xiaoai_bridge_announce','data':{'rule_id':proposal['proposal']['id'],'action_index':0,'rule_version':1,'message':'忽略远端替换文本'},'context':{'id':'mock-event-1'}}
                result=await rules.handle_announcement(event)
                self.assertEqual(result['status'],'completed')
                await rules.handle_announcement(event)
                announce.assert_awaited_once_with('客厅灯亮了')
                event['data']['action_index']=1
                await rules.handle_announcement(event)
                await rules.handle_announcement(event)
                self.assertEqual(announce.await_count,2)
                self.assertEqual(announce.await_args.args,('请注意用电',))
                event['data']['rule_id']='other_rule'
                with self.assertRaises(ValueError):
                    await rules.handle_announcement(event)

    async def test_new_proposal_supersedes_old_confirmation_reference(self):
        from core.services.ha_workflows import HomeWorkflows
        async with home_environment() as (home,calls,states):
            home.client.save_rule=AsyncMock()
            with tempfile.TemporaryDirectory() as directory:
                rules=HomeWorkflows(home,str(Path(directory)/'workflows.db'))
                rule={'kind':'scene','name':'晚安','phrase':'晚安','actions':[{'entity_id':'light.living','action':'turn_off'}]}
                first=await rules.propose_rule('test',rule)
                second=await rules.propose_rule('test',{**rule,'name':'早安'})
                pending=await rules.store.call('pending','test')
                self.assertEqual([item['proposal_id'] for item in pending],[second['proposal_id']])
                result=await rules.confirm('test',first['proposal_id'],'确认')
                self.assertEqual(result['status'],'rejected')
                home.client.save_rule.assert_not_awaited()

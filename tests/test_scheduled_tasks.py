"""任务确认和到点执行使用假HA、假播报与可控时钟。"""
import tempfile
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import unittest
from unittest.mock import AsyncMock
from test_home_assistant import home_environment


class ScheduledTasksTest(unittest.IsolatedAsyncioTestCase):
    async def test_restart_marks_interrupted_occurrence_failed_but_preserves_daily_schedule(self):
        from core.services.ha_workflows import HomeWorkflows
        from core.services.scheduled_tasks import ScheduledTasks
        now=datetime(2026,10,29,20,tzinfo=ZoneInfo('Asia/Shanghai'))
        async with home_environment() as (home,calls,states):
            with tempfile.TemporaryDirectory() as directory:
                path=str(Path(directory)/'rules.db')
                tasks=ScheduledTasks(home,HomeWorkflows(home,path),AsyncMock(return_value=True))
                proposal=await tasks.propose('test',{'when':'每天20:01','kind':'reminder','text':'喝水'},now=now)
                await tasks.confirm('test',proposal['proposal_id'],'确认',now=now)
                item=(await tasks.list())[0]
                await tasks.store.call('put',item['id'],'task',tasks._payload(item,state='running'),item['version'])
                announce=AsyncMock(return_value=True)
                restored=ScheduledTasks(home,HomeWorkflows(home,path),announce)
                await restored.recover(now=now+timedelta(minutes=2))
                recovered=(await restored.list())[0]
                self.assertEqual(recovered['state'],'scheduled')
                self.assertEqual(recovered['last_result'],'failed')
                self.assertEqual(recovered['due'],'2026-10-30T20:01:00+08:00')
                announce.assert_not_awaited()
                await restored.tick(now+timedelta(days=1,minutes=1))
                announce.assert_awaited_once_with('喝水')

    async def test_confirmed_reminder_persists_runs_once_and_never_catches_up_devices(self):
        from core.services.ha_workflows import HomeWorkflows
        from core.services.scheduled_tasks import ScheduledTasks
        now=datetime(2026,10,29,20,tzinfo=ZoneInfo('Asia/Shanghai'))
        async with home_environment() as (home,calls,states):
            with tempfile.TemporaryDirectory() as directory:
                rules=HomeWorkflows(home,str(Path(directory)/'rules.db'))
                announce=AsyncMock(return_value=True)
                tasks=ScheduledTasks(home,rules,announce)
                proposal=await tasks.propose('test',{'when':'2小时后','kind':'reminder','text':'喝水'},now=now)
                self.assertIn('22:00',proposal['message'])
                self.assertEqual(await tasks.list(),[])
                await tasks.confirm('test',proposal['proposal_id'],'确认',now=now)
                restored=ScheduledTasks(home,HomeWorkflows(home,str(Path(directory)/'rules.db')),announce)
                await restored.tick(now+timedelta(hours=2))
                await restored.tick(now+timedelta(hours=2))
                announce.assert_awaited_once_with('喝水')
                self.assertEqual((await restored.list())[0]['state'],'completed')
                device=await tasks.propose('test',{'when':'今天20:01','kind':'device','actions':[{'entity_id':'light.living','action':'turn_on'}]},now=now)
                await tasks.confirm('test',device['proposal_id'],'确认',now=now)
                await tasks.tick(now+timedelta(minutes=3))
                self.assertEqual((await tasks.list())[-1]['state'] in ('completed','missed'),True)
                self.assertFalse(any(call[0]=='POST' for call in calls))
                self.assertIn('missed',[item['state'] for item in await tasks.list()])

    async def test_daily_tasks_failure_edit_confirmation_and_id_selection(self):
        from core.services.ha_workflows import HomeWorkflows
        from core.services.scheduled_tasks import ScheduledTasks
        now=datetime(2026,10,29,20,tzinfo=ZoneInfo('Asia/Shanghai'))
        async with home_environment() as (home,calls,states):
            with tempfile.TemporaryDirectory() as directory:
                tasks=ScheduledTasks(home,HomeWorkflows(home,str(Path(directory)/'rules.db')),AsyncMock(return_value=False))
                proposal=await tasks.propose('test',{'when':'每天20:01','kind':'reminder','text':'喝水'},now=now)
                await tasks.confirm('test',proposal['proposal_id'],'确认',now=now)
                identifier=(await tasks.list())[0]['id']
                await tasks.tick(now+timedelta(minutes=1))
                task=(await tasks.list())[0]
                self.assertEqual(task['last_result'],'failed')
                self.assertEqual(task['due'],'2026-10-30T20:01:00+08:00')
                with self.assertRaises(ValueError):
                    await tasks.propose_delete('test','不存在')
                deletion=await tasks.propose_delete('test',identifier)
                await tasks.confirm('test',deletion['proposal_id'],'不删了',now=now)
                self.assertEqual(len(await tasks.list()),1)

    async def test_running_daily_task_recovers_to_future_occurrence_without_replay(self):
        from core.services.ha_workflows import HomeWorkflows
        from core.services.scheduled_tasks import ScheduledTasks
        now=datetime(2026,10,29,20,tzinfo=ZoneInfo('Asia/Shanghai'))
        async with home_environment() as (home,calls,states):
            with tempfile.TemporaryDirectory() as directory:
                announce=AsyncMock(return_value=True)
                tasks=ScheduledTasks(home,HomeWorkflows(home,str(Path(directory)/'rules.db')),announce)
                proposal=await tasks.propose('test',{'when':'每天20:01','kind':'reminder','text':'喝水'},now=now)
                await tasks.confirm('test',proposal['proposal_id'],'确认',now=now)
                item=(await tasks.list())[0]
                # 模拟进程在领取任务之后崩溃；不会模拟成功的设备回执。
                await tasks.store.call('put',item['id'],'task',tasks._payload(item,state='running'),item['version'])
                await tasks.recover(now+timedelta(minutes=3))
                item=(await tasks.list())[0]
                self.assertEqual(item['state'],'scheduled')
                self.assertEqual(item['last_result'],'failed')
                self.assertEqual(item['last_error'],'process_restarted')
                self.assertEqual(item['due'],'2026-10-30T20:01:00+08:00')
                announce.assert_not_awaited()
                await tasks.tick(now+timedelta(days=1,minutes=1))
                announce.assert_awaited_once_with('喝水')

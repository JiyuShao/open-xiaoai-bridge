"""历史查询经允许实体接口统计，模型不自行加总。"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock
from core.services.device_history import DeviceHistory


class DeviceHistoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_switch_duration_count_gaps_and_calendar_ranges(self):
        client=AsyncMock()
        client.history.return_value=[[{'entity_id':'light.room','state':'off','last_changed':'2026-10-27T15:00:00+00:00'},
            {'entity_id':'light.room','state':'on','last_changed':'2026-10-28T01:00:00+00:00'},
            {'entity_id':'light.room','state':'off','last_changed':'2026-10-28T03:00:00+00:00'}]]
        history=DeviceHistory(client,{'light.room':{'name':'房间灯'}})
        now=datetime(2026,10,29,12,tzinfo=ZoneInfo('Asia/Shanghai'))
        result=await history.query('light.room','昨天',now=now)
        self.assertEqual(result['on_seconds'],7200)
        self.assertEqual(result['on_count'],1)
        self.assertEqual(result['start'],'2026-10-28T00:00:00+08:00')
        self.assertEqual(result['end'],'2026-10-29T00:00:00+08:00')
        self.assertFalse(result['partial'])
        client.history.return_value=[]
        self.assertEqual((await history.query('light.room','过去7天',now=now))['status'],'no_records')
        with self.assertRaises(ValueError):
            await history.query('light.secret','今天')

    async def test_numeric_weighted_average_missing_data_and_energy_reset(self):
        client=AsyncMock()
        entities={'sensor.temp':{'name':'温度'},'sensor.energy':{'name':'电量','history_kind':'energy'}}
        history=DeviceHistory(client,entities)
        now=datetime(2026,10,29,4,tzinfo=ZoneInfo('Asia/Shanghai'))
        client.history.return_value=[[{'entity_id':'sensor.temp','state':'20','last_changed':'2026-10-29T00:00:00+08:00'},
            {'entity_id':'sensor.temp','state':'30','last_changed':'2026-10-29T01:00:00+08:00'},
            {'entity_id':'sensor.temp','state':'unavailable','last_changed':'2026-10-29T03:00:00+08:00'}]]
        result=await history.query('sensor.temp','今天',now=now)
        self.assertAlmostEqual(result['average'],80/3)
        self.assertEqual(result['missing_seconds'],3600)
        self.assertTrue(result['partial'])
        client.history.return_value=[[{'entity_id':'sensor.energy','state':str(value),'last_changed':f'2026-10-29T0{i}:00:00+08:00'} for i,value in enumerate((10,12,1,4))]]
        result=await history.query('sensor.energy','今天',now=now)
        self.assertEqual(result['accumulated'],6)
        self.assertEqual(result['resets'],1)

    async def test_recorder_plan_keeps_365_days_and_labels_capacity_as_mock(self):
        from scripts.recorder_plan import create_plan
        import tempfile,json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)
            baseline=await create_plan({'sensor.temp':{'name':'温度'}},output)
            config=json.loads((output/'recorder.yaml').read_text())['recorder']
            self.assertEqual(config['purge_keep_days'],365)
            self.assertTrue(config['auto_purge'])
            self.assertTrue(config['auto_repack'])
            self.assertEqual(config['include']['entities'],['sensor.temp'])
            self.assertFalse(baseline['applied_to_ha'])
            self.assertEqual(baseline['evidence'],'mock-baseline')
            self.assertGreater(baseline['sample_database_bytes'],0)
            self.assertIsNone(baseline['production_database_bytes'])

    async def test_midnight_end_boundary_is_not_counted_in_yesterday(self):
        client=AsyncMock()
        history=DeviceHistory(client,{'light.room':{'name':'灯'},'sensor.temp':{'name':'温度'}})
        now=datetime(2026,10,29,12,tzinfo=ZoneInfo('Asia/Shanghai'))
        client.history.return_value=[[{'entity_id':'light.room','state':'off','last_changed':'2026-10-28T00:00:00+08:00'},
            {'entity_id':'light.room','state':'on','last_changed':'2026-10-29T00:00:00+08:00'}]]
        result=await history.query('light.room','昨天',now=now)
        self.assertEqual(result['on_count'],0)
        self.assertEqual(result['on_seconds'],0)
        self.assertEqual(result['intervals'],[])
        client.history.return_value=[[{'entity_id':'sensor.temp','state':'20','last_changed':'2026-10-28T00:00:00+08:00'},
            {'entity_id':'sensor.temp','state':'100','last_changed':'2026-10-29T00:00:00+08:00'}]]
        result=await history.query('sensor.temp','昨天',now=now)
        self.assertEqual(result['maximum'],20)

    async def test_scheduled_report_is_plain_speech_and_preserves_missing_record_notice(self):
        from core.services.device_history import history_summary
        summary=history_summary({'name':'客厅灯','status':'ok','on_seconds':7500,'on_count':2,'partial':True,'latency_ms':99},'今天')
        self.assertEqual(summary,'客厅灯，今天，开启共2小时5分钟，新增开启2次。该时段记录不完整，统计仅基于已有记录。')
        self.assertNotIn('latency',summary)

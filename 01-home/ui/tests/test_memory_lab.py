"""Memory Lab forwards to cloud Qualia. SQL semantics are tested in anam-reader.test.mjs."""
import unittest
from unittest.mock import patch
from api import hub
from services.cloud_state import CloudUnavailable

class MemoryLabTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_identity_and_status_are_preserved(self):
        packet={'items':[{'id':9,'certainty':None}],'total':120,'limit':50,'offset':60}
        with patch.object(hub,'qualia_read',return_value=packet) as read:
            result=await hub.memory_lab_observations(identity='Claude',limit=999,offset=60,status='archived')
        self.assertEqual(result,packet)
        read.assert_called_once_with('memory-lab/observations',identity='Claude',limit=50,offset=60,status='archived')

    async def test_entities_and_all_dashboard_views_use_cloud(self):
        with patch.object(hub,'qualia_read',return_value={'source':'cloud-qualia'}) as read:
            for fn in [hub.get_mind_insights,hub.get_mind_garden_summary,hub.get_mind_garden_weather,hub.get_mind_garden_threads,hub.memory_lab_entities]:
                self.assertEqual((await fn(identity='claude'))['source'],'cloud-qualia')
            self.assertEqual(read.call_count,5)

    async def test_failure_returns_unavailable_not_empty_or_local_success(self):
        with patch.object(hub,'qualia_read',side_effect=CloudUnavailable('offline')),patch('sqlite3.connect',side_effect=AssertionError('Local memory opened')):
            result=await hub.memory_lab_observations(identity='claude',limit=20,offset=0,status='')
        self.assertEqual(result.status_code,503)
        self.assertIn(b'No local fallback',result.body)

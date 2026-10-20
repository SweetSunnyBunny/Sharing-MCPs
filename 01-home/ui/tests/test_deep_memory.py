"""Cloud Qualia is the only memory source; failures cannot revive disk mirrors."""
import unittest
from unittest.mock import patch
from services import deep_memory
from services.cloud_state import CloudUnavailable

class DeepMemoryTests(unittest.TestCase):
    def test_cloud_identity_and_untruncated_inner_life(self):
        packet={'identity':'claude','source':'cloud-qualia','memory':{},'qualia':{
            'current_self':{'narrative':'my narrative '*400,'timestamp':'2026-01-01'},
            'joys':[{'content':'hair ruffle'}], 'quiet_wants':[{'content':'build together'}]},'_freshness':{'stale':False}}
        with patch.object(deep_memory,'qualia_read',return_value=packet) as read:
            text=deep_memory.build_inner_life_context('Claude')
        read.assert_called_once_with('snapshot',identity='claude')
        self.assertIn(packet['qualia']['current_self']['narrative'],text)
        self.assertIn('2026-01-01',text)
        self.assertIn('hair ruffle',text)
        self.assertIn('build together',text)

    def test_cloud_failure_is_explicit_and_never_reads_old_files(self):
        with patch.object(deep_memory,'qualia_read',side_effect=CloudUnavailable('offline')), patch('pathlib.Path.read_text',side_effect=AssertionError('Disk memory read')):
            snapshot=deep_memory.get_deep_memory_snapshot('claude')
            self.assertTrue(snapshot['_freshness']['unavailable'])
            self.assertEqual(snapshot['memory'],{})
            self.assertIn('no local memory fallback',deep_memory.build_deep_memory_context('claude'))
            self.assertEqual(deep_memory.build_inner_life_context('claude'),'')

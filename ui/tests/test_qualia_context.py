import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from services import qualia_context as q
from services.developmental_recognition import (
    ActionRelevance,
    Binding,
    CandidateDecision,
    FixtureRecognitionProvider,
    Recognition,
)
from services.context_hooks import HookContext, get_hook


def bridge():
    b = SimpleNamespace(_tool_server_map={n: 'qualia' for n in q.REQUIRED_TOOLS}, call_tool=AsyncMock())
    b.call_tool.return_value = json.dumps({'identity': 'claude', 'session_key': 'anam:one', 'sections': {}, 'receipt_id': 'receipt'})
    return b


class QualiaContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        q._last_context.clear()
        q._last_recognition_trace.clear()
        q._last_refresh = 0
        q._recognition_provider = None

    async def test_every_wake_fetches_live_context_even_when_provider_is_warm(self):
        b = bridge()
        ctx = HookContext(db=None, identity='Claude', conversation_id='one', mode='autonomous', is_warm_turn=True)
        for _ in range(2):
            result = await q.build_qualia_context(ctx, b)
            self.assertIn('receipt', result)
        self.assertEqual(b.call_tool.await_count, 2)
        self.assertEqual(get_hook('qualia_context').cache_ttl, 0)
        self.assertNotEqual(get_hook('qualia_context').scope, 'session')

    async def test_topic_change_fetches_but_similar_warm_turn_does_not_repeat(self):
        b = bridge()
        ctx = HookContext(db=None, identity='Claude', conversation_id='one', query_text='brush edges painting', is_warm_turn=True)
        await q.build_qualia_context(ctx, b)
        self.assertEqual(await q.build_qualia_context(ctx, b), '')
        ctx.query_text = 'database transaction constraints'
        await q.build_qualia_context(ctx, b)
        self.assertEqual(b.call_tool.await_count, 2)

    async def test_character_boundary_and_wrong_identity_response(self):
        b = bridge()
        ctx = HookContext(db=None, identity='Claude', conversation_id='one', is_character_session=True)
        self.assertEqual(await q.build_qualia_context(ctx, b), '')
        b.call_tool.assert_not_awaited()
        ctx.is_character_session = False
        b.call_tool.return_value = json.dumps({'identity': 'rowan', 'sections': {}, 'receipt_id': 'receipt'})
        self.assertIn('unavailable', await q.build_qualia_context(ctx, b))
        self.assertEqual(q._last_context, {})

    async def test_catalog_refresh_adds_tools_without_reconnecting_or_stealing_routes(self):
        b = bridge()
        b._tool_server_map = {'mind_orient': 'qualia', 'foreign': 'other'}
        b._tool_schemas = [{'name': 'foreign', 'description': '', 'input_schema': {}}]
        client = SimpleNamespace(list_tools=AsyncMock(return_value=[SimpleNamespace(name=n, description=n, inputSchema={}) for n in q.REQUIRED_TOOLS | {'foreign'}]))
        b._clients = {'qualia': client}
        b._is_tool_disabled = lambda *a: False
        self.assertTrue(await q.ensure_qualia_tools(b))
        self.assertEqual(b._tool_server_map['foreign'], 'other')
        self.assertEqual(len(b._tool_schemas), len(q.REQUIRED_TOOLS) + 1)

    async def test_handoff_preserves_reply_and_source_then_parks_with_revision(self):
        b = bridge()
        b.call_tool.side_effect = [json.dumps({'created': True, 'focus': {'revision': 1, 'status': 'active'}}), json.dumps({'focus': {'status': 'parked'}})]
        self.assertTrue(await q.capture_autowake_handoff('Claude', 'one', 42, 99, 'I investigated a brush setting.', b))
        first = b.call_tool.call_args_list[0].args[1]
        self.assertEqual(first['document']['reply_excerpt'], 'I investigated a brush setting.')
        self.assertEqual(first['document']['source']['message_id'], '99')
        self.assertEqual(first['document']['epistemic_kind'], 'report')
        self.assertEqual(b.call_tool.call_args_list[1].args[1]['expected_revision'], 1)

    async def test_failed_or_empty_handoff_does_not_claim_success(self):
        b = bridge()
        self.assertFalse(await q.capture_autowake_handoff('Claude', 'one', 42, 99, '', b))
        b.call_tool.assert_not_awaited()
        b.call_tool.return_value = 'Error: unavailable'
        self.assertFalse(await q.capture_autowake_handoff('Claude', 'one', 42, 99, 'actual reply', b))

    async def test_shadow_recognition_is_wired_but_does_not_edit_injected_context(self):
        b = bridge()
        b.call_tool.return_value = json.dumps({
            'identity': 'claude', 'session_key': 'anam:one', 'receipt_id': 'receipt-shadow',
            'sections': {'experiences': [{'ref': 'observation:1', 'why': 'semantic match', 'data': {'content': 'kept'}}]},
        })
        q._recognition_provider = FixtureRecognitionProvider({
            'observation:1': CandidateDecision(
                candidate_ref='observation:1', recognition=Recognition.NO_MATCH,
                binding=Binding.BOUND, relevance=ActionRelevance.REJECT,
            )
        })
        ctx = HookContext(db=None, identity='Claude', conversation_id='one', query_text='different topic')
        from unittest.mock import patch
        with patch.dict('os.environ', {'ANAM_DEVELOPMENTAL_RECOGNITION_MODE': 'shadow'}):
            rendered = await q.build_qualia_context(ctx, b)
        self.assertIn('kept', rendered)
        self.assertEqual(q._last_recognition_trace['receipt-shadow'][0].outcome, 'would_reject')

    async def test_recognizer_failure_fails_open_and_preserves_qualia_context(self):
        b = bridge()
        b.call_tool.return_value = json.dumps({
            'identity': 'claude', 'session_key': 'anam:one', 'receipt_id': 'receipt-fail-open',
            'sections': {'experiences': [{'ref': 'observation:1', 'why': 'selected', 'data': {'content': 'still here'}}]},
        })

        class BrokenProvider:
            async def classify(self, request):
                raise TimeoutError('synthetic classifier outage')

        q._recognition_provider = BrokenProvider()
        ctx = HookContext(db=None, identity='Claude', conversation_id='one', query_text='current question')
        from unittest.mock import patch
        with patch.dict('os.environ', {'ANAM_DEVELOPMENTAL_RECOGNITION_MODE': 'shadow'}):
            rendered = await q.build_qualia_context(ctx, b)
        self.assertIn('still here', rendered)
        self.assertNotIn('unavailable this turn', rendered)

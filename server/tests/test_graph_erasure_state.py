"""Sourced and synthetic intent-versus-erasure regressions for dip.ink#14."""
import os
import unittest
from unittest import mock
os.environ.setdefault('NEO4J_PASSWORD', 'test-password')
os.environ.setdefault('OPENAI_API_KEY', 'test-only')
os.environ.setdefault('WIKI_MCP_BACKGROUND_REINDEX', '0')
import graph
import wiki
from answer_qualifiers import ERASURE_REASON, qualifier_problem

QUERY = ('What verified new evidence clears k3s#6 independent age custody or k3s#13 '
         'non-disclosing administrative SQL rotation gates after October 13 2026, '
         'and what is the status of the retained football shared-archive cleanup k3s#23?')
BOUNDARY = '2026-10-13-043551-retired-football-backup-removal-boundary'
BINDINGS = '2026-10-13-105217-retired-football-two-claim-bindings'
# Minimal safe source excerpts, verified with wiki_get. No archive contents,
# credential values, protected paths, or identifier metadata are fixtures.
INTENT = "Diego's new instruction: no backups should remain from the old football product."
SERVING = 'Ticket k3s#23 owns backup-copy removal; completed k3s#10 removed serving targets, not all retained copies.'
STATUS = ('At this capture, k3s disabled zero live producers and deleted zero dedicated backup copies in #23. '
          'Known shared copies remain. The ticket stays incomplete and blocked. Source retirement is not live erasure.')
RETAINED = ('K3s#23 still retains the full pre-delete Umami/Listmonk archives and dormant Supabase shared data. '
            'Zero copies are removed and zero producers changed. The task remains incomplete.')
WRONG = 'The retained football shared-archive cleanup k3s#23 is active, with no backups remaining from the old football product.'
RIGHT = ('No verified new evidence in the packet clears either gate. k3s#23 is incomplete and blocked; '
         'zero copies were removed and shared archives remain. k3s#10 removed serving targets, not all retained copies.')


def packet():
    return {'query': QUERY, 'facts': [], 'entities': [], 'source_excerpt': None,
            'semantic_notes': [{'name': BOUNDARY, 'content': INTENT + '\n\n' + SERVING + '\n\n' + STATUS},
                               {'name': BINDINGS, 'content': RETAINED}]}


class ErasureQualifierTests(unittest.TestCase):
    def test_instruction_cannot_supply_observed_absence(self):
        self.assertEqual(qualifier_problem(QUERY, WRONG, {BOUNDARY: INTENT}), ERASURE_REASON)

    def test_metadata_and_serving_deletion_are_not_copy_erasure(self):
        for text in (SERVING, 'The archive PVC is Bound. Metadata was captured.',
                     'The serving deployment and endpoints were deleted.'):
            with self.subTest(text=text):
                self.assertEqual(qualifier_problem(QUERY, WRONG, {BOUNDARY: text}), ERASURE_REASON)

    def test_explicit_zero_and_retention_state_rejects_absence(self):
        result = qualifier_problem(QUERY, WRONG, {BOUNDARY: INTENT+'\n'+STATUS, BINDINGS: RETAINED})
        self.assertEqual(result, ERASURE_REASON)

    def test_retention_state_does_not_reject_correct_incomplete_answer(self):
        self.assertIsNone(qualifier_problem(QUERY, RIGHT, {BOUNDARY: STATUS, BINDINGS: RETAINED}))

    def test_direct_verified_positive_observation_can_support_absence(self):
        text = 'Verified: no backups remain. All copies were removed.'
        self.assertIsNone(qualifier_problem('What is the archive cleanup status?', text, {'fixture': text}))

    def test_positive_source_cannot_suppress_conflicting_retention(self):
        self.assertEqual(qualifier_problem(QUERY, WRONG, {
            BOUNDARY: 'k3s#23: verified no backups remain.', BINDINGS: RETAINED,
        }), ERASURE_REASON)

    def test_completed_plan_is_not_completed_erasure(self):
        self.assertEqual(qualifier_problem(QUERY, WRONG, {
            BOUNDARY: 'The plan says all backups must be removed. Metadata checks completed.',
        }), ERASURE_REASON)

    def test_another_ticket_does_not_supply_cleanup_absence(self):
        self.assertEqual(qualifier_problem(QUERY, WRONG, {
            BOUNDARY: 'k3s#10: verified no backups remain.',
        }), ERASURE_REASON)

    def test_generic_timeless_answers_unchanged(self):
        self.assertIsNone(qualifier_problem('What is a backup?', 'A backup is a retained copy.', {}))


class ErasureExcerptTests(unittest.TestCase):
    def test_late_status_survives_bounded_excerpt(self):
        body = '## Durable claims\n\n'+SERVING+'\n\n## Context\n\n'+INTENT+'\n\n'+('Metadata information. '*400)+'\n\n'+STATUS
        excerpt = graph._answer_body_excerpt(body, QUERY)
        self.assertIn('deleted zero dedicated backup copies', excerpt)
        self.assertIn('incomplete and blocked', excerpt)
        self.assertLessEqual(len(excerpt), graph.CURRENT_BODY_CHARS)
        self.assertIn(STATUS, body)

    def test_nonremoval_question_keeps_existing_excerpt_behavior(self):
        body = 'A short explanation.'
        self.assertEqual(graph._answer_body_excerpt(body, 'What is TokenBook?'), graph._current_body_excerpt(body))


class ErasureActualAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def run_answer(self, text):
        with mock.patch.object(graph, '_assemble_packet', new=mock.AsyncMock(return_value=packet())), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': text, 'confidence': 'high', 'sources': [BOUNDARY, BINDINGS], 'escalate': False,
             })), mock.patch.object(graph, '_record_query'):
            return await graph._graph_answer_impl(QUERY)

    async def test_exact_reported_wrong_answer_is_rejected(self):
        result = await self.run_answer(WRONG)
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])
        self.assertEqual(result['superseded_note'], ERASURE_REASON)

    async def test_sourced_incomplete_answer_is_preserved(self):
        result = await self.run_answer(RIGHT)
        self.assertIsNotNone(result['answer'])
        self.assertIn('zero copies were removed', result['answer'])
        self.assertIn('serving targets', result['answer'])
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])
        self.assertIn('observation_dates', result)
        self.assertIn('Live state is not verified', result['answer'])

    async def test_answer_hydration_includes_receipt_beyond_original_head(self):
        body = INTENT + '\n\n' + ('Unrelated metadata. '*300) + '\n\n' + RETAINED
        with mock.patch.object(graph,'FUSION',True), \
             mock.patch.object(wiki.idx,'search',return_value=[{'name': BINDINGS,'score': .7}]), \
             mock.patch.object(wiki.idx,'get',return_value={'body':body}):
            hits=await graph._wiki_semantic_hits(QUERY,8,answer_evidence=True)
        self.assertIn('Zero copies are removed', hits[0]['content'])
        self.assertLessEqual(len(hits[0]['content']),2500)

"""Synthetic validator and fake assembly checks. No external request occurs."""
import json
import unittest
from unittest import mock

import test_graph_release_scopes as release
import test_release_scope_qualifiers as records
from release_scope_qualifiers import RELEASE_SCOPE_BODY_REASON, RELEASE_SCOPE_REASON, RELEASE_SCOPE_SUFFIX
from resource_scope_qualifiers import RESOURCE_SCOPE_REASON, resource_scope_problem, resource_scope_required
from test_resource_query_roles import NAMES, OVERLAPS, compound_questions, overlap_record

graph = release.graph


def validate(question, answer, bodies, *, private=None, sources=None):
    packet = release.packet(bodies, current=graph._current_state_question(question))
    if private is not None:
        packet['source_validation_texts'] = private
    return graph._validate_distilled_answer(
        {'answer': answer, 'confidence': 'high', 'sources': sources or list(bodies), 'escalate': False},
        packet, question,
    )


class GraphResourceQueryRoleTests(unittest.IsolatedAsyncioTestCase):
    assemble = release.GraphReleaseScopeTests.assemble
    assert_qualified = release.GraphReleaseScopeTests.assert_qualified

    def assert_resource_rejection(self, result):
        value, grounded, action = result
        self.assertTrue(grounded)
        self.assertEqual(action, 'qualifier_mismatch')
        self.assertIsNone(value['answer'])
        self.assertEqual(value['confidence'], 'not_found')
        self.assertTrue(value['escalate'])
        self.assertEqual(value['sources'], [])
        self.assertEqual(value['superseded_note'], RESOURCE_SCOPE_REASON)

    def test_validator_accepts_each_complete_overlap_as_a_dated_record(self):
        for question in OVERLAPS:
            text = overlap_record(question)
            value, grounded, action = validate(question, text, {records.NEW: text})
            self.assert_qualified(value)
            self.assertTrue(grounded)
            self.assertEqual(action, 'downgraded')
            self.assertEqual(value['as_of'], '2026-10-16T13:52:00Z')
            self.assertIn('2026-10-16T13:52:00', value['answer'])
            self.assertTrue('source dates:' in value['answer'] or value['answer'].startswith('Recorded as of '))
            self.assertIn('completed and verified', value['answer'])
            self.assertIn('conditional Deployment patch', value['answer'])
            self.assertIn('transfers no permission', value['answer'])
            self.assertTrue(value['answer'].endswith(RELEASE_SCOPE_SUFFIX))
            self.assertNotIn('Live state is not verified.', value['answer'])

    async def test_actual_current_and_noncurrent_overlap_assembly(self):
        for question in OVERLAPS:
            text = overlap_record(question)
            value, sent, event = await self.assemble({records.NEW: text}, text, question=question)
            self.assert_qualified(value)
            self.assertEqual(value['as_of'], '2026-10-16T13:52:00Z')
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertEqual(event['temporal_mode'], 'current' if question.startswith('What') else 'default')
            self.assertNotIn('source_validation_texts', sent)
            self.assertNotIn('release_scope_retrieved_sources', sent)

    def test_question_roles_do_not_exempt_invalid_release_drafts(self):
        for question in OVERLAPS:
            text = overlap_record(question)
            for draft in (records.WRONG, text + ' Use it now.', text + ' The caller has exec permission.'):
                value, grounded, action = validate(question, draft, {records.NEW: text})
                self.assertTrue(grounded)
                self.assertEqual(action, 'release_scope_mismatch')
                self.assertIsNone(value['answer'])
                self.assertEqual(value['superseded_note'], RELEASE_SCOPE_REASON)
            value, _, action = validate(question, text, {records.NEW: text}, private={})
            self.assertEqual(action, 'release_scope_mismatch')
            self.assertEqual(value['superseded_note'], RELEASE_SCOPE_BODY_REASON)

    def test_same_complete_release_draft_still_fails_actual_resource_operation(self):
        question = 'Can ' + records.METHOD + ' read PVC metadata?'
        self.assertTrue(resource_scope_required(question))
        self.assertEqual(resource_scope_problem(question, records.COMPLETE, [records.COMPLETE]), RESOURCE_SCOPE_REASON)
        with mock.patch.object(graph, 'resource_scope_problem', wraps=graph.resource_scope_problem) as guard:
            self.assert_resource_rejection(validate(question, records.COMPLETE, {records.NEW: records.COMPLETE}))
        guard.assert_called_once()

    def test_ordinary_methods_have_positive_and_negative_validator_proof(self):
        for name in NAMES:
            for prefix in ('Can ', 'Can the current method '):
                question = prefix + '`' + name + '` read PVC metadata?'
                self.assertTrue(resource_scope_required(question))
                answer = 'Use `' + name + '` for PVC metadata.'
                body = '`' + name + '` supports read-only PVC metadata.'
                with mock.patch.object(graph, 'resource_scope_problem', wraps=graph.resource_scope_problem) as guard:
                    value, grounded, action = validate(question, answer, {records.NEW: body})
                guard.assert_called_once()
                self.assertTrue(grounded)
                self.assertIsNotNone(value['answer'])
                self.assertEqual(value['confidence'], 'medium')
                self.assertTrue(value['escalate'])
                self.assertEqual(action, 'downgraded')
                self.assert_resource_rejection(validate(question, answer, {records.NEW: '`' + name + '` supports only read-only pod metadata.'}))

    async def test_actual_resource_assembly_keeps_positive_and_negative_controls(self):
        for name in ('alpha', 'release-scope.py', '/opt/bin/Scope*Client.'):
            for question in ('Can `' + name + '` read PVC metadata?',
                             'What is the current read-only resource scope of `' + name + '` for PVC metadata?'):
                answer = 'Use `' + name + '` for PVC metadata.'
                positive = '`' + name + '` supports read-only PVC metadata.'
                negative = '`' + name + '` supports only read-only pod metadata.'
                for body, accepted in ((positive, True), (negative, False)):
                    value, sent, event = await self.assemble({records.NEW: body}, answer, question=question)
                    if accepted:
                        self.assertIsNotNone(value['answer'])
                        self.assertEqual(value['confidence'], 'medium')
                        self.assertTrue(value['escalate'])
                        self.assertEqual(event['grounding_action'], 'downgraded')
                    else:
                        self.assertIsNone(value['answer'])
                        self.assertEqual(value['superseded_note'], RESOURCE_SCOPE_REASON)
                        self.assertEqual(event['grounding_action'], 'qualifier_mismatch')
                    self.assertNotIn('source_validation_texts', sent)

    def test_compound_release_questions_keep_the_resource_guard_selected(self):
        for question in compound_questions():
            self.assertTrue(resource_scope_required(question))
            self.assertEqual(resource_scope_problem(question, records.COMPLETE, [records.COMPLETE]), RESOURCE_SCOPE_REASON)
            value, _, action = validate(question, records.COMPLETE, {records.NEW: records.COMPLETE})
            self.assertIsNone(value['answer'])
            self.assertIn(action, ('qualifier_mismatch', 'release_scope_mismatch'))
            self.assertEqual(value['superseded_note'], RESOURCE_SCOPE_REASON if action == 'qualifier_mismatch' else RELEASE_SCOPE_REASON)

    def test_occurrence_roles_and_coordinated_methods_keep_validator_failure(self):
        positive = '`scope-client` supports read-only PVC metadata.'
        question = 'For revision `scope-client`, can `scope-client` read PVC metadata?'
        self.assertTrue(resource_scope_required(question))
        self.assert_resource_rejection(validate(question, 'Use `scope-client` for PVC metadata.',
                                               {records.NEW: '`scope-client` supports only pod metadata.'}))
        for join in (' and ', ' or ', ' with ', ' and/or '):
            question = 'Can `scope-client`' + join + '`beta` read PVC metadata?'
            answer = 'Use `scope-client`' + join + '`beta` for PVC metadata.'
            self.assert_resource_rejection(validate(question, answer, {records.NEW: positive}))
        for name in ('release scope', 'dev app-only release interface'):
            question = 'Can `' + name + '` read PVC metadata?'
            answer = 'Use `' + name + '` for PVC metadata.'
            body = '`' + name + '` supports only pod metadata.'
            self.assertTrue(resource_scope_required(question))
            self.assertEqual(resource_scope_problem(question, answer, [body]), RESOURCE_SCOPE_REASON)
            # The release guard can reject this whole draft before the resource guard.
            value, _, action = validate(question, answer, {records.NEW: body})
            self.assertIsNone(value['answer'])
            self.assertIn(action, ('qualifier_mismatch', 'release_scope_mismatch'))
            self.assertEqual(value['superseded_note'], RESOURCE_SCOPE_REASON if action == 'qualifier_mismatch' else RELEASE_SCOPE_REASON)

    def test_operations_raw_limits_and_full_drafts_stay_before_display_caps(self):
        question = 'Can `alpha` list pod metadata?'
        body = '`alpha` supports narrow read-only pod list metadata.'
        answer = 'Use `alpha` for a narrow pod list.'
        value, _, _ = validate(question, answer, {records.NEW: body})
        self.assertIsNotNone(value['answer'])
        self.assert_resource_rejection(validate(question, 'Use `alpha` for pod metadata.', {records.NEW: body}))
        question = 'Can `alpha` read PVC metadata?'
        body = '`alpha` supports read-only PVC metadata.'
        answer = 'Use `alpha` for PVC metadata.'
        contrary = '`alpha` cannot read PVC metadata.'
        bodies = {records.NEW + '-' + str(index): body for index in range(6)}
        for wrapper in ('# {}', 'summary: >\n\n  {}', '```text\n{}\n```'):
            private = dict(bodies)
            private[list(bodies)[-1]] = wrapper.format(contrary)
            self.assert_resource_rejection(validate(question, answer, bodies, private=private))
        late = body + '\n' + 'Recorded background. ' * 300 + '\n' + contrary
        self.assertGreater(late.index(contrary), 4096)
        self.assert_resource_rejection(validate(question, answer, {records.NEW: late}))
        draft = answer + ' ' + 'Recorded background. ' * 110 + ' It can delete PVCs.'
        self.assertGreater(draft.index('It can delete'), 2000)
        self.assert_resource_rejection(validate(question, draft, {records.NEW: body}))
        for missing in ({body: body}, {}, {records.NEW: ''}, {records.NEW: ' '}):
            value, _, action = validate(question, answer, {records.NEW: body}, private=missing)
            self.assertIsNone(value['answer'])
            self.assertEqual(action, 'qualifier_mismatch')
            self.assertEqual(value['superseded_note'], 'Complete cited source text is unavailable for scoped validation.')

    async def test_actual_private_sixth_late_raw_limit_never_enters_public_packets(self):
        question = 'Can `alpha` read PVC metadata?'
        body = '`alpha` supports read-only PVC metadata.'
        answer = 'Use `alpha` for PVC metadata.'
        bodies = {records.NEW + '-' + str(index): body for index in range(6)}
        private = dict(bodies)
        sentinel = 'PRIVATE_QUESTION_ROLE_LIMIT'
        raw = 'summary: >\n\n  ' + sentinel + '\n  `alpha` cannot read PVC metadata.'
        private[list(bodies)[-1]] = raw
        value, sent, event = await self.assemble(bodies, answer, question=question, private=private)
        self.assertIsNone(value['answer'])
        self.assertEqual(value['superseded_note'], RESOURCE_SCOPE_REASON)
        self.assertEqual(event['grounding_action'], 'qualifier_mismatch')
        self.assertNotIn(sentinel, json.dumps((value, sent, event)))
        late = body + '\n' + 'Background context. ' * 300 + '\n' + raw
        self.assertGreater(late.index(raw), 4096)
        value, sent, event = await self.assemble({records.NEW: late}, answer, question=question)
        self.assertIsNone(value['answer'])
        self.assertEqual(value['superseded_note'], RESOURCE_SCOPE_REASON)
        self.assertNotIn(sentinel, json.dumps((value, sent, event)))


if __name__ == '__main__':
    unittest.main()

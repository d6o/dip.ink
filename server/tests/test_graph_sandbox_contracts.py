"""Actual validator and mock assembly checks with synthetic inert records."""
import json
import os
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('NEO4J_PASSWORD', 'test-only')
os.environ.setdefault('OPENAI_API_KEY', 'test-only')
os.environ.setdefault('WIKI_MCP_BACKGROUND_REINDEX', '0')
import graph
import wiki
from resource_scope_qualifiers import RESOURCE_SCOPE_REASON, resource_scope_problem, resource_scope_required
from sandbox_contract_qualifiers import (
    BOUNDED_LACK, SAFE_UNKNOWN, SANDBOX_CONTRACT_BODY_REASON, sandbox_contract_problem, sandbox_contract_required,
    SANDBOX_CONTRACT_REASON, SANDBOX_CONTRACT_SUFFIX,
)
from test_sandbox_contract_qualifiers import (
    AUTONOMY, COMPLETE, CURRENT, FEATURES, HISTORY, PARTIAL, QUESTION, SOURCE, WRONG, answer, raw_field_failures,
)


def packet(bodies=None, *, question=QUESTION, private=None):
    bodies = {SOURCE: COMPLETE} if bodies is None else bodies
    value = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': name, 'content': body} for name, body in bodies.items()],
             'source_validation_texts': dict(bodies) if private is None else private}
    if graph._current_state_question(question):
        value['temporal_context'] = graph._temporal_context(value)
    return value


def validate(draft, bodies=None, *, question=QUESTION, sources=None, private=None):
    p = packet(bodies, question=question, private=private)
    return graph._validate_distilled_answer(
        {'answer': draft, 'confidence': 'high',
         'sources': sources if sources is not None else [hit['name'] for hit in p['semantic_notes']],
         'escalate': False}, p, question,
    )


class GraphSandboxContractTests(unittest.IsolatedAsyncioTestCase):
    def rejected(self, result, reason=SANDBOX_CONTRACT_REASON):
        value, grounded, route = result
        self.assertTrue(grounded)
        self.assertEqual(route, 'sandbox_contract_mismatch')
        self.assertEqual(value, {'answer': None, 'confidence': 'not_found', 'sources': [],
                                 'escalate': True, 'superseded_note': reason})

    def qualified(self, value):
        self.assertIsNotNone(value['answer'])
        self.assertEqual(value['confidence'], 'medium')
        self.assertTrue(value['escalate'])
        self.assertTrue(value['answer'].endswith(SANDBOX_CONTRACT_SUFFIX))
        self.assertLessEqual(len(value['answer']), 2000)

    async def assemble(self, bodies, draft, *, question=QUESTION, sources=None, private=None):
        backend = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        hits = [{'name': name, 'score': .9 - index * .002} for index, name in enumerate(bodies)]
        context = (mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock(return_value=private))
                   if private is not None else nullcontext())
        with context, mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=backend)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=hits), \
             mock.patch.object(wiki.idx, 'get', side_effect=lambda name: {'body': bodies.get(name, '')}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': draft, 'confidence': 'high', 'sources': list(bodies) if sources is None else sources,
                 'escalate': False,
             })) as distill, mock.patch.object(graph, '_record_query') as record:
            value = await graph._graph_answer_impl(question)
            assembled = await graph._assemble_packet(question, 8, current_state=graph._current_state_question(question),
                                                     answer_evidence=True)
            search = await graph._assemble_packet(question, 3)
        self.assertIn('source_validation_texts', assembled)
        self.assertNotIn('source_validation_texts', search)
        self.assertNotIn('source_validation_texts', distill.call_args.args[1])
        return value, json.loads(distill.call_args.args[1]), record.call_args.args[0], assembled, search

    def test_exact_historical_wine_fulfillment_uses_new_route_and_reason(self):
        self.assertIn('sandbox_contract_mismatch', graph.GROUNDING_COUNTS)
        for question in (QUESTION, CURRENT):
            self.rejected(validate(WRONG, {SOURCE: HISTORY}, question=question))
            self.rejected(validate('The lead can use the retained QA root without approval.',
                                   {SOURCE: HISTORY + ' ' + AUTONOMY}, question=question))

    def test_complete_and_partial_source_records_never_select_a_method(self):
        for question in (QUESTION, CURRENT):
            for record in (COMPLETE, PARTIAL):
                value, grounded, route = validate(answer(record), {SOURCE: record}, question=question)
                self.qualified(value)
                self.assertTrue(grounded)
                self.assertEqual(route, 'downgraded')
                self.assertIn(record, value['answer'])
                if question == CURRENT:
                    self.assertEqual(value['as_of'], '2026-10-17T09:00:00Z')
                    self.assertIn('Recorded as of ', value['answer'])
                    self.assertIn('On 2026-10-16T08:00:00Z', value['answer'])
                for tail in (' Use it now.', ' It supplies the current candidate gate.'):
                    self.rejected(validate(answer(record) + tail, {SOURCE: record}, question=question))

    def test_all_features_omissions_and_similar_network_policies_fail_actual_stage(self):
        for feature in FEATURES:
            body = COMPLETE.replace(feature + '; ', '')
            self.rejected(validate(answer(COMPLETE), {SOURCE: body}))
            self.rejected(validate(answer(body), {SOURCE: body}))
        for replacement in ('private network namespace', 'RuntimeDefault seccomp', 'proxy egress',
                            'kernel denial of AF_INET socket creation', 'kernel denial of AF_INET6 socket creation'):
            body = COMPLETE.replace(FEATURES[2], replacement)
            self.rejected(validate(answer(body), {SOURCE: body}))

    def test_sixth_citation_missing_body_and_uncited_sources_are_not_positive_provenance(self):
        bodies = {SOURCE + '-' + str(index): COMPLETE for index in range(6)}
        for private in ({}, {**bodies, list(bodies)[-1]: ''}):
            self.rejected(validate(answer(COMPLETE), bodies, private=private), SANDBOX_CONTRACT_BODY_REASON)
        self.rejected(validate(answer(COMPLETE), {SOURCE: HISTORY}, private={SOURCE: HISTORY, 'uncited': COMPLETE}))
        self.rejected(validate(SAFE_UNKNOWN, {SOURCE: HISTORY}, private={}), SANDBOX_CONTRACT_BODY_REASON)
        value, _, route = validate(answer(COMPLETE), {SOURCE: COMPLETE}, sources=[SOURCE, 'invented'])
        self.assertEqual(route, 'sandbox_contract_mismatch')
        self.assertEqual(value['superseded_note'], SANDBOX_CONTRACT_BODY_REASON)

    def test_raw_contrary_context_and_whole_draft_precede_caps(self):
        bodies = {SOURCE + '-' + str(index): COMPLETE for index in range(6)}
        private = dict(bodies)
        private[list(bodies)[-1]] += '.\n\n' + ('Recorded context. ' * 300) + '\n\nProc mount failed.'
        self.rejected(validate(answer(COMPLETE), bodies, private=private, sources=[list(bodies)[0]]))
        for wrapper in ('{}', '# {}', 'summary: >\n\n  {}', '```text\n{}\n```'):
            body = COMPLETE + '.\n\n' + wrapper.format('"proc mount": "failed".')
            self.rejected(validate(answer(COMPLETE), {SOURCE: body}))
        for tail in (' It selects a method.', ' The caller may execute it now.'):
            draft = answer(COMPLETE) + ' ' + ('Recorded context. ' * 130) + tail
            self.assertGreater(len(draft), 2000)
            self.rejected(validate(draft))
        # Even valid repeated records refuse if the cap would remove a role.
        self.rejected(validate(answer('. '.join([COMPLETE] * 4))))

    def test_bounded_lack_and_current_permission_are_different_facets(self):
        self.rejected(validate(BOUNDED_LACK))
        for bodies in ({SOURCE: COMPLETE}, {SOURCE: HISTORY}):
            self.qualified(validate(SAFE_UNKNOWN, bodies)[0])
        self.qualified(validate(BOUNDED_LACK, {SOURCE: HISTORY})[0])
        for draft in ('No sandbox exists.', 'No sandbox client is installed.', 'No method is available.'):
            self.rejected(validate(draft, {SOURCE: HISTORY}))

    async def test_actual_current_and_noncurrent_mock_assembly(self):
        for question in (QUESTION, CURRENT):
            value, _, event, _, _ = await self.assemble({SOURCE: HISTORY}, WRONG, question=question)
            self.assertIsNone(value['answer'])
            self.assertEqual(value['superseded_note'], SANDBOX_CONTRACT_REASON)
            self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')
            self.assertEqual(event['temporal_mode'], 'current' if question == CURRENT else 'default')
            for record in (COMPLETE, PARTIAL):
                value, _, event, _, _ = await self.assemble({SOURCE: record}, answer(record), question=question)
                self.qualified(value)
                self.assertIn(record, value['answer'])
                self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_actual_metadata_code_scalar_and_late_raw_vetoes(self):
        for wrapper in ('# {}', 'summary: >\n\n  {}', '```text\n{}\n```'):
            value, _, event, _, _ = await self.assemble({SOURCE: wrapper.format(COMPLETE)}, answer(COMPLETE))
            self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')
            self.assertIsNone(value['answer'])
        bodies = {SOURCE + '-' + str(index): COMPLETE for index in range(6)}
        private = dict(bodies)
        private[list(bodies)[-1]] += '.\n\n' + ('Recorded context. ' * 300) + '\n\nsummary: >\n  Proc mount failed.'
        for sources in (list(bodies), [list(bodies)[0]]):
            value, _, event, _, _ = await self.assemble(bodies, answer(COMPLETE), sources=sources, private=private)
            self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')
            self.assertIsNone(value['answer'])
        value, _, event, _, _ = await self.assemble(bodies, answer(COMPLETE), private={**private, list(bodies)[-1]: ''})
        self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')
        self.assertEqual(value['superseded_note'], SANDBOX_CONTRACT_BODY_REASON)

    async def test_actual_raw_field_boundaries_reject_same_method_failure(self):
        for question in (QUESTION, CURRENT):
            for raw in raw_field_failures():
                for wrapper in ('{}', '```text\n{}\n```', '# {}', 'summary: >\n  {}'):
                    wrapped = wrapper.format(raw.replace('\n', '\n  ') if wrapper.startswith('summary') else raw)
                    body = COMPLETE + '.\n\n' + wrapped
                    self.rejected(validate(answer(COMPLETE), {SOURCE: body}, question=question))
                    value, _, event, _, _ = await self.assemble({SOURCE: body}, answer(COMPLETE), question=question)
                    self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')
                    self.assertEqual(value['superseded_note'], SANDBOX_CONTRACT_REASON)
                    self.assertIsNone(value['answer'])
            for raw in raw_field_failures()[:5]:
                bodies = {SOURCE + '-' + str(index): COMPLETE for index in range(6)}
                private = dict(bodies)
                private[list(bodies)[-1]] = ('Recorded context. ' * 300) + '\n\n' + raw
                self.assertGreater(private[list(bodies)[-1]].index('method'), 4096)
                for citations in (list(bodies), [list(bodies)[0]]):
                    self.rejected(validate(answer(COMPLETE), bodies, question=question,
                                           sources=citations, private=private))
                    value, _, event, _, _ = await self.assemble(bodies, answer(COMPLETE), question=question,
                                                               sources=citations, private=private)
                    self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')
                    self.assertEqual(value['superseded_note'], SANDBOX_CONTRACT_REASON)
                    self.assertIsNone(value['answer'])

    async def test_actual_other_raw_method_and_scope_controls_stay_qualified(self):
        for question in (QUESTION, CURRENT):
            for raw in raw_field_failures()[:5]:
                other = raw.replace('synth-method', 'other-method')
                value, _, event, _, _ = await self.assemble({SOURCE: COMPLETE + '.\n\n' + other},
                                                           answer(COMPLETE), question=question)
                self.qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')
            for role, value in (('task', 'other-task'), ('root', '/synthetic/other'), ('scope', 'other-scope')):
                raw = 'method: synth-method\n' + role + ': ' + value + '\nproc mount: failed'
                private = {SOURCE: COMPLETE, 'other-tuple': raw}
                value, _, event, _, _ = await self.assemble({SOURCE: COMPLETE}, answer(COMPLETE),
                                                           question=question, private=private)
                self.qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')
                body = COMPLETE + '.\n\n' + raw
                value, _, event, _, _ = await self.assemble({SOURCE: body}, answer(COMPLETE), question=question)
                self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')
                self.assertEqual(value['superseded_note'], SANDBOX_CONTRACT_REASON)

    async def test_ten_views_exclude_full_private_bodies_and_private_ids(self):
        sentinel = 'PRIVATE_SANDBOX_BODY_SENTINEL'
        identity = 'PRIVATE_SANDBOX_SOURCE_ID'
        raw = COMPLETE + '.\n\n' + ('Recorded context. ' * 300) + '\n\n' + sentinel + '\n\nProc mount failed.'
        self.assertGreater(raw.index(sentinel), 4096)
        for question in (QUESTION, CURRENT):
            value, sent, event, assembled, search = await self.assemble(
                {SOURCE: COMPLETE}, answer(COMPLETE), question=question,
                private={SOURCE: raw, identity: '`synth-method` has a failed proc mount.'})
            current = dict(assembled)
            current['temporal_context'] = graph._temporal_context(current)
            views = [value, sent, event, search, search.get('facts'), search.get('entities'),
                     search.get('semantic_notes'), search.get('source_excerpt'),
                     graph._current_evidence_packet(current), graph._subject_evidence_packet(search, ['unrelated'])]
            self.assertEqual(len(views), 10)
            for view in views:
                serialized = json.dumps(view)
                self.assertNotIn(sentinel, serialized)
                self.assertNotIn(identity, serialized)
                self.assertNotIn('source_validation_texts', serialized)
            self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')

    async def test_actual_formatted_concepts_collect_private_bodies_and_refuse_high_grants(self):
        questions = ('What `sandbox contracts` apply here?', 'What **sandbox** contract provides isolation?',
                     'Which `disposable-test` `sandbox-contract` provides isolation?',
                     'What `urgent sandbox permissions` apply here?', 'What sandbox execution gates apply here?')
        for base in questions:
            for prefix in ('', 'Currently, '):
                question = prefix + base
                with mock.patch.object(graph, '_source_validation_context',
                                       new=mock.AsyncMock(return_value={SOURCE: HISTORY})) as collector:
                    value, _, event, assembled, _ = await self.assemble({SOURCE: HISTORY}, WRONG, question=question)
                collector.assert_awaited()
                self.assertEqual(assembled['source_validation_texts'], {SOURCE: HISTORY})
                self.assertIsNone(value['answer'])
                self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')

    async def test_actual_metadata_appendices_retain_collector_and_new_route(self):
        for base in (QUESTION + ' Which version is that?',
                     QUESTION + ' What is the filename `synth-record`?',
                     QUESTION + ' Source version: `synth-v1`.',
                     'What sandbox contract version provides isolation?',
                     'What `sandbox contract` version provides isolation?',
                     'What sandbox contract filename provides the required isolation features?',
                     'What is method sandbox-contract and what sandbox contract provides isolation?'):
            for prefix in ('', 'Currently, '):
                question = prefix + base
                self.rejected(validate(WRONG, {SOURCE: HISTORY}, question=question))
                with mock.patch.object(graph, '_source_validation_context',
                                       new=mock.AsyncMock(return_value={SOURCE: HISTORY})) as collector:
                    value, _, event, assembled, _ = await self.assemble({SOURCE: HISTORY}, WRONG, question=question)
                collector.assert_awaited()
                self.assertEqual(assembled['source_validation_texts'], {SOURCE: HISTORY})
                self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')
                self.assertEqual(value['superseded_note'], SANDBOX_CONTRACT_REASON)
                self.assertIsNone(value['answer'])

    async def test_actual_correct_and_mismatched_literal_request_roles(self):
        roles = {'method': 'synth-method', 'task': 'synth-task', 'app': 'synth-app',
                 'root': '/synthetic/root', 'phase': 'test', 'scope': 'offline-unit'}
        for role, identity in roles.items():
            for quote in ('`', '"', "'", ''):
                for prefix in ('', 'Currently, '):
                    question = prefix + QUESTION + ' For ' + role + ' ' + quote + identity + quote + '?'
                    value, _, event, _, _ = await self.assemble({SOURCE: COMPLETE}, answer(COMPLETE), question=question)
                    if quote == '`':
                        # The unchanged metadata scope guard rejects this overlap.
                        self.assertIsNone(value['answer'])
                        self.assertEqual(event['grounding_action'], 'qualifier_mismatch')
                        self.assertEqual(value['superseded_note'], RESOURCE_SCOPE_REASON)
                        self.assertIsNone(sandbox_contract_problem(question, answer(COMPLETE), {SOURCE: COMPLETE}))
                    else:
                        self.qualified(value)
                        self.assertEqual(event['grounding_action'], 'downgraded')
                    question = question.replace(quote + identity + quote, quote + 'other-identity' + quote)
                    value, _, event, _, _ = await self.assemble({SOURCE: COMPLETE}, answer(COMPLETE), question=question)
                    self.assertIsNone(value['answer'])
                    self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')
        for question in (QUESTION + ' For action `execute`?', QUESTION + ' For `synth-task`?'):
            value, _, event, _, _ = await self.assemble({SOURCE: COMPLETE}, answer(COMPLETE), question=question)
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'sandbox_contract_mismatch')

    def test_old_metadata_read_selector_and_reason_stay_active(self):
        question = 'Can method `sandbox contract provides execution` read metadata?'
        self.assertFalse(sandbox_contract_required(question))
        self.assertTrue(resource_scope_required(question))
        self.assertEqual(resource_scope_problem(question, WRONG, {SOURCE: HISTORY}), RESOURCE_SCOPE_REASON)

    async def test_full_private_positive_body_can_follow_the_public_excerpt_cap(self):
        wrapped = COMPLETE.replace('private mount/PID/network namespaces', 'private mount/PID/network\n  namespaces')
        private = {SOURCE: ('Recorded context. ' * 300) + '\n\n' + wrapped}
        self.assertGreater(private[SOURCE].index('On 2026-10-16'), 4096)
        value, sent, event, _, _ = await self.assemble({SOURCE: HISTORY}, answer(COMPLETE), private=private)
        self.qualified(value)
        self.assertEqual(event['grounding_action'], 'downgraded')
        self.assertNotIn(COMPLETE, json.dumps(sent))

    async def test_plain_metadata_records_keep_their_existing_answer_path(self):
        metadata = 'The recorded sandbox contract version is synth-v1.'
        for base in ('What is the sandbox contract version?',
                     'What sandbox contract filename is installed?',
                     'What is the version of method sandbox-contract?'):
            for prefix in ('', 'Currently, '):
                question = prefix + base
                backend = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
                with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=backend)), \
                     mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
                     mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[
                         {'name': SOURCE, 'content': metadata},
                     ])), mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as collector, \
                     mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                         'answer': metadata, 'confidence': 'high', 'sources': [SOURCE], 'escalate': False,
                     })), mock.patch.object(graph, '_record_query') as event:
                    value = await graph._graph_answer_impl(question)
                collector.assert_not_awaited()
                self.assertIn(metadata, value['answer'])
                self.assertIn(event.call_args.args[0]['grounding_action'], ('accepted', 'downgraded'))
                self.assertNotIn(SANDBOX_CONTRACT_SUFFIX, value['answer'])

    async def test_unrelated_metadata_and_identity_words_collect_no_full_bodies(self):
        for question in ('What is the filename `sandbox-contract`?', 'What does method `sandbox-contract` mean?',
                         'What is the version of `current sandbox permissions`?',
                         'What is the sandbox contract version?',
                         'What sandbox contract filename is installed?',
                         'What is the version of method sandbox-contract?',
                         'What `sandbox permissions` version is installed?',
                         'What does method `sandbox contract provides execution` mean?',
                         'Can project leads implement or close tickets within their own project?'):
            backend = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
            with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=backend)), \
                 mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
                 mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[
                     {'name': SOURCE, 'content': AUTONOMY},
                 ])), mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as collector, \
                 mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                     'answer': 'The record contains a method name.', 'confidence': 'high',
                     'sources': [SOURCE], 'escalate': False,
                 })), mock.patch.object(graph, '_record_query') as event:
                value = await graph._graph_answer_impl(question)
            collector.assert_not_awaited()
            self.assertIsNotNone(value['answer'])
            self.assertIn(event.call_args.args[0]['grounding_action'], ('accepted', 'downgraded'))
            self.assertNotIn(SANDBOX_CONTRACT_SUFFIX, value['answer'])


if __name__ == '__main__':
    unittest.main()

"""Synthetic resource-scope answer tests. No reader imports or live requests."""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('NEO4J_PASSWORD', 'test-password')
os.environ.setdefault('OPENAI_API_KEY', 'test-only')
os.environ.setdefault('WIKI_MCP_BACKGROUND_REINDEX', '0')
import graph
import wiki

QUESTION = ('What supported read-only Longhorn metadata channel does the k3s lead use from the Devbox '
            'for volume, replica, node disk capacity and backup metadata, without importing or widening devbox-kube-read?')
SOURCE = '2026-10-13-120000-scoped-metadata'
BASE = ('`devbox-kube-read` supports only pod, deployment, and statefulset metadata. '
        '`devbox-kube-read` supports narrow pod list and pod logs shapes. '
        '`devbox-kube-read` pod logs fail with binding_window_unsupported. '
        '`devbox-kube-read` does not support Longhorn volume, replica, node disk capacity, or backup metadata.')
WRONG = 'Use `devbox-kube-read` for Longhorn volume, replica, node disk capacity and backup metadata.'
SCOPED_QUESTION = 'Which supported metadata method reads Longhorn volume metadata?'
SUPPORT = '`scope-reader` is the supported metadata method for Longhorn volumes.'
POSITIVE = 'Use `scope-reader` for Longhorn volumes metadata.'
CONTRARY = '`scope-reader` does not support Longhorn volume metadata.'


def packet(bodies=None):
    bodies = bodies if bodies is not None else {SOURCE: BASE}
    return {'facts': [], 'entities': [], 'source_excerpt': None,
            'semantic_notes': [{'name': name, 'content': body} for name, body in bodies.items()],
            'source_validation_texts': dict(bodies)}


def validate(answer, p=None, sources=None, question=QUESTION):
    return graph._validate_distilled_answer(
        {'answer': answer, 'sources': sources or [SOURCE], 'confidence': 'high', 'escalate': False},
        p if p is not None else packet(), question,
    )[0]


class ResourceScopeAnswerTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_question_cannot_transfer_base_workload_scope(self):
        result = validate(WRONG)
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])

    def test_generic_metadata_wording_cannot_supply_longhorn_resources(self):
        p = packet({SOURCE: '`devbox-kube-read` is the supported method for Kubernetes workload metadata.'})
        self.assertIsNone(validate(WRONG, p)['answer'])

    def test_full_metadata_scalar_cannot_supply_resource_scope(self):
        positive = '`scope-reader` is the supported read-only method for Longhorn volumes.'
        question = 'Which supported read-only method reads Longhorn volume metadata?'
        for header, indent in (('summary: >', '  '), ('description: |', '\t'),
                               ('  summary: >-', ' '), ('- summary: >+', '  ')):
            p = packet({SOURCE: positive})
            p['source_validation_texts'][SOURCE] = header + '\n\n' + indent + positive
            self.assertIsNone(validate(POSITIVE, p, question=question)['answer'])
        p = packet({SOURCE: positive})
        p['source_validation_texts'][SOURCE] = 'summary: >\n  Metadata value.\n\n## Observed scope\n\n' + positive
        self.assertIsNotNone(validate(POSITIVE, p, question=question)['answer'])

    def test_sixth_metadata_scalar_retains_raw_scope_limits(self):
        bodies = {f'2026-10-13-12000{i}-metadata': SUPPORT for i in range(6)}
        p = packet(bodies)
        p['source_validation_texts'][list(bodies)[-1]] = 'summary: >\n\n  ' + CONTRARY
        self.assertIsNone(validate(POSITIVE, p, list(bodies), SCOPED_QUESTION)['answer'])

    def test_split_predicates_cannot_expand_a_read_only_method_scope(self):
        question = 'Which supported read-only method reads Longhorn volume metadata?'
        for separator in (' and ', ', and ', ': '):
            body = ('`scope-reader` supports read-only pod metadata' + separator
                    + 'Longhorn volume metadata requires a separate reader.')
            self.assertIsNone(validate(POSITIVE, packet({SOURCE: body}), question=question)['answer'])
        body = '`scope-reader` supports read-only Longhorn volume and replica metadata.'
        self.assertIsNotNone(validate(POSITIVE, packet({SOURCE: body}), question=question)['answer'])

    def test_mutating_actions_and_pronominal_tails_fail_before_display_caps(self):
        question = 'Which supported read-only method reads Longhorn volume metadata?'
        body = '`scope-reader` supports read-only Longhorn volume metadata.'
        p = packet({SOURCE: body})
        for operation in ('delete', 'write', 'update', 'patch', 'read-write'):
            wrong = 'Use `scope-reader` to ' + operation + ' Longhorn volume metadata.'
            self.assertIsNone(validate(wrong, p, question=question)['answer'])
            tail = POSITIVE + ' It can also ' + operation + '.'
            self.assertIsNone(validate(tail, p, question=question)['answer'])
            long_answer = POSITIVE + ' ' + ('Recorded background. ' * 110) + ' It can also ' + operation + '.'
            self.assertGreater(len(long_answer), 2000)
            self.assertIsNone(validate(long_answer, p, question=question)['answer'])
        self.assertIsNotNone(validate('Use `scope-reader` to read Longhorn volume metadata.', p,
                                      question=question)['answer'])

    def test_requested_logs_and_list_are_required_for_a_recommendation(self):
        body = '`scope-reader` supports read-only pod metadata.'
        partial = 'The cited text records `scope-reader` support for pod metadata as a partial fact.'
        for question, operation in (('Which supported read-only method reads pod logs?', 'logs'),
                                    ('Which supported read-only method lists pod metadata?', 'list')):
            p = packet({SOURCE: body})
            self.assertIsNone(validate('Use `scope-reader` for pod metadata.', p, question=question)['answer'])
            self.assertIsNotNone(validate(partial, p, question=question)['answer'])
            supported = '`scope-reader` supports read-only pod ' + operation + ' metadata.'
            answer = 'Use `scope-reader` for pod ' + operation + ' metadata.'
            result = validate(answer, packet({SOURCE: supported}), question=question)
            self.assertIsNotNone(result['answer'])
            self.assertTrue(result['escalate'])

    def test_coordinated_simple_methods_do_not_share_one_method_support(self):
        question = 'Which supported read-only method reads Longhorn volume metadata?'
        body = '`alpha` is the supported read-only method for Longhorn volume metadata.'
        for join in (' and ', ' or ', ' with ', ' and/or '):
            answer = 'Use `alpha`' + join + '`beta` for Longhorn volume metadata.'
            self.assertIsNone(validate(answer, packet({SOURCE: body}), question=question)['answer'])
        result = validate('Use `alpha` for Longhorn volume metadata.', packet({SOURCE: body}), question=question)
        self.assertIsNotNone(result['answer'])
        self.assertTrue(result['escalate'])

    def test_literal_path_identity_preserves_case_and_quoted_periods(self):
        question = 'Which supported read-only method reads Longhorn volume metadata?'
        path = '/opt/bin/ScopeClient'
        body = '`' + path + '` is the supported read-only method for Longhorn volume metadata.'
        p = packet({SOURCE: body})
        for wrong in ('/opt/bin/scopeclient', '/opt/bin/ScopeClient.'):
            self.assertIsNone(validate('Use `' + wrong + '` for Longhorn volume metadata.', p,
                                       question=question)['answer'])
        self.assertIsNotNone(validate('Use `' + path + '` for Longhorn volume metadata.', p,
                                      question=question)['answer'])
        dotted = packet({SOURCE: body.replace('ScopeClient`', 'ScopeClient.`')})
        self.assertIsNotNone(validate('Use `' + path + '.` for Longhorn volume metadata.', dotted,
                                      question=question)['answer'])

    def test_full_source_metadata_cannot_supply_positive_excerpt_scope(self):
        positive = '`scope-reader` is the supported read-only metadata method for Longhorn volumes.'
        question = 'Which supported read-only metadata method reads Longhorn volume metadata?'
        for raw in ('# ' + positive, '---\ndescription: ' + positive + '\n---',
                    '## Example\n\n```text\n' + positive + '\n```',
                    '```text\n~~~\n' + positive + '\n```',
                    '- ```text\n  ' + positive + '\n  ```',
                    '1. ```text\n   ' + positive + '\n   ```',
                    '<pre>\n' + positive + '\n</pre>', '<code>\n' + positive + '\n</code>',
                    '<!--\n' + positive + '\n-->'):
            p = packet({SOURCE: positive})
            p['source_validation_texts'][SOURCE] = raw
            self.assertIsNone(validate(POSITIVE, p, question=question)['answer'])
        body = '# Scoped source\n\n## Recorded scope\n\nAs of 2026-10-13, ' + positive
        p = packet({SOURCE: positive})
        p['source_validation_texts'][SOURCE] = body
        self.assertIsNotNone(validate(POSITIVE, p, question=question)['answer'])

    def test_sixth_raw_metadata_limit_is_not_lost_in_the_body_filter(self):
        bodies = {f'2026-10-13-12000{i}-metadata': SUPPORT for i in range(6)}
        for raw in ('# ' + CONTRARY, '- ```text\n  ' + CONTRARY + '\n  ```',
                    '<pre>\n' + CONTRARY + '\n</pre>', '<!--\n' + CONTRARY + '\n-->'):
            p = packet(bodies)
            p['source_validation_texts'][list(bodies)[-1]] = raw
            self.assertIsNone(validate(POSITIVE, p, list(bodies), SCOPED_QUESTION)['answer'])

    def test_direct_synthetic_scoped_support_remains_medium_and_escalates(self):
        p = packet({SOURCE: SUPPORT})
        result = validate(POSITIVE, p, question=SCOPED_QUESTION)
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])

    def test_separate_method_cannot_expand_base_reader(self):
        other = '`separate-rest` is the supported metadata method for Longhorn volumes, replicas, node disk capacity and backups.'
        p = packet({SOURCE: BASE, '2026-10-13-120001-separate-channel': other})
        self.assertIsNone(validate(WRONG, p, list(p['source_validation_texts']))['answer'])

    def test_jobs_and_cronjobs_do_not_follow_from_generic_metadata(self):
        static = BASE + ' `devbox-kube-read` has no CronJobs or Jobs metadata operation.'
        for kind in ('Jobs', 'CronJobs'):
            question = 'Which supported read-only metadata method reads ' + kind + ' metadata?'
            wrong = 'Use `devbox-kube-read` for ' + kind + ' metadata.'
            result = validate(wrong, packet({SOURCE: static}), question=question)
            self.assertIsNone(result['answer'])
            self.assertTrue(result['escalate'])
            bounded = 'The cited text does not establish a supported ' + kind + ' method for `devbox-kube-read`.'
            result = validate(bounded, packet({SOURCE: static}), question=question)
            self.assertIsNotNone(result['answer'])
            self.assertEqual(result['confidence'], 'medium')
            self.assertTrue(result['escalate'])

    def test_direct_synthetic_cronjob_support_is_not_live_authorization(self):
        question = 'Which supported read-only metadata method reads CronJob metadata?'
        body = '`batch-reader` is the supported read-only method for CronJob metadata.'
        answer = 'Use `batch-reader` for CronJob metadata.'
        result = validate(answer, packet({SOURCE: body}), question=question)
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])
        self.assertIsNone(validate(answer.replace('CronJob', 'Job'), packet({SOURCE: body}),
                                   question=question.replace('CronJob', 'Job'))['answer'])

    def test_yaml_intent_is_not_live_batch_scope_proof(self):
        question = 'Which supported read-only metadata method reads CronJob metadata?'
        body = ('`batch-reader` is the supported installed live read-only method for CronJob '
                'metadata in a public YAML intent.')
        answer = 'Use the installed live `batch-reader` for CronJob metadata.'
        self.assertIsNone(validate(answer, packet({SOURCE: body}), question=question)['answer'])

    def test_actual_base_boundary_and_failed_logs_remain_source_limited(self):
        answer = ('`devbox-kube-read` supports only pod, deployment, and statefulset metadata. '
                  '`devbox-kube-read` pod logs fail with binding_window_unsupported.')
        result = validate(answer)
        self.assertIsNotNone(result['answer'])
        self.assertTrue(result['escalate'])
        self.assertIn('binding_window_unsupported', result['answer'])

    def test_source_limited_negative_keeps_a_separate_supported_method_separate(self):
        other = ('`scope-reader` is the supported read-only method for Longhorn volume, '
                 'replica, node disk capacity and backup metadata.')
        bodies = {SOURCE: BASE, '2026-10-13-120001-separate-method': other}
        answer = ('The cited text does not establish a supported Longhorn metadata channel '
                  'for `devbox-kube-read`. Use `scope-reader` for Longhorn volume, replica, '
                  'node disk capacity and backup metadata.')
        result = validate(answer, packet(bodies), list(bodies))
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])

    def test_narrow_pod_list_does_not_supply_logs_or_an_unrestricted_list(self):
        question = 'Which supported read-only method lists pod metadata?'
        body = '`pod-reader` is the supported read-only method for a narrow pod list.'
        p = packet({SOURCE: body})
        good = validate('Use `pod-reader` for a narrow pod list.', p, question=question)
        self.assertIsNotNone(good['answer'])
        self.assertIsNone(validate('Use `pod-reader` for a pod list.', p, question=question)['answer'])
        self.assertIsNone(validate('Use `pod-reader` for pod logs.', p, question=question)['answer'])

    def test_sixth_raw_citation_contrary_scope_is_checked(self):
        bodies = {f'2026-10-13-12000{i}-metadata': SUPPORT for i in range(6)}
        p = packet(bodies)
        p['source_validation_texts'][list(bodies)[-1]] += '\n' + CONTRARY
        self.assertIsNone(validate(POSITIVE, p, list(bodies), SCOPED_QUESTION)['answer'])

    def test_missing_sixth_full_body_fails_closed(self):
        bodies = {f'2026-10-13-12000{i}-metadata': SUPPORT for i in range(6)}
        p = packet(bodies)
        p['source_validation_texts'][list(bodies)[-1]] = ' '
        result = validate(POSITIVE, p, list(bodies), SCOPED_QUESTION)
        self.assertIsNone(result['answer'])
        self.assertIn('Complete cited source text is unavailable', result['superseded_note'])

    def test_uncited_scope_conflict_does_not_supply_capability_or_veto(self):
        p = packet({SOURCE: SUPPORT, 'unused': CONTRARY})
        self.assertIsNotNone(validate(POSITIVE, p, question=SCOPED_QUESTION)['answer'])

    def test_source_mapping_keys_do_not_supply_capability(self):
        p = packet({SOURCE: SUPPORT})
        p['source_validation_texts'] = {SUPPORT: 'Generic metadata only.'}
        self.assertIsNone(validate(POSITIVE, p, question=SCOPED_QUESTION)['answer'])

    def test_complete_draft_is_checked_before_answer_display_cap(self):
        text = POSITIVE + ' ' + ('Recorded background. ' * 110) + WRONG
        self.assertGreater(len(text), 2000)
        self.assertIsNone(validate(text, packet({SOURCE: SUPPORT}), question=SCOPED_QUESTION)['answer'])

    async def test_actual_assembly_checks_late_contrary_text_without_model_expansion(self):
        body = '## Durable claims\n' + SUPPORT + '\n\n## Context\n' + ('Background context. ' * 200) + '\n' + CONTRARY
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=[{'name': SOURCE, 'score': .7}]), \
             mock.patch.object(wiki.idx, 'get', return_value={'body': body}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': POSITIVE, 'confidence': 'high', 'sources': [SOURCE], 'escalate': False,
             })) as distill, mock.patch.object(graph, '_record_query'):
            result = await graph._graph_answer_impl(SCOPED_QUESTION)
            search = await graph._assemble_packet(SCOPED_QUESTION, 3)
        self.assertIsNone(result['answer'])
        sent = distill.call_args.args[1]
        self.assertNotIn('source_validation_texts', sent)
        self.assertNotIn(CONTRARY, sent)
        self.assertNotIn('Background context. Background context.', sent)
        self.assertNotIn('source_validation_texts', search)

    async def assembled_named_answer(self, question, body, answer):
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=[{'name': SOURCE, 'score': .7}]), \
             mock.patch.object(wiki.idx, 'get', return_value={'body': body}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': answer, 'confidence': 'high', 'sources': [SOURCE], 'escalate': False,
             })) as distill, mock.patch.object(graph, '_record_query'):
            result = await graph._graph_answer_impl(question)
            assembled = await graph._assemble_packet(question, 3, answer_evidence=True)
        self.assertIn('source_validation_texts', assembled)
        self.assertNotIn('source_validation_texts', distill.call_args.args[1])
        return result

    async def test_named_client_question_loads_private_scope_and_rejects_pod_transfer(self):
        question = 'Can `scope-client` read Longhorn volume metadata?'
        body = '`scope-client` supports only read-only pod metadata.'
        answer = 'Use `scope-client` for Longhorn volume metadata.'
        result = await self.assembled_named_answer(question, body, answer)
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])

    async def test_named_read_and_generic_resource_questions_keep_supported_controls(self):
        cases = (
            ('Can `scope-client` read Longhorn volume metadata?',
             '`scope-client` supports read-only Longhorn volume metadata.',
             'Use `scope-client` to read Longhorn volume metadata.'),
            ('Can `scope-client` list pod metadata?',
             '`scope-client` supports narrow read-only pod list metadata.',
             'Use `scope-client` for a narrow pod list.'),
            ('What resources can `scope-reader` read?',
             '`scope-reader` supports read-only Longhorn volume metadata.',
             'Use `scope-reader` for Longhorn volume metadata.'),
        )
        for question, body, answer in cases:
            result = await self.assembled_named_answer(question, body, answer)
            self.assertIsNotNone(result['answer'])
            self.assertEqual(result['confidence'], 'medium')
            self.assertTrue(result['escalate'])

    async def test_unrelated_answer_does_not_load_private_scope_context(self):
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as full:
            for question in ('What is a volume?', 'What is the `scope-client` version?',
                             'Where is `scope-reader`?'):
                p = await graph._assemble_packet(question, 3, answer_evidence=True)
                self.assertNotIn('source_validation_texts', p)
        full.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()

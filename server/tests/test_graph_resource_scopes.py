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

    async def test_unrelated_answer_does_not_load_private_scope_context(self):
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as full:
            p = await graph._assemble_packet('What is a volume?', 3, answer_evidence=True)
        full.assert_not_awaited()
        self.assertNotIn('source_validation_texts', p)


if __name__ == '__main__':
    unittest.main()

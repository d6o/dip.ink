"""Cutoff answer regressions with synthetic text and mocked local sources."""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('NEO4J_PASSWORD', 'test-password')
os.environ.setdefault('OPENAI_API_KEY', 'test-only')
os.environ.setdefault('WIKI_MCP_BACKGROUND_REINDEX', '0')
import graph
import wiki

CUTOFF = '2026-10-13T02:37:29Z'
QUESTION = ('Is there a new verified independent off-cluster age recipient, protected '
            'restore-identity path, or custody receipt for the Infisical PostgreSQL backup after ' + CUTOFF + '?')
OLD = 'As of 2026-10-11T12:05:54Z, Infisical PostgreSQL independent backup custody remains unverified.'
LATER = 'As of 2026-10-14T12:00:00Z, a new Infisical PostgreSQL independent custody receipt is verified.'
WRONG = 'No new verified independent custody receipt exists after ' + CUTOFF + '.'
SAFE = 'The retrieved dated evidence contains no qualifying custody receipt after ' + CUTOFF + '.'
SOURCE = '2026-10-13-120000-backup-evidence'


def packet(bodies=None, private=True):
    bodies = bodies if bodies is not None else {SOURCE: OLD}
    out = {'facts': [{'source_slug': name, 'fact': text, 'current': True}
                     for name, text in bodies.items()],
           'entities': [], 'source_excerpt': None,
           'semantic_notes': [{'name': name, 'content': text} for name, text in bodies.items()]}
    if private:
        out['source_validation_texts'] = dict(bodies)
    return out


def validate(answer, p=None, sources=None):
    return graph._validate_distilled_answer(
        {'answer': answer, 'sources': sources or [SOURCE], 'confidence': 'high', 'escalate': False},
        p if p is not None else packet(), QUESTION,
    )[0]


class TemporalAnswerTests(unittest.IsolatedAsyncioTestCase):
    def test_old_current_fact_cannot_support_universal_after_cutoff(self):
        result = validate(WRONG)
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])

    def test_bounded_retrieved_negative_is_medium_and_escalates(self):
        result = validate(SAFE)
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])

    def test_sixth_raw_citation_conflict_is_checked_before_display_cap(self):
        bodies = {f'2026-10-13-12000{i}-backup': OLD for i in range(6)}
        p = packet(bodies)
        p['source_validation_texts'][list(bodies)[-1]] = OLD + '\n' + LATER
        result = validate(SAFE, p, list(bodies))
        self.assertIsNone(result['answer'])

    def test_missing_sixth_full_body_fails_closed(self):
        bodies = {f'2026-10-13-12000{i}-backup': OLD for i in range(6)}
        p = packet(bodies)
        p['source_validation_texts'][list(bodies)[-1]] = '  '
        self.assertIsNone(validate(SAFE, p, list(bodies))['answer'])

    def test_metadata_disguised_as_body_cannot_date_a_retrieved_negative(self):
        undated = 'Infisical PostgreSQL backup custody remains unverified.'
        bodies = (
            '# 2026-10-14-120000-infisical-postgresql-backup\n\n' + undated,
            '## Infisical PostgreSQL backup custody publication on 2026-10-14\n\n' + undated,
            'Infisical PostgreSQL backup custody remains unverified on `2026-10-14-receipt.md`.',
            'Infisical PostgreSQL backup custody remains unverified; URL: https://example.invalid/2026-10-14/',
            '## Example\n\n' + OLD,
            'The maintenance completed on 2026-10-14, while ' + undated,
        )
        for body in bodies:
            with self.subTest(body=body):
                result = validate(SAFE, packet({SOURCE: body}))
                self.assertIsNone(result['answer'])
                self.assertTrue(result['escalate'])

    def test_foreign_fence_marker_cannot_promote_a_full_source_observation(self):
        for opening, nested, closing in (('```text', '~~~', '```'),
                                         ('~~~text', '```', '~~~'),
                                         ('````text', '```', '````')):
            p = packet()
            p['source_validation_texts'][SOURCE] = opening + '\n' + nested + '\n' + OLD + '\n' + closing
            self.assertIsNone(validate(SAFE, p)['answer'])

    def test_code_filter_preserves_the_raw_possible_receipt_veto(self):
        p = packet()
        p['source_validation_texts'][SOURCE] = OLD + '\n\n```text\n~~~\n' + LATER + '\n```'
        self.assertIsNone(validate(SAFE, p)['answer'])

    def test_full_metadata_body_cannot_fall_back_to_a_dated_excerpt(self):
        p = packet()
        p['source_validation_texts'][SOURCE] = (
            '# As of 2026-10-14, Infisical PostgreSQL backup custody remains unverified.')
        self.assertIsNone(validate(SAFE, p)['answer'])

    def test_sixth_possible_receipt_with_a_url_still_requires_abstention(self):
        bodies = {f'2026-10-13-12000{i}-backup': OLD for i in range(6)}
        p = packet(bodies)
        p['source_validation_texts'][list(bodies)[-1]] += '\n\n' + LATER + ' https://example.invalid/receipt'
        self.assertIsNone(validate(SAFE, p, list(bodies))['answer'])

    def test_metadata_shaped_draft_is_not_removed_before_validation(self):
        self.assertIsNone(validate(SAFE + '\nas_of: ' + WRONG)['answer'])

    def test_missing_full_body_cannot_use_old_positive_excerpt(self):
        p = packet()
        p['source_validation_texts'][SOURCE] = ''
        self.assertIsNone(validate(SAFE, p)['answer'])

    def test_uncited_conflict_does_not_change_cited_subset(self):
        p = packet({SOURCE: OLD, 'other-backup': LATER})
        self.assertIsNotNone(validate(SAFE, p)['answer'])

    def test_complete_draft_is_checked_before_answer_cap(self):
        text = SAFE + ' ' + 'Historical background. ' * 110 + WRONG
        self.assertGreater(len(text), 2000)
        self.assertIsNone(validate(text)['answer'])

    def test_validation_mapping_uses_values_not_source_names(self):
        p = packet()
        p['source_validation_texts'] = {OLD: 'No dated observation.'}
        self.assertIsNone(validate(SAFE, p)['answer'])

    async def test_real_assembly_hides_full_bodies_from_model_and_search(self):
        hidden = LATER.replace('T12:00:00Z', '')
        body = '## Durable claims\n' + OLD + '\n\n## Context\n' + ('Background context. ' * 200) + '\n' + hidden
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=[{'name': SOURCE, 'score': .7}]), \
             mock.patch.object(wiki.idx, 'get', return_value={'body': body}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': SAFE, 'confidence': 'high', 'sources': [SOURCE], 'escalate': False,
             })) as distill, mock.patch.object(graph, '_record_query'):
            result = await graph._graph_answer_impl(QUESTION)
            search = await graph._assemble_packet(QUESTION, 3)
        self.assertIsNone(result['answer'])
        sent = distill.call_args.args[1]
        self.assertNotIn('source_validation_texts', sent)
        self.assertNotIn(hidden, sent)
        self.assertNotIn('Background context. Background context.', sent)
        self.assertNotIn('source_validation_texts', search)
        self.assertNotIn('credential_validation_texts', search)

    async def test_current_view_also_excludes_private_full_text(self):
        p = packet()
        p['temporal_context'] = graph._temporal_context(p)
        self.assertNotIn('source_validation_texts', str(graph._current_evidence_packet(p)))

    async def test_empty_search_abstains_without_universal_negative(self):
        with mock.patch.object(graph, '_assemble_packet', new=mock.AsyncMock(return_value=packet({}))), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock()) as distill, \
             mock.patch.object(graph, '_record_query'):
            result = await graph._graph_answer_impl(QUESTION)
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])
        distill.assert_not_awaited()

    async def test_unrelated_question_does_not_read_validation_context(self):
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as full:
            p = await graph._assemble_packet('What is a backup?', 3, answer_evidence=True)
        self.assertNotIn('source_validation_texts', p)
        full.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()

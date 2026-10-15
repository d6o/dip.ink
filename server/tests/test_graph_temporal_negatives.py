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

    def test_contracted_and_restrictive_absence_abstains_in_the_answer_path(self):
        for text in ("There isn't a new custody receipt after " + CUTOFF + '.',
                     'There isn\u2019t a new custody receipt after ' + CUTOFF + '.',
                     'All custody receipts are dated before ' + CUTOFF + '.'):
            result = validate(text)
            self.assertIsNone(result['answer'])
            self.assertEqual(result['confidence'], 'not_found')
            self.assertTrue(result['escalate'])

    def test_universal_temporal_tails_cannot_hide_after_the_display_cap(self):
        for tail in ("There isn't a new custody receipt after " + CUTOFF + '.',
                     'All custody receipts are dated before ' + CUTOFF + '.'):
            text = SAFE + ' ' + ('Recorded background. ' * 110) + tail
            self.assertGreater(len(text), 2000)
            self.assertIsNone(validate(text)['answer'])

    def test_coordinated_restrictive_absence_does_not_survive_validation(self):
        for clause in ('All custody receipts are verified and dated before ',
                       'All custody\nreceipts are verified and\ndated before '):
            draft = clause + CUTOFF + '.'
            result = validate(draft)
            self.assertIsNone(result['answer'])
            self.assertEqual(result['confidence'], 'not_found')
            self.assertTrue(result['escalate'])
            self.assertIn('temporal absence claim', result['superseded_note'])
            long_draft = SAFE + ' ' + ('Recorded background. ' * 110) + draft
            self.assertGreater(len(long_draft), 2000)
            late_result = validate(long_draft)
            self.assertIsNone(late_result['answer'])
            self.assertIn('temporal absence claim', late_result['superseded_note'])

    def test_coordinated_raw_receipt_veto_reaches_a_sixth_scalar_body(self):
        bodies = {f'2026-10-13-12000{i}-backup': OLD for i in range(6)}
        for candidate in ('As of 2026-10-14T12:00:00Z, a custody receipt is new and verified.',
                          'As of 2026-10-14T12:00:00Z,\na custody receipt is new and\nverified.'):
            p = packet(bodies)
            p['source_validation_texts'][list(bodies)[-1]] += '\n\nsummary: >\n\n  ' + candidate.replace('\n', '\n  ')
            result = validate(SAFE, p, list(bodies))
            self.assertIsNone(result['answer'])
            self.assertIn('temporal absence claim', result['superseded_note'])

    async def test_private_assembly_preserves_conjoined_raw_receipt_context(self):
        question = 'Is there a new custody receipt after ' + CUTOFF + '?'
        candidate = 'As of 2026-10-14T12:00:00Z,\na custody receipt is new and\nverified.'
        bodies = {SOURCE + '-' + str(index): OLD for index in range(6)}
        bodies[list(bodies)[-1]] += '\n\nsummary: >\n\n  ' + candidate.replace('\n', '\n  ')
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        hits = [{'name': name, 'score': .7 - index * .005} for index, name in enumerate(bodies)]
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=hits), \
             mock.patch.object(wiki.idx, 'get', side_effect=lambda name: {'body': bodies[name]}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': SAFE, 'confidence': 'high', 'sources': list(bodies), 'escalate': False,
             })) as distill, mock.patch.object(graph, '_record_query'):
            result = await graph._graph_answer_impl(question)
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])
        self.assertIn('temporal absence claim', result['superseded_note'])
        self.assertNotIn('source_validation_texts', distill.call_args.args[1])
        self.assertNotIn('receipt is new', distill.call_args.args[1])

    def test_bounded_retrieved_negative_is_medium_and_escalates(self):
        result = validate(SAFE)
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])
        bold = OLD.replace('2026-10-11T12:05:54Z', '**2026-10-11T12:05:54Z**')
        self.assertIsNotNone(validate(SAFE, packet({SOURCE: bold}))['answer'])

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

    def test_full_metadata_scalar_cannot_supply_a_dated_observation(self):
        for header, indent in (('description: |', '  '), ('summary: >', '\t'),
                               ('  description: |-', ' '), ('- description: >+', '  ')):
            p = packet()
            p['source_validation_texts'][SOURCE] = header + '\n\n' + indent + OLD
            self.assertIsNone(validate(SAFE, p)['answer'])
        p = packet()
        p['source_validation_texts'][SOURCE] = 'description: |\n  Metadata value.\n\n## Observed custody\n\n' + OLD
        self.assertIsNotNone(validate(SAFE, p)['answer'])

    def test_sixth_metadata_scalar_does_not_hide_a_possible_receipt(self):
        bodies = {f'2026-10-13-12000{i}-backup': OLD for i in range(6)}
        p = packet(bodies)
        p['source_validation_texts'][list(bodies)[-1]] += '\n\nsummary: >\n\n  ' + LATER
        self.assertIsNone(validate(SAFE, p, list(bodies))['answer'])

    def test_foreign_fence_marker_cannot_promote_a_full_source_observation(self):
        for opening, nested, closing in (('```text', '~~~', '```'),
                                         ('~~~text', '```', '~~~'),
                                         ('````text', '```', '````')):
            p = packet()
            p['source_validation_texts'][SOURCE] = opening + '\n' + nested + '\n' + OLD + '\n' + closing
            self.assertIsNone(validate(SAFE, p)['answer'])

    def test_list_and_html_code_cannot_supply_a_full_source_observation(self):
        for wrapped in ('- ```text\n  ' + OLD + '\n  ```', '1. ```text\n   ' + OLD + '\n   ```',
                        '<pre>\n' + OLD + '\n</pre>', '<code>\n' + OLD + '\n</code>', '<!--\n' + OLD + '\n-->'):
            p = packet()
            p['source_validation_texts'][SOURCE] = wrapped
            self.assertIsNone(validate(SAFE, p)['answer'])

    def test_code_filter_preserves_the_raw_possible_receipt_veto(self):
        for wrapped in ('```text\n~~~\n' + LATER + '\n```',
                        '- ```text\n  ' + LATER + '\n  ```', '<pre>\n' + LATER + '\n</pre>',
                        '<!--\n' + LATER + '\n-->'):
            p = packet()
            p['source_validation_texts'][SOURCE] = OLD + '\n\n' + wrapped
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

    async def test_formatted_question_cutoff_keeps_full_source_validation_private(self):
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        for wrapper in ('`', '**', '__'):
            question = QUESTION.replace(CUTOFF, wrapper + CUTOFF + wrapper)
            with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
                 mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
                 mock.patch.object(graph, 'FUSION', True), \
                 mock.patch.object(wiki.idx, 'search', return_value=[{'name': SOURCE, 'score': .7}]), \
                 mock.patch.object(wiki.idx, 'get', return_value={'body': OLD}), \
                 mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                     'answer': WRONG, 'confidence': 'high', 'sources': [SOURCE], 'escalate': False,
                 })) as distill, mock.patch.object(graph, '_record_query'):
                result = await graph._graph_answer_impl(question)
                assembled = await graph._assemble_packet(question, 3, answer_evidence=True)
            self.assertIsNone(result['answer'])
            self.assertTrue(result['escalate'])
            self.assertIn('source_validation_texts', assembled)
            self.assertNotIn('source_validation_texts', distill.call_args.args[1])

    async def test_coverage_inference_tail_fails_complete_draft_and_private_assembly(self):
        tail = 'This proves complete coverage.'
        prefix = SAFE + ' ' + ('Recorded background. ' * 100)
        draft = prefix + (' ' * (2436 - len(prefix) - len(tail))) + tail
        self.assertEqual(len(draft), 2436)
        large = '## Durable claims\n' + OLD + '\n\n## Context\n' + ('Recorded context. ' * 250) + '\nPRIVATE_BODY_SENTINEL'
        self.assertGreater(len(large), 4096)
        bodies = {SOURCE + '-' + str(index): large for index in range(6)}
        result = validate(draft, packet(bodies), list(bodies))
        self.assertIsNone(result['answer'])
        self.assertIn('temporal absence claim', result['superseded_note'])
        question = 'Is there a new custody receipt after ' + CUTOFF + '?'
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        hits = [{'name': name, 'score': .7 - index * .005} for index, name in enumerate(bodies)]
        outputs = [{'answer': answer, 'confidence': 'high', 'sources': list(bodies), 'escalate': False}
                   for answer in (draft, SAFE)]
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=hits), \
             mock.patch.object(wiki.idx, 'get', side_effect=lambda name: {'body': bodies[name]}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(side_effect=outputs)) as distill, \
             mock.patch.object(graph, '_record_query'):
            rejected = await graph._graph_answer_impl(question)
            safe = await graph._graph_answer_impl(question)
        self.assertIsNone(rejected['answer'])
        self.assertIn('temporal absence claim', rejected['superseded_note'])
        self.assertIsNotNone(safe['answer'])
        self.assertEqual(safe['confidence'], 'medium')
        self.assertTrue(safe['escalate'])
        for call in distill.call_args_list:
            self.assertNotIn('source_validation_texts', call.args[1])
            self.assertNotIn('PRIVATE_BODY_SENTINEL', call.args[1])

    async def test_embedded_inference_fails_large_six_source_private_assembly(self):
        prefix = 'The retrieved dated evidence contains no qualifying custody receipt'
        middle = (', with exhaustive external coverage,', ', because all external sources are covered,',
                  ' with a fully verified inventory', ' because retrieval is complete',
                  ' whose absence confirms completeness',
                  ', establishing exhaustive coverage of every external record,',
                  ', confirming exhaustive external coverage,', ' because the external search is exhaustive',
                  ', indicating the search includes every external receipt,')
        drafts = [prefix + inference + ' after ' + CUTOFF + '.' for inference in middle]
        drafts += [SAFE + ' ' + ('Recorded background. ' * 110) + draft for draft in drafts]
        large = '## Durable claims\n' + OLD + '\n\n## Context\n' + ('Recorded context. ' * 250) + '\nPRIVATE_BODY_SENTINEL'
        self.assertGreater(len(large), 4096)
        bodies = {SOURCE + '-' + str(index): large for index in range(6)}
        for draft in drafts:
            result = validate(draft, packet(bodies), list(bodies))
            self.assertIsNone(result['answer'])
            self.assertIn('temporal absence claim', result['superseded_note'])
        question = 'Is there a new custody receipt after ' + CUTOFF + '?'
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        hits = [{'name': name, 'score': .7 - index * .005} for index, name in enumerate(bodies)]
        outputs = [{'answer': draft, 'confidence': 'high', 'sources': list(bodies), 'escalate': False}
                   for draft in drafts + [SAFE]]
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=hits), \
             mock.patch.object(wiki.idx, 'get', side_effect=lambda name: {'body': bodies[name]}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(side_effect=outputs)) as distill, \
             mock.patch.object(graph, '_record_query'):
            rejected = [await graph._graph_answer_impl(question) for _ in drafts]
            safe = await graph._graph_answer_impl(question)
        for result in rejected:
            self.assertIsNone(result['answer'])
            self.assertIn('temporal absence claim', result['superseded_note'])
        self.assertIsNotNone(safe['answer'])
        self.assertTrue(safe['escalate'])
        for call in distill.call_args_list:
            self.assertNotIn('source_validation_texts', call.args[1])
            self.assertNotIn('PRIVATE_BODY_SENTINEL', call.args[1])

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

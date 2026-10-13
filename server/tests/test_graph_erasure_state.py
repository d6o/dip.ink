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
        self.assertIsNone(qualifier_problem(QUERY, RIGHT, {BOUNDARY: SERVING + " " + STATUS, BINDINGS: RETAINED}))

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

    def test_another_named_product_cannot_supply_positive_absence(self):
        self.assertEqual(qualifier_problem(
            "What is the TargetApp backup cleanup status?", "TargetApp: no backups remain.",
            {"fixture": "OtherApp: verified no backups remain."},
        ), ERASURE_REASON)

    def test_unidentified_completion_cannot_borrow_another_cleanup_ticket(self):
        self.assertEqual(qualifier_problem(QUERY, "No backups remain.", {
            BOUNDARY: "k3s#10: verified no backups remain.",
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


class CopyObjectScopeTests(unittest.TestCase):
    def test_dedicated_backups_do_not_establish_shared_archive_absence(self):
        self.assertEqual(qualifier_problem('What is copy cleanup status?', 'No shared archives remain.',
                         {'fixture': 'No dedicated backups remain.'}), ERASURE_REASON)

    def test_serving_copy_removal_does_not_establish_archive_erasure(self):
        self.assertEqual(qualifier_problem('What is archive cleanup status?', 'All shared archives were deleted.',
                         {'fixture': 'All copies were deleted from the serving target.'}), ERASURE_REASON)

    def test_same_kind_serving_target_restriction_still_cannot_prove_absence(self):
        self.assertEqual(qualifier_problem('What is archive cleanup status?', 'All shared archives were deleted.',
                         {'fixture': 'All shared archives were deleted from the serving target.'}), ERASURE_REASON)

    def test_direct_gone_claim_cannot_use_removal_instruction(self):
        self.assertEqual(qualifier_problem('What is retained copy status?', 'The retained copies are gone.',
                         {'fixture': 'No retained copies should remain.'}), ERASURE_REASON)

    def test_direct_gone_claim_with_matched_observation_passes(self):
        text = 'The retained copies are gone.'
        self.assertIsNone(qualifier_problem('What is retained copy status?', text, {'fixture': text}))

    def test_conflicting_zero_receipt_cannot_be_masked_by_direct_predicate(self):
        self.assertEqual(qualifier_problem('What is retained copy status?', 'The retained copies are gone.',
                         {'fixture': 'The retained copies are gone. Zero copies were removed.'}), ERASURE_REASON)

    def test_scope_modifiers_are_not_interchangeable(self):
        for source, claim in (
            ('No dedicated backups remain.', 'No backups remain.'),
            ('All shared archives were deleted.', 'All retained archives were deleted.'),
            ('No local copies remain.', 'No shared copies remain.'),
        ):
            with self.subTest(source=source, claim=claim):
                self.assertEqual(qualifier_problem('What is copy removal status?', claim, {'fixture': source}), ERASURE_REASON)

    def test_historical_serving_result_is_allowed_without_total_copy_claim(self):
        text = 'The serving targets were deleted. Shared archives remain.'
        self.assertIsNone(qualifier_problem('What happened to serving targets and archives?', text, {'fixture': text}))

    def test_bounded_historical_deletion_with_other_copies_retained_is_not_total_erasure(self):
        for text in (
            'Two dedicated backups were removed. Shared archives remain.',
            '3 local copies were deleted. Retained shared copies remain.',
            'One retained copy is gone. Other archives remain.',
        ):
            with self.subTest(text=text):
                self.assertIsNone(qualifier_problem('What is copy cleanup status?', text, {'fixture': text}))

    def test_bounded_deleted_subset_cannot_support_all_copy_absence(self):
        self.assertEqual(qualifier_problem('What is copy cleanup status?', 'All dedicated backups were removed.',
                         {'fixture': 'Two dedicated backups were removed.'}), ERASURE_REASON)

    def test_positive_erasure_date_must_come_from_matching_object_support(self):
        undated = 'ArchivePage'
        unrelated = '2026-10-13-020000-other-object'
        p = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': undated, 'content': 'No shared archives remain.'},
                                {'name': unrelated, 'content': 'No dedicated backups remain.'}]}
        result, _, action = graph._validate_distilled_answer({
            'answer': 'No shared archives remain.', 'confidence': 'high', 'sources': [undated, unrelated],
        }, p, 'What is the archive cleanup status?')
        self.assertIsNone(result['answer'])
        self.assertEqual(action, 'qualifier_mismatch')


class ScopedConflictAndPluralExcerptTests(unittest.IsolatedAsyncioTestCase):
    def test_separate_shared_archives_do_not_veto_dedicated_backup_absence(self):
        self.assertIsNone(qualifier_problem('What is backup cleanup status?', 'No dedicated backups remain.', {
            'backup': 'No dedicated backups remain.', 'archive': 'Shared archives remain.',
        }))

    def test_same_kind_separate_scope_is_not_a_contradiction(self):
        self.assertIsNone(qualifier_problem('What is backup cleanup status?', 'No dedicated backups remain.', {
            'backup': 'No dedicated backups remain.', 'shared': 'Shared backups remain.',
        }))

    def test_same_object_retention_still_rejects_absence(self):
        self.assertEqual(qualifier_problem('What is backup cleanup status?', 'No shared archives remain.', {
            'first': 'No shared archives remain.', 'second': 'Shared archives remain.',
        }), ERASURE_REASON)

    def test_generic_zero_copy_receipt_remains_a_conservative_veto(self):
        self.assertEqual(qualifier_problem('What is backup cleanup status?', 'No dedicated backups remain.', {
            'first': 'No dedicated backups remain.', 'second': 'Zero copies were removed.',
        }), ERASURE_REASON)

    def test_plural_removal_query_keeps_closing_receipt(self):
        body = INTENT + '\n\n' + ('Metadata details. '*400) + '\n\n' + STATUS
        for query in ('What is the status of the retained backups?', 'What is the status of the retained archives?'):
            with self.subTest(query=query):
                text = graph._answer_body_excerpt(body, query)
                self.assertIn('deleted zero dedicated backup copies', text)
                self.assertIn('incomplete and blocked', text)
                self.assertLessEqual(len(text), 2500)

    async def test_actual_answer_preserves_bounded_absence_and_other_retention(self):
        slug = '2026-10-13-120000-dedicated-backup-state'
        content = 'No dedicated backups remain. Shared archives remain.'
        p = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': slug, 'content': content}]}
        with mock.patch.object(graph, '_assemble_packet', new=mock.AsyncMock(return_value=p)), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': content, 'confidence': 'high', 'sources': [slug], 'escalate': False,
             })), mock.patch.object(graph, '_record_query'):
            result = await graph._graph_answer_impl('What is the backup cleanup status?')
        self.assertIsNotNone(result['answer'])
        self.assertIn('Shared archives remain', result['answer'])
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])


class BoundedRemovalDatesAndTicketScopeTests(unittest.TestCase):
    DATE = '2026-10-13-120000-copy-removal-receipt'
    OTHER = '2026-10-13-130000-other-copy-state'

    def validate(self, question, answer, notes):
        p = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': key, 'content': text} for key, text in notes.items()]}
        return graph._validate_distilled_answer({
            'answer': answer, 'confidence': 'high', 'sources': list(notes), 'escalate': False,
        }, p, question)

    def test_exact_counted_deletion_has_its_own_observation_date(self):
        text = 'k3s#23 deleted two dedicated backup copies.'
        result, _, _ = self.validate('What is the status of dedicated backup removal k3s#23?', text,
                                      {self.DATE: text})
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['observation_dates'], {self.DATE: '2026-10-13T12:00:00Z'})
        self.assertIn(text, result['answer'])
        self.assertTrue(result['escalate'])

    def test_exact_serving_history_is_not_total_retained_copy_erasure(self):
        text = 'k3s#10 removed serving targets, not retained copies.'
        result, _, _ = self.validate('What is the status of backup serving-target removal k3s#10?', text,
                                      {self.DATE: text})
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['observation_dates'], {self.DATE: '2026-10-13T12:00:00Z'})

    def test_another_ticket_retention_does_not_veto_supported_absence(self):
        text = 'k3s#23: no shared archives remain.'
        other = 'k3s#99: shared archives remain.'
        self.assertIsNone(qualifier_problem('What is the status of shared archive cleanup k3s#23?', text,
                                            {self.DATE: text, self.OTHER: other}))
        result, _, _ = self.validate('What is the status of shared archive cleanup k3s#23?', text,
                                      {self.DATE: text, self.OTHER: other})
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['observation_dates'], {self.DATE: '2026-10-13T12:00:00Z'})

    def test_undated_absence_cannot_borrow_other_ticket_retention_date(self):
        text = 'k3s#23: no shared archives remain.'
        result, _, _ = self.validate('What is the status of shared archive cleanup k3s#23?', text,
                                    {'CleanupNote': text, self.OTHER: 'k3s#99: shared archives remain.'})
        self.assertIsNone(result['answer'])

    def test_undated_absence_cannot_borrow_other_ticket_absence_date(self):
        text = 'k3s#23: no shared archives remain.'
        result, _, _ = self.validate('What is the status of shared archive cleanup k3s#23?', text,
                                    {'CleanupNote': text, self.OTHER: 'k3s#99: no dedicated backups remain.'})
        self.assertIsNone(result['answer'])

    def test_counted_result_is_not_permission_to_change_the_count(self):
        result, _, _ = self.validate('What is the status of dedicated backup removal k3s#23?',
                                    'k3s#23 deleted three dedicated backup copies.',
                                    {self.DATE: 'k3s#23 deleted two dedicated backup copies.'})
        self.assertIsNone(result['answer'])

    def test_unrelated_gate_in_compound_answer_does_not_hide_copy_receipt(self):
        text = 'k3s#23 deleted two dedicated backup copies.'
        result, _, _ = self.validate('What evidence clears k3s#6, and what is the status of backup removal k3s#23?',
                                    'k3s#6 remains blocked. ' + text, {self.DATE: text})
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['observation_dates'], {self.DATE: '2026-10-13T12:00:00Z'})

    def test_source_instruction_does_not_date_a_bounded_result(self):
        text = 'k3s#23 must delete two dedicated backup copies.'
        result, _, _ = self.validate('What is the status of dedicated backup removal k3s#23?',
                                    'k3s#23 deleted two dedicated backup copies.', {self.DATE: text})
        self.assertIsNone(result['answer'])


class MultiplePredicateAndZeroScopeTests(unittest.IsolatedAsyncioTestCase):
    SOURCE = '2026-10-13-120000-copy-result'

    def packet(self, text):
        return {'facts': [], 'entities': [], 'source_excerpt': None,
                'semantic_notes': [{'name': self.SOURCE, 'content': text}]}

    async def answer(self, draft, text):
        with mock.patch.object(graph, '_assemble_packet', new=mock.AsyncMock(return_value=self.packet(text))), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': draft, 'confidence': 'high', 'sources': [self.SOURCE], 'escalate': False,
             })), mock.patch.object(graph, '_record_query'):
            return await graph._graph_answer_impl('What is the status of retained shared-archive cleanup k3s#23?')

    async def test_counted_predicate_does_not_hide_later_total_claim(self):
        result = await self.answer('Two copies were removed and all shared copies are gone.',
                                   'k3s#23: two copies were removed. Shared copies remain.')
        self.assertIsNone(result['answer'])
        self.assertEqual(result['superseded_note'], ERASURE_REASON)

    async def test_zero_predicate_does_not_hide_later_total_claim(self):
        result = await self.answer('Zero copies were removed and all retained copies are gone.',
                                   'k3s#23: zero copies were removed. Retained copies remain.')
        self.assertIsNone(result['answer'])

    def test_instruction_predicate_does_not_hide_later_total_claim(self):
        self.assertEqual(qualifier_problem('What is retained copy status?',
            'No backups should remain and all retained copies are gone.',
            {'fixture': 'No backups should remain. Retained copies remain.'}), ERASURE_REASON)

    async def test_backup_and_local_zero_masks_preserve_observed_nonremoval(self):
        for text in ('Zero backup copies were removed. Shared archives remain.',
                     'Zero local copies were removed. Shared archives remain.'):
            with self.subTest(text=text):
                self.assertIsNone(qualifier_problem('What is retained copy status?', text, {'fixture': text}))
                result = await self.answer(text, 'k3s#23: ' + text)
                self.assertIsNotNone(result['answer'])
                self.assertIn(text, result['answer'])
                self.assertEqual(result['confidence'], 'medium')
                self.assertTrue(result['escalate'])

    def test_zero_scoped_copy_observations_still_veto_affirmative_erasure(self):
        for modifier in ('backup', 'local', 'old', 'new'):
            with self.subTest(modifier=modifier):
                self.assertEqual(qualifier_problem('What is retained copy status?', 'The retained copies are gone.',
                    {'fixture': f'Zero {modifier} copies were removed. Retained copies remain.'}), ERASURE_REASON)

    def test_instruction_only_source_cannot_make_a_second_predicate_observed(self):
        self.assertEqual(qualifier_problem('What is retained copy status?', 'All retained copies are gone.',
            {'fixture': 'The plan requires cleanup and all retained copies are gone.'}), ERASURE_REASON)


class BoundedCountCannotBorrowRetentionTests(unittest.IsolatedAsyncioTestCase):
    SOURCE = '2026-10-13-120000-removal-receipt'
    OTHER = '2026-10-13-130000-retention-receipt'
    OBSERVED = 'k3s#23 deleted two dedicated backup copies.'
    WRONG = 'k3s#23 deleted three dedicated backup copies.'
    QUERY = 'What is the status of dedicated backup removal k3s#23?'

    def packet(self, notes):
        return {'facts': [], 'entities': [], 'source_excerpt': None,
                'semantic_notes': [{'name': key, 'content': text} for key, text in notes.items()]}

    def test_guard_rejects_wrong_count_with_same_source_retention(self):
        from answer_qualifiers import BOUNDED_REMOVAL_REASON
        self.assertEqual(qualifier_problem(self.QUERY, self.WRONG, {
            self.SOURCE: self.OBSERVED + ' Shared archives remain.',
        }), BOUNDED_REMOVAL_REASON)

    async def test_actual_answer_path_rejects_wrong_count_with_retention(self):
        with mock.patch.object(graph,'_assemble_packet',new=mock.AsyncMock(return_value=self.packet({
            self.SOURCE: self.OBSERVED + ' Shared archives remain.',
        }))), mock.patch.object(graph,'_distill',new=mock.AsyncMock(return_value={
            'answer': self.WRONG,'confidence':'high','sources':[self.SOURCE],'escalate':False,
        })), mock.patch.object(graph,'_record_query'):
            result=await graph._graph_answer_impl(self.QUERY)
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])

    def test_date_helper_requires_direct_bounded_support(self):
        from answer_qualifiers import copy_state_supporting_sources
        self.assertEqual(copy_state_supporting_sources(self.QUERY,self.WRONG,{
            self.SOURCE:self.OBSERVED+' Shared archives remain.',
        }), [])

    def test_correct_count_preserved_but_other_retention_cannot_refresh_its_date(self):
        p=self.packet({self.SOURCE:self.OBSERVED,self.OTHER:'Shared archives remain.'})
        result,_,_=graph._validate_distilled_answer({
            'answer':self.OBSERVED,'confidence':'high','sources':[self.SOURCE,self.OTHER],
        },p,self.QUERY)
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['observation_dates'],{self.SOURCE:'2026-10-13T12:00:00Z'})

    def test_valid_first_result_does_not_hide_a_second_changed_count(self):
        from answer_qualifiers import BOUNDED_REMOVAL_REASON
        self.assertEqual(qualifier_problem(self.QUERY,self.OBSERVED+' '+self.WRONG,{
            self.SOURCE:self.OBSERVED+' Shared archives remain.',
        }), BOUNDED_REMOVAL_REASON)


class ZeroIsARemovalCountTests(unittest.IsolatedAsyncioTestCase):
    SOURCE = '2026-10-13-120000-removal-count-receipt'
    QUESTION = 'What is the status of dedicated backup removal k3s#23?'

    async def answer(self, draft, text):
        p = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': self.SOURCE, 'content': text}]}
        with mock.patch.object(graph,'_assemble_packet',new=mock.AsyncMock(return_value=p)), \
             mock.patch.object(graph,'_distill',new=mock.AsyncMock(return_value={
                 'answer':draft,'confidence':'high','sources':[self.SOURCE],'escalate':False,
             })), mock.patch.object(graph,'_record_query'):
            return await graph._graph_answer_impl(self.QUESTION)

    async def test_zero_does_not_match_observed_two_even_with_retention(self):
        result = await self.answer('k3s#23: zero dedicated backup copies were removed.',
                                  'k3s#23: two dedicated backup copies were removed. Shared archives remain.')
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])

    async def test_correct_sourced_zero_count_remains_dated(self):
        text = 'k3s#23: zero dedicated backup copies were removed. Shared archives remain.'
        result = await self.answer(text,text)
        self.assertIsNotNone(result['answer'])
        self.assertIn('zero dedicated backup copies were removed',result['answer'])
        self.assertEqual(result['observation_dates'],{self.SOURCE:'2026-10-13T12:00:00Z'})

    async def test_zero_and_positive_voices_report_the_same_no_effect(self):
        result = await self.answer('k3s#23: zero dedicated backup copies were removed.',
                                  'k3s#23 deleted zero dedicated backup copies. Shared archives remain.')
        self.assertIsNotNone(result['answer'])
        self.assertTrue(result['escalate'])

    def test_wrong_scope_or_other_ticket_cannot_supply_zero_support(self):
        from answer_qualifiers import BOUNDED_REMOVAL_REASON
        draft = 'k3s#23: zero dedicated backup copies were removed.'
        for text in ('k3s#23: zero shared archive copies were removed.',
                     'k3s#99: zero dedicated backup copies were removed.',
                     'k3s#23: two dedicated backup copies were removed. Shared archives remain.'):
            with self.subTest(text=text):
                self.assertEqual(qualifier_problem(self.QUESTION,draft,{'fixture':text}),BOUNDED_REMOVAL_REASON)

    def test_instruction_or_question_does_not_establish_zero(self):
        from answer_qualifiers import BOUNDED_REMOVAL_REASON
        draft = 'Zero dedicated backup copies were removed.'
        for text in ('Zero dedicated backup copies must be removed.',
                     'Were zero dedicated backup copies removed?'):
            with self.subTest(text=text):
                self.assertEqual(qualifier_problem(self.QUESTION,draft,{'fixture':text}),BOUNDED_REMOVAL_REASON)

    def test_zero_clause_cannot_hide_a_changed_positive_count(self):
        from answer_qualifiers import BOUNDED_REMOVAL_REASON
        draft = 'Zero shared archive copies were removed and three dedicated backup copies were deleted.'
        text = 'Zero shared archive copies were removed and two dedicated backup copies were deleted.'
        self.assertEqual(qualifier_problem('What is backup removal status?',draft,{'fixture':text}),BOUNDED_REMOVAL_REASON)


class ZeroProductScopeMustBeClauseLocalTests(unittest.IsolatedAsyncioTestCase):
    SOURCE = '2026-10-13-120000-copy-state'
    QUERY = 'What is the TargetApp backup removal status?'
    DRAFT = 'TargetApp: zero dedicated backup copies were removed.'

    async def answer(self, draft, text):
        p={'facts':[],'entities':[],'source_excerpt':None,
           'semantic_notes':[{'name':self.SOURCE,'content':text}]}
        with mock.patch.object(graph,'_assemble_packet',new=mock.AsyncMock(return_value=p)), \
             mock.patch.object(graph,'_distill',new=mock.AsyncMock(return_value={
                 'answer':draft,'confidence':'high','sources':[self.SOURCE],'escalate':False,
             })), mock.patch.object(graph,'_record_query'):
            return await graph._graph_answer_impl(self.QUERY)

    async def test_nearby_product_mention_does_not_supply_zero_relation(self):
        result=await self.answer(self.DRAFT,
            'TargetApp cleanup remains pending. OtherApp: zero dedicated backup copies were removed.')
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])

    async def test_correct_product_voice_match_retains_date(self):
        result=await self.answer(self.DRAFT,'TargetApp deleted zero dedicated backup copies.')
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['observation_dates'],{self.SOURCE:'2026-10-13T12:00:00Z'})

    def test_direct_guard_rejects_other_product_and_comparison_support(self):
        from answer_qualifiers import BOUNDED_REMOVAL_REASON
        for text in ('OtherApp: zero dedicated backup copies were removed.',
                     'TargetApp works with OtherApp: zero dedicated backup copies were removed.'):
            with self.subTest(text=text):
                self.assertEqual(qualifier_problem(self.QUERY,self.DRAFT,{'fixture':text}),BOUNDED_REMOVAL_REASON)

    def test_date_helper_does_not_borrow_other_product_zero_state(self):
        from answer_qualifiers import copy_state_supporting_sources
        self.assertEqual(copy_state_supporting_sources(self.QUERY,self.DRAFT,{
            self.SOURCE:'TargetApp cleanup remains pending. OtherApp: zero dedicated backup copies were removed.',
        }),[])

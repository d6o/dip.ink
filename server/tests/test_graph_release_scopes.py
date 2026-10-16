"""Synthetic validator and in-process assembly checks. No live request occurs."""
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
from release_scope_qualifiers import RELEASE_SCOPE_BODY_REASON, RELEASE_SCOPE_REASON, RELEASE_SCOPE_SUFFIX
from resource_scope_qualifiers import RESOURCE_SCOPE_REASON
from test_release_scope_qualifiers import (
    APP, BASELINE, COMPLETE, EXACT_QUESTION, METHOD, NEW, NONCURRENT, OLD, OLD_METHOD, PREVIEW,
    PREVIOUS, PROPERTY_CASES, PROPERTY_GOOD_CASES, QUESTION, REV, WRONG, problem as release_problem,
)


def packet(bodies=None, *, current=False):
    bodies = {NEW: COMPLETE} if bodies is None else bodies
    value = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': name, 'content': text} for name, text in bodies.items()],
             'source_validation_texts': dict(bodies)}
    if current:
        value['temporal_context'] = graph._temporal_context(value)
    return value


def validate(answer=COMPLETE, p=None, sources=None, question=QUESTION):
    return graph._validate_distilled_answer(
        {'answer': answer, 'confidence': 'high', 'sources': sources or [NEW], 'escalate': False},
        packet() if p is None else p, question,
    )


class GraphReleaseScopeTests(unittest.IsolatedAsyncioTestCase):
    def assert_release_rejection(self, result, reason=RELEASE_SCOPE_REASON):
        value, grounded, action = result
        self.assertEqual(action, 'release_scope_mismatch')
        self.assertTrue(grounded)
        self.assertIsNone(value['answer'])
        self.assertEqual(value['confidence'], 'not_found')
        self.assertEqual(value['sources'], [])
        self.assertTrue(value['escalate'])
        self.assertEqual(value['superseded_note'], reason)

    def assert_qualified(self, value):
        self.assertIsNotNone(value['answer'])
        self.assertEqual(value['confidence'], 'medium')
        self.assertTrue(value['escalate'])
        self.assertTrue(value['answer'].endswith(RELEASE_SCOPE_SUFFIX))
        self.assertNotIn('Live state is not verified', value['answer'])

    def test_wrong_transfer_reaches_new_guard_before_old_gates(self):
        for question in (QUESTION, NONCURRENT):
            self.assert_release_rejection(validate(WRONG, packet({OLD: PREVIEW, NEW: COMPLETE}),
                                                   [NEW, OLD], question))
        bodies = {OLD: PREVIEW.replace('ComputeHours', 'GPUHours'), NEW: COMPLETE.replace('ComputeHours', 'GPUHours')}
        self.assert_release_rejection(validate(WRONG.replace('ComputeHours', 'GPUHours'), packet(bodies),
                                               list(bodies), EXACT_QUESTION))

    def test_correct_receipt_keeps_dated_checks_without_new_live_claim(self):
        for current, question in ((False, NONCURRENT), (True, QUESTION)):
            value, grounded, action = validate(p=packet(current=current), question=question)
            self.assert_qualified(value)
            self.assertIn('completed and verified', value['answer'])
            self.assertIn('not production durability', value['answer'])
            self.assertIn('2026-10-16T13:52:00', value['answer'])
            self.assertNotIn('On 2026-10-16', value['answer'])
            self.assertEqual(action, 'downgraded')
            self.assertTrue(grounded)

    def test_proposal_stays_limited_history_not_a_completed_release(self):
        value, _, _ = validate(PREVIEW, packet({OLD: PREVIEW}), [OLD])
        self.assert_qualified(value)
        self.assertIn('no admission receipt or execution', value['answer'])
        self.assertNotIn('completed', value['answer'])
        self.assert_release_rejection(validate(COMPLETE, packet({OLD: PREVIEW}), [OLD]))
        self.assert_release_rejection(validate(PREVIEW, packet({OLD: PREVIEW, NEW: COMPLETE}), [OLD]))

    def test_production_durability_and_escalation_do_not_fix_false_promotion(self):
        for tail in (' Production durability is verified.', ' Use it now.', ' The caller has exec permission.'):
            self.assert_release_rejection(validate(COMPLETE + tail))
        draft = {'answer': WRONG, 'sources': [NEW], 'confidence': 'medium', 'escalate': True}
        self.assert_release_rejection(graph._validate_distilled_answer(draft, packet(), QUESTION))

    def test_subject_method_revision_and_scope_cannot_union(self):
        for body in (COMPLETE.replace('ComputeHours', 'OtherHours'), COMPLETE.replace(REV, PREVIOUS),
                     COMPLETE.replace(METHOD, '`maintenance/other-app/deploy-dev.py`'), PREVIEW):
            self.assert_release_rejection(validate(COMPLETE, packet({NEW: body})))
        self.assert_release_rejection(validate(COMPLETE, packet({OLD: PREVIEW, NEW: COMPLETE.replace(REV, PREVIOUS)}),
                                               [OLD, NEW]))

    def test_stale_current_fact_cannot_outweigh_newer_semantic_completed_body(self):
        old = COMPLETE.replace(REV, PREVIOUS)
        p = packet({NEW: COMPLETE})
        p['facts'] = [{'source_slug': BASELINE, 'fact': old, 'current': True}]
        p['source_validation_texts'][BASELINE] = old
        p['temporal_context'] = graph._temporal_context(p)
        self.assert_release_rejection(validate(old, p, [BASELINE]))
        value, _, _ = validate(COMPLETE, p)
        self.assert_qualified(value)
        sent = graph._current_evidence_packet(p)
        self.assertEqual(sent['facts'], [])
        self.assertNotIn('source_validation_texts', sent)
        self.assertEqual(sent['semantic_notes'][0]['name'], NEW)

    def test_nonhash_revision_still_gets_source_date_and_no_new_check(self):
        text = COMPLETE.replace(REV, '`dev-interface-next`')
        value, _, _ = validate(text, packet({NEW: text}), question=NONCURRENT)
        self.assert_qualified(value)
        self.assertEqual(value['as_of'], '2026-10-16T13:52:00Z')
        self.assertIn('source dates:', value['answer'])

    def test_unrelated_private_keys_cannot_veto_or_supply_support(self):
        p = packet()
        p['source_validation_texts']['unretrieved'] = COMPLETE.replace(REV, PREVIOUS)
        value, _, _ = validate(p=p)
        self.assert_qualified(value)
        p['source_validation_texts'] = {COMPLETE: COMPLETE}
        self.assert_release_rejection(validate(p=p), RELEASE_SCOPE_BODY_REASON)

    def test_missing_private_bodies_fail_closed_on_every_raw_citation(self):
        p = packet()
        del p['source_validation_texts']
        self.assert_release_rejection(validate(p=p), RELEASE_SCOPE_BODY_REASON)
        bodies = {NEW + '-' + str(index): COMPLETE for index in range(6)}
        for absent in ('', ' ', None):
            p = packet(bodies)
            p['source_validation_texts'][list(bodies)[-1]] = absent
            self.assert_release_rejection(validate(p=p, sources=list(bodies)), RELEASE_SCOPE_BODY_REASON)
        self.assert_release_rejection(validate(sources=[NEW, 'invented']), RELEASE_SCOPE_BODY_REASON)

    def test_titles_dates_labels_fences_and_scalars_are_not_positive(self):
        for wrapper in ('# {}', 'description: {}', 'summary: >\n\n  {}', '```text\n{}\n```',
                        '<pre>\n{}\n</pre>', '## Example\n{}'):
            p = packet()
            p['source_validation_texts'][NEW] = wrapper.format(COMPLETE)
            self.assert_release_rejection(validate(p=p))
        self.assert_release_rejection(validate('On 2026-10-16, ' + COMPLETE))

    def test_sixth_raw_citation_and_late_full_source_keep_veto(self):
        bodies = {NEW + '-' + str(index): COMPLETE for index in range(6)}
        p = packet(bodies)
        value, _, _ = validate(p=p, sources=list(bodies))
        self.assertEqual(len(value['sources']), 5)
        contrary = METHOD + ' release status is `proposal`.'
        for wrapper in ('# {}', 'description: {}', 'summary: >\n\n  {}', '```text\n{}\n```'):
            p = packet(bodies)
            p['source_validation_texts'][list(bodies)[-1]] = wrapper.format(contrary)
            self.assert_release_rejection(validate(p=p, sources=list(bodies)))
        p = packet()
        p['source_validation_texts'][NEW] += '\n' + 'Background context. ' * 300 + '\n' + contrary
        self.assertGreater(p['source_validation_texts'][NEW].index(contrary), 4096)
        self.assert_release_rejection(validate(p=p))

    def test_full_draft_unknown_tail_precedes_display_cap(self):
        answer = COMPLETE + ' ' + ('Historical record: ' + COMPLETE + ' ') * 6 + ' The caller has permission.'
        self.assertGreater(answer.index('The caller'), 2000)
        self.assert_release_rejection(validate(answer))

    def test_named_role_policy_checks_every_separator_without_global_identity(self):
        for current, question in ((True, QUESTION), (False, NONCURRENT)):
            for contrary in PROPERTY_CASES:
                self.assert_release_rejection(validate(COMPLETE, packet({NEW: COMPLETE, OLD: contrary}, current=current),
                                                       [NEW, OLD], question))
            for safe in PROPERTY_GOOD_CASES:
                value, _, _ = validate(COMPLETE, packet({NEW: COMPLETE + '\n' + safe}, current=current), question=question)
                self.assert_qualified(value)

    def test_ordered_local_context_and_explicit_identity_roles_use_new_route(self):
        other = COMPLETE.replace('ComputeHours', 'OtherHours')
        for current, question in ((True, QUESTION), (False, NONCURRENT)):
            self.assert_release_rejection(validate(COMPLETE, packet({NEW: other + '\n' + COMPLETE
                                                                   + '\nThe release status = `unknown`.'}, current=current),
                                                   question=question))
            for safe in (other + '\n' + COMPLETE, COMPLETE + '\n' + other + '\nThe release status = `unknown`.',
                         COMPLETE + '\nApp: `OtherHours`\n' + METHOD + ' release scope: `preview-only`.'):
                value, _, _ = validate(COMPLETE, packet({NEW: safe}, current=current), question=question)
                self.assert_qualified(value)
            for requested in (question.replace('interface', 'interface using ' + OLD_METHOD),
                              question.rstrip('?') + ' for revision `dev-interface-next`?',
                              question.replace('ComputeHours', 'OtherHours').rstrip('?') + ', not ComputeHours?'):
                self.assert_release_rejection(validate(COMPLETE, packet({NEW: COMPLETE + '\n' + other}, current=current),
                                                       question=requested))

    def assert_resource_overlap(self, value, event):
        self.assertIsNone(value['answer'])
        self.assertEqual(value['confidence'], 'not_found')
        self.assertTrue(value['escalate'])
        self.assertEqual(value['sources'], [])
        self.assertEqual(value['superseded_note'], RESOURCE_SCOPE_REASON)
        self.assertEqual(event['grounding_action'], 'qualifier_mismatch')

    async def assert_assembly_rejection(self, bodies, answer=COMPLETE, **kwargs):
        value, sent, event = await self.assemble(bodies, answer, **kwargs)
        self.assertIsNone(value['answer'])
        self.assertEqual(value['superseded_note'], RELEASE_SCOPE_REASON)
        self.assertEqual(event['grounding_action'], 'release_scope_mismatch')
        return value, sent, event

    async def assemble(self, bodies, answer, *, question=QUESTION, sources=None, private=None, facts=None):
        edges = [SimpleNamespace(fact=fact, episodes=[name], valid_at='', invalid_at=None)
                 for name, fact in (facts or {}).items()]
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=edges, episodes=[])))
        hits = [{'name': name, 'score': .8 - index * .002} for index, name in enumerate(bodies)]
        context = (mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock(return_value=private))
                   if private is not None else nullcontext())
        with context, mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={name: name for name in facts or {}})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=hits), \
             mock.patch.object(wiki.idx, 'get', side_effect=lambda name: {'body': bodies.get(name, (facts or {}).get(name, ''))}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': answer, 'confidence': 'high', 'sources': sources or list(bodies), 'escalate': False,
             })) as distill, mock.patch.object(graph, '_record_query') as record:
            result = await graph._graph_answer_impl(question)
            assembled = await graph._assemble_packet(question, 8, current_state=graph._current_state_question(question), answer_evidence=True)
            search = await graph._assemble_packet(question, 3)
        self.assertIn('source_validation_texts', assembled)
        self.assertNotIn('source_validation_texts', search)
        self.assertNotIn('PRIVATE_', json.dumps(search))
        self.assertNotIn('release_scope_retrieved_sources', search)
        self.assertNotIn('release_scope_retrieved_sources', distill.call_args.args[1])
        self.assertNotIn('source_validation_texts', distill.call_args.args[1])
        return result, json.loads(distill.call_args.args[1]), record.call_args.args[0]

    async def test_actual_mock_assembly_exact_question_and_noncurrent_use_new_route(self):
        for question in (QUESTION, NONCURRENT, EXACT_QUESTION):
            bodies = {OLD: PREVIEW, NEW: COMPLETE}
            answer = WRONG
            if question == EXACT_QUESTION:
                bodies = {name: text.replace('ComputeHours', 'GPUHours') for name, text in bodies.items()}
                answer = answer.replace('ComputeHours', 'GPUHours')
            value, sent, event = await self.assemble(bodies, answer, question=question)
            self.assertIsNone(value['answer'])
            self.assertEqual(value['superseded_note'], RELEASE_SCOPE_REASON)
            self.assertEqual(event['grounding_action'], 'release_scope_mismatch')
            self.assertEqual(event['temporal_mode'], 'default' if question == NONCURRENT else 'current')
            self.assertNotIn('source_validation_texts', sent)

    async def test_actual_mock_safe_completed_receipt_keeps_recorded_verification(self):
        for question in (QUESTION, NONCURRENT):
            value, _, event = await self.assemble({OLD: PREVIEW, NEW: COMPLETE}, COMPLETE,
                                                  question=question, sources=[NEW])
            self.assert_qualified(value)
            self.assertIn('completed and verified', value['answer'])
            self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_actual_mock_stale_graph_current_and_newer_semantic_receipt(self):
        old = COMPLETE.replace(REV, PREVIOUS)
        value, sent, event = await self.assemble({NEW: COMPLETE}, old, sources=[BASELINE], facts={BASELINE: old})
        self.assertIsNone(value['answer'])
        self.assertEqual(event['grounding_action'], 'release_scope_mismatch')
        self.assertEqual(sent['facts'], [])
        value, _, event = await self.assemble({NEW: COMPLETE}, COMPLETE, facts={BASELINE: old})
        self.assert_qualified(value)
        self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_actual_mock_unhydrated_retrieved_body_retains_private_veto_only(self):
        # Only three public bodies hydrate. The fourth full body still vetoes.
        bodies = {NEW + '-' + str(index): 'ComputeHours source context.' for index in range(4)}
        bodies[list(bodies)[-1]] = COMPLETE
        old = COMPLETE.replace(REV, PREVIOUS)
        value, sent, event = await self.assemble(bodies, old, sources=[OLD], facts={OLD: old})
        self.assertIsNone(value['answer'])
        self.assertEqual(event['grounding_action'], 'release_scope_mismatch')
        self.assertNotIn(COMPLETE, json.dumps(sent))
        self.assertNotIn('release_scope_retrieved_sources', sent)
        self.assertNotIn(list(bodies)[-1], [hit['name'] for hit in sent['semantic_notes']])

    async def test_actual_mock_metadata_only_does_not_supply_completion(self):
        for raw in ('# ' + COMPLETE, 'description: >\n\n  ' + COMPLETE, '```text\n' + COMPLETE + '\n```'):
            value, _, event = await self.assemble({NEW: raw}, COMPLETE)
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'release_scope_mismatch')

    async def test_actual_mock_private_bodies_and_late_limits_never_enter_public_maps(self):
        sentinel = 'PRIVATE_RELEASE_BODY_SENTINEL'
        contrary = METHOD + ' release status is `proposal`.'
        text = COMPLETE + '\n\n## Context\n' + 'Background context. ' * 300 + '\n' + sentinel + '\n' + contrary
        self.assertGreater(text.index(contrary), 4096)
        for question in (QUESTION, NONCURRENT):
            value, sent, event = await self.assemble({NEW: text}, COMPLETE, question=question)
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'release_scope_mismatch')
            self.assertNotIn(sentinel, json.dumps(sent))
            self.assertNotIn(sentinel, json.dumps(value))
            self.assertNotIn(contrary, json.dumps(sent))

    async def test_actual_mock_missing_private_map_and_sixth_raw_metadata(self):
        for private in ({}, {NEW: ''}, {NEW: ' '}, {COMPLETE: COMPLETE}):
            value, _, event = await self.assemble({NEW: COMPLETE}, COMPLETE, private=private)
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'release_scope_mismatch')
            self.assertEqual(value['superseded_note'], RELEASE_SCOPE_BODY_REASON)
        bodies = {NEW + '-' + str(index): COMPLETE for index in range(6)}
        private = dict(bodies)
        private[list(bodies)[-1]] = 'summary: >\n\n  PRIVATE_SIXTH_RELEASE\n  ' + METHOD + ' release scope is `preview-only`.'
        value, sent, event = await self.assemble(bodies, COMPLETE, private=private)
        self.assertIsNone(value['answer'])
        self.assertEqual(event['grounding_action'], 'release_scope_mismatch')
        self.assertNotIn('PRIVATE_SIXTH_RELEASE', json.dumps(sent))

    async def test_actual_mock_formatted_concepts_collect_full_bodies_and_keep_safe_controls(self):
        overlap = QUESTION.replace('dev app-only release interface', '`dev app-only release interface`')
        for question in ('Which ComputeHours `release scope` did the source record?',
                         'What is the current ComputeHours `release scope`?', overlap):
            for answer in (COMPLETE, WRONG):
                collector = mock.AsyncMock(wraps=graph._source_validation_context)
                with mock.patch.object(graph, '_source_validation_context', new=collector):
                    value, sent, event = await self.assemble({NEW: COMPLETE}, answer, question=question)
                self.assertEqual(collector.await_count, 2)
                self.assertNotIn('source_validation_texts', sent)
                if answer == COMPLETE:
                    self.assertIsNone(release_problem(COMPLETE, question=question))
                    self.assert_qualified(value)
                    self.assertEqual(event['grounding_action'], 'downgraded')
                else:
                    self.assertIsNone(value['answer'])
                    self.assertEqual(value['superseded_note'], RELEASE_SCOPE_REASON)
                    self.assertEqual(event['grounding_action'], 'release_scope_mismatch')

    async def test_actual_mock_named_role_plain_copular_colon_equal_case_and_values(self):
        for question in (QUESTION, NONCURRENT):
            for contrary in PROPERTY_CASES[::9]:
                await self.assert_assembly_rejection({NEW: COMPLETE, OLD: contrary}, question=question, sources=[NEW, OLD])
            for safe in PROPERTY_GOOD_CASES[::4]:
                value, _, event = await self.assemble({NEW: COMPLETE + '\n' + safe}, COMPLETE, question=question)
                self.assert_qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')
            for contrary in (OLD_METHOD + ' release status = `unknown`.',
                             METHOD + ' release status: `unknown` for `OtherHours` revision ' + REV + '.',
                             METHOD + ' release scope = `preview-only` for ' + APP + ' revision ' + PREVIOUS + '.'):
                value, _, _ = await self.assemble({NEW: COMPLETE + '\n' + contrary}, COMPLETE, question=question)
                self.assert_qualified(value)

    async def test_actual_mock_named_role_sixth_raw_wrappers_and_late_private_text(self):
        bodies = {NEW + '-' + str(index): COMPLETE for index in range(6)}
        for question in (QUESTION, NONCURRENT):
            for contrary in PROPERTY_CASES[::19]:
                for wrapper in ('# {}', 'summary: >\n\n  {}', 'description: |\n\n  {}', '```text\n{}\n```'):
                    private = dict(bodies)
                    private[list(bodies)[-1]] = wrapper.format(contrary)
                    await self.assert_assembly_rejection(bodies, question=question, private=private)
            contrary = METHOD + ' RELEASE STATUS = `NOT COMPLETED`.'
            text = COMPLETE + '\n' + 'Recorded context. ' * 300 + '\nPRIVATE_ROLE_LIMIT\n' + contrary
            self.assertGreater(text.index(contrary), 4096)
            value, sent, event = await self.assert_assembly_rejection({NEW: text}, question=question)
            self.assertNotIn('PRIVATE_ROLE_LIMIT', json.dumps((value, sent, event)))

    async def test_actual_mock_ordered_local_context_and_explicit_resets(self):
        other = COMPLETE.replace('ComputeHours', 'OtherHours')
        for question in (QUESTION, NONCURRENT):
            bad = other + '\n' + COMPLETE + '\nThe release status is `unknown`.'
            await self.assert_assembly_rejection({NEW: bad}, question=question)
            for safe in (other + '\n' + COMPLETE, COMPLETE + '\n' + other + '\nThe release status is `unknown`.',
                         COMPLETE + '\nMethod: ' + OLD_METHOD + '\nThe release status is `unknown`.',
                         COMPLETE + '\nApp: `OtherHours`\nThe release scope = `preview-only`.'):
                value, _, event = await self.assemble({NEW: safe}, COMPLETE, question=question)
                self.assert_qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')
            for wrapper in ('{}', '# {}', 'summary: >\n\n  {}', '```text\n{}\n```'):
                raw = other + '\n' + COMPLETE + '\n' + 'Recorded context. ' * 300 + '\n' + wrapper.format('The release status = `unknown`.')
                bodies = {NEW + '-' + str(index): COMPLETE for index in range(6)}
                private = dict(bodies)
                private[list(bodies)[-1]] = raw
                await self.assert_assembly_rejection(bodies, question=question, private=private)

    async def test_actual_mock_explicit_query_roles_and_right_identity_controls(self):
        other = COMPLETE.replace('ComputeHours', 'OtherHours')
        bodies = {NEW: COMPLETE + '\n' + other}
        for question in (QUESTION, NONCURRENT):
            for requested in (question.replace('interface', 'interface using ' + OLD_METHOD),
                              question.rstrip('?') + ' for revision `dev-interface-next`?',
                              question.replace('ComputeHours', 'OtherHours').rstrip('?') + ', not ComputeHours?'):
                await self.assert_assembly_rejection(bodies, question=requested)
            right = question.replace('interface', 'interface using ' + METHOD)
            self.assertIsNone(release_problem(COMPLETE, question=right))
            value, _, event = await self.assemble({NEW: COMPLETE}, COMPLETE, question=right)
            self.assert_qualified(value)
            self.assertEqual(event['grounding_action'], 'downgraded')
            branch = COMPLETE.replace(REV, '`dev-interface-next`')
            right = question.rstrip('?') + ' for revision `dev-interface-next`?'
            self.assertIsNone(release_problem(branch, {NEW: branch}, question=right))
            value, _, event = await self.assemble({NEW: branch}, branch, question=right)
            self.assert_qualified(value)
            self.assertEqual(event['grounding_action'], 'downgraded')
            right = question.rstrip('?') + ' for revision dev-interface-next?'
            value, _, _ = await self.assemble({NEW: branch}, branch, question=right)
            self.assert_qualified(value)
        right = 'What is the current OtherHours release interface, not ComputeHours?'
        value, _, _ = await self.assemble(bodies, other, question=right)
        self.assert_qualified(value)
        historical = 'Historical record: ' + COMPLETE + ' Historical record: ' + COMPLETE.replace(REV, PREVIOUS)
        query = 'Compare ComputeHours release scopes for revisions ' + REV + ' and ' + PREVIOUS + '?'
        value, _, _ = await self.assemble({NEW: COMPLETE, OLD: COMPLETE.replace(REV, PREVIOUS)}, historical, question=query)
        self.assert_qualified(value)

    async def test_actual_mock_uncited_raw_method_limits_do_not_need_global_app_revision(self):
        raw_name = '2026-10-16-140000-raw-release-limit'
        for question in (QUESTION, NONCURRENT):
            for raw in (METHOD + ' release status: `proposal`.',
                        '# ComputeHours release record\n' + METHOD + ' release scope=preview-only.'):
                await self.assert_assembly_rejection({NEW: COMPLETE, raw_name: raw}, sources=[NEW], question=question)
            safe = '# OtherHours release record\n' + METHOD + ' release scope=preview-only.'
            value, _, _ = await self.assemble({NEW: COMPLETE, raw_name: safe}, COMPLETE, question=question, sources=[NEW])
            self.assert_qualified(value)

    async def test_actual_mock_unrelated_private_dates_cannot_hide_relevant_completed_body(self):
        unrelated = '2026-10-19-120000-other-release-record'
        sentinel = '\n' + 'Recorded context. ' * 300 + '\nPRIVATE_LATER_RELEASE_BODY'
        for other in ('OtherHours source metadata.', COMPLETE.replace('ComputeHours', 'OtherHours')):
            original = {OLD: PREVIEW, NEW: COMPLETE + sentinel, unrelated: other}
            for bodies in (original, dict(reversed(list(original.items())))):
                value, sent, event = await self.assert_assembly_rejection(bodies, PREVIEW, sources=[OLD])
                self.assertNotIn('PRIVATE_LATER_RELEASE_BODY', json.dumps((value, sent, event)))
                value, sent, event = await self.assemble(bodies, COMPLETE, sources=[NEW])
                self.assert_qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')
                self.assertNotIn('PRIVATE_LATER_RELEASE_BODY', json.dumps((value, sent, event)))

    async def test_actual_mock_raw_canonical_soft_wraps_keep_veto_without_positive_support(self):
        preview = PREVIEW.replace(REV, REV + ' via ' + METHOD)
        for question in (QUESTION, NONCURRENT):
            for wrapper in ('```text\n{}\n```', 'summary: >\n\n  {}', '# {}'):
                raw = wrapper.format(preview.replace(', limited', ',\nlimited'))
                await self.assert_assembly_rejection({NEW: COMPLETE, OLD: raw}, sources=[NEW, OLD], question=question)
                late = COMPLETE + '\n' + 'Recorded context. ' * 300 + '\n' + raw
                self.assertGreater(late.index(raw), 4096)
                await self.assert_assembly_rejection({NEW: late}, question=question)
                bodies = {NEW + '-' + str(index): COMPLETE for index in range(6)}
                private = dict(bodies)
                private[list(bodies)[-1]] = raw
                await self.assert_assembly_rejection(bodies, question=question, private=private)
                await self.assert_assembly_rejection({NEW: wrapper.format(COMPLETE.replace(', limited', ',\nlimited'))},
                                                     question=question)

    async def test_actual_mock_full_draft_tail_precedes_display(self):
        answer = COMPLETE + ' ' + ('Historical record: ' + COMPLETE + ' ') * 6 + ' Use the driver now.'
        self.assertGreater(answer.index('Use the driver'), 2000)
        value, _, event = await self.assemble({NEW: COMPLETE}, answer)
        self.assertIsNone(value['answer'])
        self.assertEqual(event['grounding_action'], 'release_scope_mismatch')

    async def test_unrelated_answer_and_search_add_no_private_collection(self):
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as collect:
            values = [await graph._assemble_packet(question, 3, answer_evidence=True) for question in (
                'What is the weather?', 'Where is `release/scope/deploy-dev.py`?',
                'What is the filename of `release scope`?', 'Which tool is named `release scope`?',
            )]
            search = await graph._assemble_packet(QUESTION, 3)
        collect.assert_not_awaited()
        for value in values:
            self.assertNotIn('source_validation_texts', value)
        self.assertNotIn('source_validation_texts', search)


if __name__ == '__main__':
    unittest.main()

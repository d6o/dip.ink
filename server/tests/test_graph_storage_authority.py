"""Actual validator and fake assembly tests. All records are synthetic and inert."""
import json
import os
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('NEO4J_PASSWORD', 'test-password')
os.environ.setdefault('OPENAI_API_KEY', 'test-only')
os.environ.setdefault('WIKI_MCP_BACKGROUND_REINDEX', '0')
import graph
import wiki
from storage_authority_qualifiers import (
    STORAGE_AUTHORITY_BODY_REASON, STORAGE_AUTHORITY_REASON, STORAGE_AUTHORITY_SUFFIX,
    storage_authority_problem,
)
from test_storage_authority_qualifiers import (
    AUTONOMY, COMPLETE, CURRENT, HISTORY, METADATA, OBJECT, OWNER, QUESTION, READ,
    RETENTION, SOURCE, UNKNOWN, SAFE_UNKNOWN, WRONG, answer,
)


def packet(bodies=None, *, current=False, private=None):
    bodies = bodies if bodies is not None else {SOURCE: OWNER}
    value = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': name, 'content': body} for name, body in bodies.items()],
             'source_validation_texts': dict(bodies) if private is None else private}
    if current:
        value['temporal_context'] = graph._temporal_context(value)
    return value


def validate(draft, bodies=None, *, question=QUESTION, sources=None, private=None):
    p = packet(bodies, current=graph._current_state_question(question), private=private)
    return graph._validate_distilled_answer(
        {'answer': draft, 'confidence': 'high', 'sources': sources or [hit['name'] for hit in p['semantic_notes']],
         'escalate': False}, p, question,
    )


class GraphStorageAuthorityTests(unittest.IsolatedAsyncioTestCase):
    def assert_rejected(self, result, reason=STORAGE_AUTHORITY_REASON):
        value, grounded, route = result
        self.assertTrue(grounded)
        self.assertEqual(route, 'storage_authority_mismatch')
        self.assertEqual(value, {'answer': None, 'confidence': 'not_found', 'sources': [],
                                 'escalate': True, 'superseded_note': reason})

    def assert_qualified(self, value):
        self.assertIsNotNone(value['answer'])
        self.assertEqual(value['confidence'], 'medium')
        self.assertTrue(value['escalate'])
        self.assertTrue(value['answer'].endswith(STORAGE_AUTHORITY_SUFFIX))
        self.assertLessEqual(len(value['answer']), 2000)

    async def assemble(self, bodies, draft, *, question=QUESTION, sources=None, private=None):
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        hits = [{'name': name, 'score': .8 - index * .002} for index, name in enumerate(bodies)]
        context = (mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock(return_value=private))
                   if private is not None else nullcontext())
        with context, mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=hits), \
             mock.patch.object(wiki.idx, 'get', side_effect=lambda name: {'body': bodies.get(name, '')}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': draft, 'confidence': 'high', 'sources': sources or list(bodies), 'escalate': False,
             })) as distill, mock.patch.object(graph, '_record_query') as record:
            result = await graph._graph_answer_impl(question)
            assembled = await graph._assemble_packet(question, 8, current_state=graph._current_state_question(question),
                                                     answer_evidence=True)
            search = await graph._assemble_packet(question, 3)
        self.assertIn('source_validation_texts', assembled)
        self.assertNotIn('source_validation_texts', search)
        self.assertNotIn('source_validation_texts', distill.call_args.args[1])
        return result, json.loads(distill.call_args.args[1]), record.call_args.args[0], assembled, search

    def test_exact_wrong_answer_uses_the_stable_route_not_an_earlier_gate(self):
        bodies = {SOURCE: AUTONOMY + '\n\n' + METADATA}
        self.assert_rejected(validate(WRONG, bodies))
        self.assert_rejected(validate('The lead can expand the filesystem without further approval.', bodies))
        self.assertIn('storage_authority_mismatch', graph.GROUNDING_COUNTS)

    def test_source_owned_conditional_selection_and_dated_receipts_survive(self):
        for question in (QUESTION, CURRENT):
            for record in (OWNER, COMPLETE, METADATA, HISTORY, RETENTION, READ):
                with self.subTest(question=question, record=record):
                    value, grounded, route = validate(answer(record), {SOURCE: record}, question=question)
                    self.assert_qualified(value)
                    self.assertTrue(grounded)
                    self.assertEqual(route, 'downgraded')
                    if question == CURRENT:
                        self.assertEqual(value['as_of'], '2026-10-17T04:03:33Z')
                        self.assertIn('Recorded as of ', value['answer'])
                    self.assertIn(record, value['answer'])

    def test_metadata_cannot_supply_an_actor_or_a_capacity_operation(self):
        bodies = {SOURCE: METADATA}
        for draft in ('K3s expanded the filesystem to 160Gi.',
                      'The recorded 160Gi gives the lead permission to expand.', answer(METADATA) + ' Proceed now.'):
            self.assert_rejected(validate(draft, bodies))
        self.assert_qualified(validate(answer(METADATA), bodies)[0])

    def test_every_raw_citation_and_missing_body_use_the_new_body_reason(self):
        bodies = {SOURCE + '-' + str(index): OWNER for index in range(6)}
        for private in ({}, {name: '' for name in bodies},
                        {**bodies, list(bodies)[-1]: ''}):
            self.assert_rejected(validate(answer(OWNER), bodies, private=private), STORAGE_AUTHORITY_BODY_REASON)
        self.assert_rejected(validate(UNKNOWN, {SOURCE: OWNER}, private={}), STORAGE_AUTHORITY_BODY_REASON)

    def test_raw_metadata_sixth_and_late_holds_cannot_hide_behind_caps(self):
        bodies = {SOURCE + '-' + str(index): OWNER for index in range(6)}
        private = dict(bodies)
        private[list(bodies)[-1]] = ('summary: >\n\n  ' + ('Recorded context. ' * 300)
                                     + '\n\n  ' + OBJECT + ' remains blocked by `EEE#15`.')
        self.assert_rejected(validate(answer(OWNER), bodies, private=private))
        for tail in (' It allows deletion.', ' Use it to expand.', ' Approval is unnecessary.'):
            draft = answer(OWNER) + ' ' + ('Recorded background. ' * 110) + tail
            self.assertGreater(len(draft), 2000)
            self.assert_rejected(validate(draft))

    def test_wrong_actor_method_ticket_object_phase_or_protection_recipient_fails(self):
        for old, new in (('`K3s`', '`EEE`'), ('`k3s#29`', '`k3s#5`'),
                         ('`guarded-patch`', '`read-client`'), (OBJECT, '`Other PVC`'),
                         ('completed expansion', 'approved expansion'),
                         ('no cleanup', 'cleanup is allowed'), ('custody release', 'Other custody release')):
            self.assert_rejected(validate(answer(COMPLETE.replace(old, new)), {SOURCE: COMPLETE}))
        self.assert_rejected(validate(answer(OWNER), {SOURCE: READ + '\n\n' + AUTONOMY}))

    def test_question_roles_do_not_use_any_mention_union(self):
        for question in ('Does project `Other` own storage expansion?',
                         'Does ticket `other#29` own storage expansion?',
                         'Who owns expansion of `Other PVC`?',
                         'Who owns storage expansion via `Other-method`?'):
            self.assert_rejected(validate(answer(COMPLETE), {SOURCE: COMPLETE}, question=question))
        right = 'Does project `K3s` own ticket `k3s#29` for expansion of ' + OBJECT + ' via `guarded-patch`?'
        self.assert_qualified(validate(answer(COMPLETE), {SOURCE: COMPLETE}, question=right)[0])

    def test_uncited_relevant_veto_survives_an_unrelated_newer_date(self):
        private = {SOURCE: OWNER, '2026-10-17-060000-other': OBJECT + ' remains blocked by `EEE#15`.',
                   '2099-01-01-000000-unrelated': '`Other PVC` is metadata only.'}
        self.assert_rejected(validate(answer(OWNER), {SOURCE: OWNER}, private=private))
        self.assert_qualified(validate(SAFE_UNKNOWN, {SOURCE: OWNER}, private=private)[0])

    def test_safe_unknown_is_not_global_absence_or_permission(self):
        self.assert_qualified(validate(SAFE_UNKNOWN, {SOURCE: AUTONOMY})[0])
        self.assert_rejected(validate(UNKNOWN, {SOURCE: OWNER}))
        for draft in ('No storage permission exists.', 'No project owns the filesystem.',
                      UNKNOWN + ' Cleanup can proceed.'):
            self.assert_rejected(validate(draft, {SOURCE: AUTONOMY}))

    def test_display_caps_cannot_remove_a_condition_from_a_valid_record(self):
        body = OWNER + '. ' + COMPLETE + '. ' + RETENTION
        draft = answer(body) + (' ' + answer(OWNER).removeprefix('The cited source records: ')) * 2
        self.assertGreater(len(draft), 2000)
        for question in (QUESTION, CURRENT):
            self.assertIsNone(storage_authority_problem(question, draft, {SOURCE: body}))
            self.assert_rejected(validate(draft, {SOURCE: body}, question=question))

    def test_historical_receipt_and_metadata_preserve_current_holds(self):
        for record in (COMPLETE, METADATA, HISTORY):
            bodies = {SOURCE: record + '\n\n' + OWNER + '\n\n'
                      + OBJECT + ' remains blocked by `EEE#15`.'}
            value, _, route = validate(answer(record), bodies)
            self.assert_qualified(value)
            self.assertEqual(route, 'downgraded')
        for contrary in ('Activation remains blocked by `EEE#15`.', 'Owner: `Other`'):
            self.assert_rejected(validate(answer(OWNER), {SOURCE: OWNER + '\n\n' + contrary}))

    async def test_actual_exact_current_and_noncurrent_assembly(self):
        for question in (QUESTION, CURRENT):
            value, sent, event, _, _ = await self.assemble({SOURCE: AUTONOMY + '\n\n' + METADATA}, WRONG,
                                                          question=question)
            self.assertIsNone(value['answer'])
            self.assertEqual(value['superseded_note'], STORAGE_AUTHORITY_REASON)
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
            self.assertEqual(event['temporal_mode'], 'current' if question == CURRENT else 'default')
            self.assertNotIn('source_validation_texts', sent)
            for record in (OWNER, COMPLETE, METADATA, HISTORY, RETENTION, READ):
                value, _, event, _, _ = await self.assemble({SOURCE: record}, answer(record), question=question)
                self.assert_qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_actual_metadata_only_and_complete_draft_controls(self):
        for raw in ('# ' + OWNER, 'description: >\n\n  ' + OWNER, '```text\n' + OWNER + '\n```'):
            value, _, event, _, _ = await self.assemble({SOURCE: raw}, answer(OWNER))
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
            self.assertEqual(value['superseded_note'], STORAGE_AUTHORITY_REASON)
        draft = answer(OWNER) + ' ' + ('Recorded context. ' * 120) + ' It permits cleanup.'
        value, _, event, _, _ = await self.assemble({SOURCE: OWNER}, draft)
        self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
        self.assertIsNone(value['answer'])

    async def test_actual_formatted_concepts_keep_the_private_path_and_positive_control(self):
        for question in (QUESTION.replace('filesystem capacity', '`filesystem capacity`'),
                         QUESTION.replace('cleanup or expansion', '**cleanup** or `expansion`'),
                         'Who owns `storage` `expansion` via `/opt/metadata-client`?'):
            value, _, event, _, _ = await self.assemble({SOURCE: OWNER}, WRONG, question=question)
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
            self.assertIsNone(value['answer'])
            record = COMPLETE if 'via' in question else OWNER
            if 'via' in question:
                record = record.replace('`guarded-patch`', '`/opt/metadata-client`')
            value, _, event, _, _ = await self.assemble({SOURCE: record}, answer(record), question=question)
            self.assert_qualified(value)
            self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_actual_raw_sixth_missing_body_and_uncited_owner_conflicts(self):
        bodies = {SOURCE + '-' + str(index): OWNER for index in range(6)}
        private = dict(bodies)
        private[list(bodies)[-1]] = 'summary: >\n\n  ' + OBJECT + ' remains blocked by `EEE#15`.'
        value, _, event, _, _ = await self.assemble(bodies, answer(OWNER), private=private)
        self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
        self.assertEqual(value['superseded_note'], STORAGE_AUTHORITY_REASON)
        value, _, event, _, _ = await self.assemble(bodies, answer(OWNER), private={**private, list(bodies)[-1]: ''})
        self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
        self.assertEqual(value['superseded_note'], STORAGE_AUTHORITY_BODY_REASON)
        private = {SOURCE: OWNER, 'uncited': OWNER.replace('`K3s`', '`Other`')}
        value, _, event, _, _ = await self.assemble({SOURCE: OWNER}, answer(OWNER), private=private)
        self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
        self.assertIsNone(value['answer'])

    async def test_ten_views_never_disclose_private_full_bodies_or_identities(self):
        sentinel = 'PRIVATE_STORAGE_FULL_BODY_SENTINEL'
        private_identity = 'PRIVATE_STORAGE_IDENTITY'
        raw = (OWNER + '\n\n' + ('Recorded context. ' * 300) + '\n\n' + sentinel + '\n\n'
               + OBJECT + ' remains blocked by `EEE#15`.')
        self.assertGreater(raw.index(sentinel), 4096)
        for question in (QUESTION, CURRENT):
            private = {SOURCE: raw, private_identity: OBJECT + ' remains blocked by `EEE#15`.'}
            value, sent, event, assembled, search = await self.assemble({SOURCE: OWNER}, answer(OWNER),
                                                                       question=question, private=private)
            current_packet = dict(assembled)
            current_packet['temporal_context'] = graph._temporal_context(current_packet)
            views = [value, sent, event, search, search.get('facts'), search.get('entities'),
                     search.get('semantic_notes'), search.get('source_excerpt'),
                     graph._current_evidence_packet(current_packet),
                     graph._subject_evidence_packet(search, ['unrelated'])]
            self.assertEqual(len(views), 10)
            for view in views:
                serialized = json.dumps(view)
                self.assertNotIn(sentinel, serialized)
                self.assertNotIn(private_identity, serialized)
                self.assertNotIn('source_validation_texts', serialized)
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')

    async def test_false_owner_negative_abstains_but_caller_permission_stays_unknown(self):
        for question in (QUESTION, CURRENT):
            self.assert_rejected(validate(UNKNOWN, {SOURCE: OWNER}, question=question))
            value, _, event, _, _ = await self.assemble({SOURCE: OWNER}, UNKNOWN, question=question)
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
            self.assertEqual(value['superseded_note'], STORAGE_AUTHORITY_REASON)
            self.assertIsNone(value['answer'])
            self.assert_qualified(validate(SAFE_UNKNOWN, {SOURCE: OWNER}, question=question)[0])
            value, _, event, _, _ = await self.assemble({SOURCE: OWNER}, SAFE_UNKNOWN, question=question)
            self.assert_qualified(value)
            self.assertEqual(event['grounding_action'], 'downgraded')
        self.assert_rejected(validate(SAFE_UNKNOWN, {SOURCE: OWNER + '. Current caller permission is approved.'}))

    async def test_explicit_role_and_action_pairs_reach_the_actual_storage_stage(self):
        pairs = (
            ('Which project owns `other-filesystem` filesystem capacity for expansion?',
             'Which project owns ' + OBJECT + ' filesystem capacity for expansion?'),
            ('Which project owns the Devbox shared-home filesystem under ticket wrong#9?',
             'Which project owns the Devbox shared-home filesystem under ticket k3s#29?'),
            ('Does project Other own filesystem expansion?', 'Does project K3s own filesystem expansion?'),
            ('Who owns storage volume `Other PVC` for expansion?', 'Who owns storage volume ' + OBJECT + ' for expansion?'),
            ('Who owns filesystem expansion via other-method?', 'Who owns filesystem expansion via guarded-patch?'),
            ('Who owns filesystem expansion under hold Other#15?', 'Who owns filesystem expansion under hold EEE#15?'),
        )
        for wrong, correct in pairs:
            for prefix in ('', 'Currently, '):
                wrong, correct = prefix + wrong, prefix + correct
                self.assert_rejected(validate(answer(COMPLETE), {SOURCE: COMPLETE}, question=wrong))
                value, _, event, _, _ = await self.assemble({SOURCE: COMPLETE}, answer(COMPLETE), question=wrong)
                self.assertIsNone(value['answer'])
                self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
                value, _, route = validate(answer(COMPLETE), {SOURCE: COMPLETE}, question=correct)
                self.assert_qualified(value)
                self.assertEqual(route, 'downgraded')
                value, _, event, _, _ = await self.assemble({SOURCE: COMPLETE}, answer(COMPLETE), question=correct)
                self.assert_qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')
        cleanup = 'Which project owns cleanup of ' + OBJECT + '?'
        for record in (OWNER, COMPLETE):
            self.assert_rejected(validate(answer(record), {SOURCE: record}, question=cleanup))
            value, _, event, _, _ = await self.assemble({SOURCE: record}, answer(record), question=cleanup)
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
        self.assert_qualified(validate(answer(HISTORY), {SOURCE: HISTORY}, question=cleanup)[0])

    async def test_quoted_concepts_collect_private_bodies_and_remove_the_wrong_grant(self):
        questions = ('What storage mutation authority does generic lead autonomy provide?',
                     'What `storage mutation authority` does generic lead autonomy provide?',
                     'What storage-mutation permission applies here?',
                     'What `storage-mutation` `permission` applies here?')
        for base in questions:
            for prefix in ('', 'Currently, '):
                question = prefix + base
                self.assert_rejected(validate(WRONG, {SOURCE: METADATA}, question=question))
                with mock.patch.object(graph, '_source_validation_context',
                                       new=mock.AsyncMock(return_value={SOURCE: METADATA})) as collector:
                    value, _, event, assembled, _ = await self.assemble({SOURCE: METADATA}, WRONG, question=question)
                collector.assert_awaited()
                self.assertEqual(assembled['source_validation_texts'], {SOURCE: METADATA})
                self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
                self.assertEqual(value['superseded_note'], STORAGE_AUTHORITY_REASON)
                self.assertIsNone(value['answer'])

    async def test_raw_quoted_fields_and_action_holds_keep_actual_stage_controls(self):
        for question in (QUESTION, CURRENT):
            for bad in ('`Owner`: `Other`.', '"Owner": "Other".',
                        '"protection limits": "none".', 'Expansion remains blocked.'):
                body = OWNER + '.\n\n' + bad
                self.assert_rejected(validate(answer(OWNER), {SOURCE: body}, question=question))
                value, _, event, _, _ = await self.assemble({SOURCE: body}, answer(OWNER), question=question)
                self.assertIsNone(value['answer'])
                self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
            for good in ('`Owner`: `K3s`.', '"Owner": "K3s".',
                         '`Other PVC` remains protected.\n\n"Owner": "Other".',
                         'Action: cleanup.\n\nCleanup remains blocked.'):
                body = OWNER + '.\n\n' + good
                self.assert_qualified(validate(answer(OWNER), {SOURCE: body}, question=question)[0])
                value, _, event, _, _ = await self.assemble({SOURCE: body}, answer(OWNER), question=question)
                self.assert_qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_ordered_prefix_properties_survive_late_and_sixth_source_caps(self):
        other = METADATA.replace(OBJECT, '`Other PVC`')
        for question in (QUESTION, CURRENT):
            for key in ('Owner', '`Owner`', '"Owner"'):
                body = key + ': `Other`.\n\n' + OWNER + '.\n\n' + other
                self.assert_rejected(validate(answer(OWNER), {SOURCE: body}, question=question))
                value, _, event, _, _ = await self.assemble({SOURCE: body}, answer(OWNER), question=question)
                self.assertIsNone(value['answer'])
                self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
                bodies = {SOURCE + '-' + str(index): OWNER for index in range(6)}
                private = dict(bodies)
                private[list(bodies)[-1]] = ('Recorded context. ' * 300) + '\n\n' + body
                self.assert_rejected(validate(answer(OWNER), bodies, question=question, private=private))
                value, _, event, _, _ = await self.assemble(bodies, answer(OWNER), question=question, private=private)
                self.assertIsNone(value['answer'])
                self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
                for good in (key + ': `K3s`.\n\n' + OWNER + '.\n\n' + other,
                             '`Other PVC` remains protected.\n\n' + body):
                    self.assert_qualified(validate(answer(OWNER), {SOURCE: good}, question=question)[0])
                    value, _, event, _, _ = await self.assemble({SOURCE: good}, answer(OWNER), question=question)
                    self.assert_qualified(value)
                    self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_plural_concept_tokens_keep_the_collector_and_actual_storage_route(self):
        questions = ('What storage permissions apply here?',
                     'What `storage permissions` apply here?',
                     'What PVC expansion permission applies here?',
                     'What `PVC expansion permission` applies here?',
                     'What PVC `permissions` apply here?',
                     'What volume `constraints` apply here?',
                     'What `PV` `mutation` `permissions` apply here?')
        for base in questions:
            for prefix in ('', 'Currently, '):
                question = prefix + base
                self.assert_rejected(validate(WRONG, {SOURCE: METADATA}, question=question))
                with mock.patch.object(graph, '_source_validation_context',
                                       new=mock.AsyncMock(return_value={SOURCE: METADATA})) as collector:
                    value, _, event, assembled, _ = await self.assemble({SOURCE: METADATA}, WRONG, question=question)
                collector.assert_awaited()
                self.assertEqual(assembled['source_validation_texts'], {SOURCE: METADATA})
                self.assertIsNone(value['answer'])
                self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
                value, _, event, _, _ = await self.assemble({SOURCE: METADATA}, answer(METADATA), question=question)
                self.assert_qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_unbound_literal_targets_refuse_instead_of_becoming_generic(self):
        for base in ('What protection constraints apply for `other-filesystem` storage volume?',
                     'What protection constraints apply to storage expansion target `other-filesystem`?',
                     'What storage protection constraints apply toward `other-filesystem`?',
                     'What storage protection constraints apply concerning ' + OBJECT + '?'):
            for prefix in ('', 'Currently, '):
                question = prefix + base
                self.assert_rejected(validate(answer(OWNER), {SOURCE: OWNER}, question=question))
                value, _, event, _, _ = await self.assemble({SOURCE: OWNER}, answer(OWNER), question=question)
                self.assertIsNone(value['answer'])
                self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
        for base in ('What protection constraints apply for ' + OBJECT + ' storage volume?',
                     'What protection constraints apply to storage expansion target ' + OBJECT + '?'):
            for prefix in ('', 'Currently, '):
                for record in (OWNER, METADATA):
                    value, _, event, _, _ = await self.assemble({SOURCE: record}, answer(record), question=prefix + base)
                    self.assert_qualified(value)
                    self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_local_action_restrictions_can_restore_their_own_source_context(self):
        bad = OWNER + '.\nAction: cleanup.\n\nExpansion remains blocked.'
        good = OWNER + '.\nAction: cleanup.\n\nCleanup remains blocked.'
        reset = OWNER + '.\n\n`Other PVC` remains protected.\nAction: cleanup.\n\nExpansion remains blocked.'
        for question in (QUESTION, CURRENT):
            self.assert_rejected(validate(answer(OWNER), {SOURCE: bad}, question=question))
            value, _, event, _, _ = await self.assemble({SOURCE: bad}, answer(OWNER), question=question)
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
            bodies = {SOURCE + '-' + str(index): OWNER for index in range(6)}
            private = dict(bodies)
            private[list(bodies)[-1]] += '\n\n' + ('Recorded context. ' * 300) + '\n\n' + bad
            value, _, event, _, _ = await self.assemble(bodies, answer(OWNER), question=question, private=private)
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
            for body in (good, reset):
                value, _, event, _, _ = await self.assemble({SOURCE: body}, answer(OWNER), question=question)
                self.assert_qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_possible_caller_permission_is_not_cancelled_by_an_unrelated_negative(self):
        bad = ('Current caller permission is approved for ' + OBJECT
               + '; cleanup of `Other PVC` is blocked.')
        for question in (QUESTION, CURRENT):
            self.assert_rejected(validate(SAFE_UNKNOWN, {SOURCE: OWNER + '.\n\n' + bad}, question=question))
            value, _, event, _, _ = await self.assemble({SOURCE: OWNER + '.\n\n' + bad}, SAFE_UNKNOWN, question=question)
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
            bodies = {SOURCE + '-' + str(index): OWNER for index in range(6)}
            private = dict(bodies)
            private[list(bodies)[-1]] += '\n\n' + ('Recorded context. ' * 300) + '\n\n' + bad
            value, _, event, _, _ = await self.assemble(bodies, SAFE_UNKNOWN, question=question, private=private)
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'storage_authority_mismatch')
            value, _, event, _, _ = await self.assemble({SOURCE: OWNER}, SAFE_UNKNOWN, question=question)
            self.assert_qualified(value)
            self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_literal_filename_and_method_questions_collect_no_private_body(self):
        for question in ('What does method `storage-mutation` mean?',
                         'What is the filename `storage-mutation`?',
                         'Which method is named `storage-mutation`?',
                         'What `storage-mutation` method is installed?'):
            g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
            with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
                 mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
                 mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[
                     {'name': SOURCE, 'content': METADATA},
                 ])), mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as collector, \
                 mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                     'answer': 'The record contains a method name.', 'confidence': 'high',
                     'sources': [SOURCE], 'escalate': False,
                 })), mock.patch.object(graph, '_record_query') as event:
                result = await graph._graph_answer_impl(question)
            collector.assert_not_awaited()
            self.assertIsNotNone(result['answer'])
            self.assertIn(event.call_args.args[0]['grounding_action'], ('accepted', 'downgraded'))
            self.assertNotIn(STORAGE_AUTHORITY_SUFFIX, result['answer'])

    async def test_unrelated_governance_collects_no_extra_private_context(self):
        question = 'Can project leads implement or close tickets within their own project?'
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[
                 {'name': SOURCE, 'content': AUTONOMY},
             ])), mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as context, \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': AUTONOMY, 'confidence': 'high', 'sources': [SOURCE], 'escalate': False,
             })), mock.patch.object(graph, '_record_query') as event:
            result = await graph._graph_answer_impl(question)
        context.assert_not_awaited()
        self.assertIsNotNone(result['answer'])
        self.assertEqual(event.call_args.args[0]['grounding_action'], 'accepted')
        self.assertFalse(result['escalate'])


if __name__ == '__main__':
    unittest.main()

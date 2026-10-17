"""Actual validator and assembly tests use only inert synthetic full bodies."""
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
from execution_phase_qualifiers import (
    EXECUTION_PHASE_BODY_REASON, EXECUTION_PHASE_REASON, EXECUTION_PHASE_SUFFIX,
    SAFE_IDENTITY, SAFE_PERMISSION, execution_phase_problem, execution_phase_required,
)
from resource_scope_qualifiers import RESOURCE_SCOPE_REASON, resource_scope_problem, resource_scope_required
from test_execution_phase_qualifiers import (
    ACTUAL, BROKER, CURRENT, DRY, HISTORY, PARTIAL, PATH, PROBE, PROPOSAL, QUESTION,
    RETAINED_QUESTION, SOURCE, WRONG, answer, raw_failures, record,
)

# The owner retained this draft. No source lookup or external replay occurs.
RETAINED_WRONG = (
    'The exact direct Go 1.27 cache-only dry-run method recorded for k3s#30 is a supported Go BUILD-cache cleanup '
    'executed at 16:57:46–16:58:29Z, exiting 0 with empty stdout/stderr and no deadline extension. '
    'It targets exactly 256 direct hexadecimal build-cache buckets under /home/diego/.cache/go-build, preserving '
    'root inode/mode 0700, legacy README, and special inode identities. The dry run uses a restricted environment '
    'with local toolchain, /tmp, HOME, and CWD, bounded by 60s/5s termination limits. It excludes module source, '
    'fuzz, projects, workspaces, backups, tools, trust files, and locks. The dry run proves restoration of current '
    'filesystem headroom (free space increased from ~2GB to ~12.7GB) without changing PVC, replica, storage settings, '
    'trust files, locks, project or Git state, or application deployment. One bounded normal tool/home-write check '
    'from an existing affected caller verifies a fresh owned 0700 directory and 0600 file with <=512B writes and '
    'fsync, confirming preserved executable identity and clean environment. The dry run does not authorize test '
    'replay or reconcile old Git effects, trust-lock history, or migration holds. One-shot authority is held by '
    'root as sole capacity writer; the environment is clean and isolated with preserved inode identities; the '
    'dry run proves cache cleanup and space recovery without side effects or live API requests.'
)
RETAINED_SOURCE = '2026-10-17-170420-devbox-go-build-cache-relief-12gb-headroom'
UNAVAILABLE_SOURCE = '2026-10-17-170453-devbox-enospc-go-cache-relief-verified'


def packet(bodies=None, *, question=QUESTION, private=None):
    bodies = {SOURCE: DRY} if bodies is None else bodies
    value = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': name, 'content': body} for name, body in bodies.items()],
             'source_validation_texts': dict(bodies) if private is None else private}
    if graph._current_state_question(question):
        value['temporal_context'] = graph._temporal_context(value)
    return value


def validate(draft, bodies=None, *, question=QUESTION, sources=None, private=None):
    p = packet(bodies, question=question, private=private)
    return graph._validate_distilled_answer(
        {'answer': draft, 'confidence': 'high', 'escalate': False,
         'sources': sources if sources is not None else [hit['name'] for hit in p['semantic_notes']]}, p, question,
    )


class GraphExecutionPhaseTests(unittest.IsolatedAsyncioTestCase):
    def rejected(self, result, reason=EXECUTION_PHASE_REASON):
        value, grounded, route = result
        self.assertTrue(grounded)
        self.assertEqual(route, 'execution_phase_mismatch')
        self.assertEqual(value, {'answer': None, 'confidence': 'not_found', 'sources': [],
                                 'escalate': True, 'superseded_note': reason})

    def qualified(self, result):
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])
        self.assertTrue(result['answer'].endswith(EXECUTION_PHASE_SUFFIX))
        self.assertLessEqual(len(result['answer']), 2000)

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
             })) as distill, mock.patch.object(graph, '_record_query') as event:
            result = await graph._graph_answer_impl(question)
            assembled = await graph._assemble_packet(question, 8, current_state=graph._current_state_question(question),
                                                     answer_evidence=True)
            search = await graph._assemble_packet(question, 3)
        self.assertIn('source_validation_texts', assembled)
        self.assertNotIn('source_validation_texts', search)
        self.assertNotIn('source_validation_texts', distill.call_args.args[1])
        return result, json.loads(distill.call_args.args[1]), event.call_args.args[0], assembled, search

    def test_stable_route_rejects_original_wrong_phase_claims_with_full_fake_body(self):
        self.assertIn('execution_phase_mismatch', graph.GROUNDING_COUNTS)
        full_fake_body = DRY + '. ' + ACTUAL + '. ' + PROBE + '. ' + BROKER
        for question in (QUESTION, CURRENT, RETAINED_QUESTION, 'Currently, ' + RETAINED_QUESTION):
            for draft in (WRONG, RETAINED_WRONG):
                self.rejected(validate(draft, {SOURCE: full_fake_body}, question=question))

    def test_exact_two_citations_with_unavailable_body_use_distinct_body_reason(self):
        # The retained excerpt is not a complete actual body. This map is synthetic.
        fake = {RETAINED_SOURCE: DRY + '. ' + ACTUAL, UNAVAILABLE_SOURCE: HISTORY}
        for question in (RETAINED_QUESTION, 'Currently, ' + RETAINED_QUESTION):
            self.rejected(validate(RETAINED_WRONG, fake, question=question,
                                   private={RETAINED_SOURCE: fake[RETAINED_SOURCE], UNAVAILABLE_SOURCE: ''}),
                          EXECUTION_PHASE_BODY_REASON)
            self.rejected(validate(RETAINED_WRONG, {RETAINED_SOURCE: fake[RETAINED_SOURCE]}, question=question))

    def test_complete_partial_and_bounded_unknown_answers_stay_qualified(self):
        for question in (QUESTION, CURRENT):
            for body in (DRY, ACTUAL, PROBE, PATH, BROKER, PROPOSAL, PARTIAL, record(actors=False)):
                value, grounded, route = validate(answer(body), {SOURCE: body}, question=question)
                self.qualified(value)
                self.assertTrue(grounded)
                self.assertEqual(route, 'downgraded')
                self.assertIn(body, value['answer'])
                if question == CURRENT:
                    self.assertEqual(value['as_of'], '2026-10-17T09:00:00Z')
                    self.assertIn('On 2026-10-16T', value['answer'])
            for draft in (SAFE_PERMISSION, SAFE_IDENTITY):
                self.qualified(validate(draft, {SOURCE: HISTORY}, question=question)[0])
        value, _, route = validate(answer(DRY, ACTUAL), {SOURCE: DRY + '. ' + ACTUAL})
        self.qualified(value)
        self.assertEqual(route, 'downgraded')

    def test_flags_deadlines_effects_proof_and_identity_cannot_transfer(self):
        for old, new in (('10s', '60s'), ('2s', '5s'), ('20s', '80s'), ('toy clean -n -cache', 'toy clean -cache'),
                         ('no deletion or headroom effect', 'dated free-block relief'),
                         ('"direct"', '"brokered"'), ('"task-A"', '"task-B"'), ('"app-A"', '"app-B"'),
                         ('"cache-A"', '"cache-B"'), ('"caller-A"', '"caller-B"'), ('"candidate-A"', '"candidate-B"'),
                         ('/synthetic/Tool', '/synthetic/tool'), ('"1.27"', '"1.27.0"'),
                         ('"dated-path-record"', '"fresh-lease"'), ('"spent"', '"one-shot"'),
                         ('08:00:00', '08:03:00'), ('"recorded conditions only"', '"isolated"')):
            self.rejected(validate(answer(DRY.replace(old, new)), {SOURCE: DRY + '. ' + ACTUAL}))
        for body in (PROBE, PATH, BROKER, PROPOSAL):
            for tail in (' It proves executable identity.', ' It proves a clean environment.',
                         ' It permits the new resource.', ' No earlier broker side effects occurred.'):
                self.rejected(validate(answer(body) + tail, {SOURCE: body}))

    def test_same_scope_body_conflicts_use_new_stage_not_a_latest_source_union(self):
        changed = DRY.replace('TERM "10s"', 'TERM "60s"')
        for private in ({SOURCE: DRY + '. ' + changed}, {SOURCE: DRY, 'uncited': changed}):
            self.rejected(validate(answer(DRY), private=private))
        self.qualified(validate(answer(DRY), {SOURCE: DRY + '. ' + ACTUAL})[0])

    def test_split_sources_and_raw_metadata_cannot_supply_positive_support(self):
        for wrapper in ('# {}', 'title: {}', 'summary: >\n\n  {}', '```text\n{}\n```', '> {}'):
            self.rejected(validate(answer(DRY), {SOURCE: wrapper.format(DRY)}))
        self.rejected(validate(answer(DRY), {SOURCE: DRY.split(': TERM')[0], 'newest': ACTUAL}))
        self.rejected(validate(answer(DRY), {SOURCE: HISTORY}, private={SOURCE: HISTORY, 'uncited': DRY}))
        self.rejected(validate(answer(DRY), {SOURCE: HISTORY}, private={DRY: HISTORY, SOURCE: HISTORY}))

    def test_all_raw_citations_and_scalar_sources_require_complete_bodies(self):
        bodies = {SOURCE + '-' + str(index): DRY for index in range(6)}
        for private in ({}, {**bodies, list(bodies)[-1]: ''}):
            self.rejected(validate(answer(DRY), bodies, private=private), EXECUTION_PHASE_BODY_REASON)
        self.rejected(validate(SAFE_PERMISSION, private={}), EXECUTION_PHASE_BODY_REASON)
        self.rejected(validate(answer(DRY), sources=[SOURCE, 'invented']), EXECUTION_PHASE_BODY_REASON)
        # The validator accepts a scalar citation, but still requires its body.
        self.qualified(validate(answer(DRY), sources=SOURCE)[0])
        self.rejected(validate(answer(DRY), sources=SOURCE, private={}), EXECUTION_PHASE_BODY_REASON)

    def test_full_draft_and_late_sixth_body_precede_all_display_caps(self):
        bodies = {SOURCE + '-' + str(index): DRY for index in range(6)}
        private = dict(bodies)
        private[list(bodies)[-1]] += '.\n\n' + ('Recorded context. ' * 300) + '\n\nmethod: direct\nTERM: 60s'
        self.assertGreater(private[list(bodies)[-1]].index('TERM: 60s'), 4096)
        for sources in (list(bodies), [list(bodies)[0]]):
            self.rejected(validate(answer(DRY), bodies, private=private, sources=sources))
        for tail in (' It permits a new cleanup.', ' It proves executable identity.'):
            draft = answer(DRY) + ' ' + ('Recorded context. ' * 130) + tail
            self.assertGreater(len(draft), 2000)
            self.rejected(validate(draft))
        # A valid long draft still refuses if display limits would remove a phase.
        self.rejected(validate(answer(DRY, DRY, DRY, DRY)))

    def test_explicit_roles_and_literal_identity_bytes_use_actual_stage(self):
        for role, value in (('method', 'brokered'), ('resource', 'cache-B'), ('task', 'task-B'),
                            ('app', 'app-B'), ('caller', 'caller-B'), ('candidate', 'candidate-B'),
                            ('source identity', 'new-record'), ('executable', '/synthetic/tool'), ('scope', 'unbound')):
            for literal in (value, '"' + value + '"', '`' + value + '`'):
                self.rejected(validate(answer(DRY), question=QUESTION[:-1] + ' for ' + role + ' ' + literal + '?'))
        self.rejected(validate(answer(DRY), question=QUESTION[:-1] + ' for executable "/synthetic/Tool "?'))
        self.qualified(validate(answer(DRY), question=QUESTION[:-1] + ' for executable "/synthetic/Tool"?')[0])

    def test_raw_property_order_and_other_scope_controls_use_actual_stage(self):
        for raw in raw_failures():
            for wrapper in ('{}', '# {}', 'summary: >\n  {}', '```text\n{}\n```'):
                wrapped = wrapper.format(raw.replace('\n', '\n  ') if wrapper.startswith('summary') else raw)
                self.rejected(validate(answer(DRY), {SOURCE: DRY + '.\n\n' + wrapped}))
                self.qualified(validate(answer(DRY), {SOURCE: DRY + '.\n\n' + wrapped.replace('direct', 'brokered')})[0])
        for role, value in (('task', 'task-B'), ('resource', 'cache-B'), ('start', '2026-10-15T08:00:00Z')):
            raw = 'method: direct\n' + role + ': ' + value + '\nobserved effect: unknown'
            self.qualified(validate(answer(DRY), {SOURCE: DRY + '.\n\n' + raw})[0])

    def test_explicit_alias_roles_and_unknown_roles_reach_actual_stage(self):
        for role, value in (('path', '/synthetic/Tool'), ('sourceIdentity', 'dated-path-record'), ('target', 'cache-A')):
            self.qualified(validate(answer(DRY), question=QUESTION[:-1] + ' for ' + role + ' "' + value + '"?')[0])
            self.rejected(validate(answer(DRY), question=QUESTION[:-1] + ' for ' + role + ' "different"?'))
        for role in ('root', 'object', 'operation', 'scope', 'who'):
            self.rejected(validate(answer(DRY), question=QUESTION[:-1] + ' for ' + role + ' "different"?'))

    def test_operation_date_does_not_come_from_the_source_capture_date(self):
        self.qualified(validate(answer(DRY), question=QUESTION + ' On 2026-10-16?')[0])
        self.rejected(validate(answer(DRY), question=QUESTION + ' On 2026-10-17?'))
        self.qualified(validate(answer(DRY), question=QUESTION + ' At 2026-10-16T08:00:00Z?')[0])
        self.rejected(validate(answer(DRY), question=QUESTION + ' At 2026-10-16T08:03:00Z?'))

    def test_old_resource_guard_is_not_skipped_for_backtick_tuple_overlap(self):
        q = QUESTION[:-1] + ' for method `direct`, resource `cache-A`, task `task-A`?'
        self.assertTrue(execution_phase_required(q))
        self.assertTrue(resource_scope_required(q))
        self.assertIsNone(execution_phase_problem(q, answer(DRY), {SOURCE: DRY}))
        self.assertEqual(resource_scope_problem(q, answer(DRY), {SOURCE: DRY}), RESOURCE_SCOPE_REASON)
        result, grounded, route = validate(answer(DRY), question=q)
        self.assertTrue(grounded)
        self.assertEqual(route, 'qualifier_mismatch')
        self.assertEqual(result['superseded_note'], RESOURCE_SCOPE_REASON)
        # These controls reach all guards without a resource exemption.
        for identity in ('direct', '"direct"'):
            self.qualified(validate(answer(DRY), question=QUESTION[:-1] + ' for method ' + identity + '?')[0])
        self.rejected(validate(WRONG, question=q))

    async def test_actual_current_and_noncurrent_private_collection_and_validator(self):
        for question in (QUESTION, CURRENT):
            for draft in (WRONG, RETAINED_WRONG):
                value, _, event, assembled, _ = await self.assemble({SOURCE: DRY + '. ' + ACTUAL + '. ' + PROBE},
                                                                 draft, question=question)
                self.assertIsNone(value['answer'])
                self.assertEqual(value['superseded_note'], EXECUTION_PHASE_REASON)
                self.assertEqual(event['grounding_action'], 'execution_phase_mismatch')
                self.assertEqual(assembled['source_validation_texts'][SOURCE], DRY + '. ' + ACTUAL + '. ' + PROBE)
                self.assertEqual(event['temporal_mode'], 'current' if question == CURRENT else 'default')
            for body in (DRY, ACTUAL, PROBE, PARTIAL):
                value, _, event, _, _ = await self.assemble({SOURCE: body}, answer(body), question=question)
                self.qualified(value)
                self.assertEqual(event['grounding_action'], 'downgraded')

    async def test_retained_two_citations_and_fake_single_source_are_not_vacuous(self):
        for question in (RETAINED_QUESTION, 'Currently, ' + RETAINED_QUESTION):
            fake = {RETAINED_SOURCE: DRY + '. ' + ACTUAL + '. ' + PROBE, UNAVAILABLE_SOURCE: HISTORY}
            value, _, event, _, _ = await self.assemble(fake, RETAINED_WRONG, question=question,
                                                       private={RETAINED_SOURCE: fake[RETAINED_SOURCE], UNAVAILABLE_SOURCE: ''})
            self.assertEqual(value['superseded_note'], EXECUTION_PHASE_BODY_REASON)
            self.assertEqual(event['grounding_action'], 'execution_phase_mismatch')
            value, _, event, _, _ = await self.assemble({RETAINED_SOURCE: fake[RETAINED_SOURCE]},
                                                       RETAINED_WRONG, question=question)
            self.assertIsNone(value['answer'])
            self.assertEqual(value['superseded_note'], EXECUTION_PHASE_REASON)
            self.assertEqual(event['grounding_action'], 'execution_phase_mismatch')

    async def test_actual_sixth_late_and_wrapped_raw_values_veto_only(self):
        for wrapper in ('# {}', 'summary: >\n\n  {}', '```text\n{}\n```'):
            value, _, event, _, _ = await self.assemble({SOURCE: wrapper.format(DRY)}, answer(DRY))
            self.assertIsNone(value['answer'])
            self.assertEqual(event['grounding_action'], 'execution_phase_mismatch')
        bodies = {SOURCE + '-' + str(index): DRY for index in range(6)}
        private = dict(bodies)
        private[list(bodies)[-1]] += '.\n\n' + ('Recorded context. ' * 300) + '\n\nsummary: >\n  method: direct\n  proof domain: unknown'
        for sources in (list(bodies), [list(bodies)[0]]):
            value, _, event, _, _ = await self.assemble(bodies, answer(DRY), sources=sources, private=private)
            self.assertIsNone(value['answer'])
            self.assertEqual(value['superseded_note'], EXECUTION_PHASE_REASON)
            self.assertEqual(event['grounding_action'], 'execution_phase_mismatch')
        value, _, event, _, _ = await self.assemble(bodies, answer(DRY), private={**private, list(bodies)[-1]: ''})
        self.assertEqual(value['superseded_note'], EXECUTION_PHASE_BODY_REASON)
        self.assertEqual(event['grounding_action'], 'execution_phase_mismatch')

    async def test_ten_public_views_exclude_full_private_bodies_and_private_keys(self):
        sentinel = 'PRIVATE_EXECUTION_BODY_SENTINEL'
        identity = 'PRIVATE_EXECUTION_SOURCE_ID'
        raw = DRY + '.\n\n' + ('Recorded context. ' * 300) + '\n\n' + sentinel + '\n\nmethod: direct\nproof domain: unknown'
        self.assertGreater(raw.index(sentinel), 4096)
        for question in (QUESTION, CURRENT):
            value, sent, event, assembled, search = await self.assemble(
                {SOURCE: DRY}, answer(DRY), question=question,
                private={SOURCE: raw, identity: 'method: direct\nobserved effect: unknown'})
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
            self.assertEqual(event['grounding_action'], 'execution_phase_mismatch')

    async def test_metadata_only_and_adjacent_queries_do_not_collect_private_bodies(self):
        queries = ('What is the filename of the dry-run record?', 'What is the definition of an execution phase?',
                   'Which version is the execution-phase note?', 'What is path "/synthetic/execution-phase"?',
                   'What does task "execution-phase" mean?', 'How do I format a table?')
        backend = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=backend)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as collect:
            for question in queries:
                self.assertFalse(execution_phase_required(question))
                p = await graph._assemble_packet(question, 3, answer_evidence=True)
                self.assertNotIn('source_validation_texts', p)
            collect.assert_not_awaited()

    async def test_formatted_concepts_and_metadata_appendix_reach_actual_new_stage(self):
        for question in ('What does `dry-run` version supports?',
                         'What does **execution phase** version provides?',
                         QUESTION + '; what is the filename?'):
            value, _, event, _, _ = await self.assemble({SOURCE: DRY}, WRONG, question=question)
            self.assertIsNone(value['answer'])
            self.assertEqual(value['superseded_note'], EXECUTION_PHASE_REASON)
            self.assertEqual(event['grounding_action'], 'execution_phase_mismatch')

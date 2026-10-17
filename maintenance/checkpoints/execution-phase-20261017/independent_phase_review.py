"""Independent inert review of exact f1c94b9. No external source access."""
import atexit
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import graph
import wiki
import execution_phase_qualifiers as phase
from test_execution_phase_qualifiers import (
    QUESTION, CURRENT, SOURCE, DRY, ACTUAL, PROBE, PATH, BROKER, PROPOSAL,
    PARTIAL, HISTORY, WRONG, RETAINED_QUESTION, record, answer,
)
from test_graph_execution_phases import RETAINED_WRONG, RETAINED_SOURCE, UNAVAILABLE_SOURCE

REPORT = []


def save_report():
    with open('/tmp/dipink-checkpoint-phase-review-observations.json', 'w') as stream:
        json.dump(REPORT, stream, indent=2)


atexit.register(save_report)


def packet(bodies, question, private=None):
    packet = {'facts': [], 'entities': [], 'source_excerpt': None,
              'semantic_notes': [{'name': key, 'content': body} for key, body in bodies.items()],
              'source_validation_texts': bodies if private is None else private}
    if graph._current_state_question(question):
        packet['temporal_context'] = graph._temporal_context(packet)
    return packet


def validate(question, draft, bodies, private=None, sources=None):
    return graph._validate_distilled_answer(
        {'answer': draft, 'confidence': 'high', 'escalate': False,
         'sources': list(bodies) if sources is None else sources},
        packet(bodies, question, private), question,
    )


class IndependentPhaseReview(unittest.IsolatedAsyncioTestCase):
    def check(self, label, question, draft, bodies, expected=None, private=None, sources=None):
        private = bodies if private is None else private
        cited = list(bodies) if sources is None else sources
        cited = [cited] if isinstance(cited, str) else cited
        helper = phase.execution_phase_problem(question, draft, {key: private.get(key, '') for key in cited},
                                               retrieved_texts=private)
        result, grounded, route = validate(question, draft, bodies, private, sources)
        REPORT.append({'label': label, 'stage': 'helper+validator', 'question': question,
                       'selected': phase.execution_phase_required(question), 'helper': helper,
                       'route': route, 'answer': result['answer'], 'confidence': result['confidence'],
                       'escalate': result['escalate'], 'expected': expected})
        if expected:
            self.assertEqual((helper, route, result), (expected, 'execution_phase_mismatch', {
                'answer': None, 'confidence': 'not_found', 'sources': [], 'escalate': True,
                'superseded_note': expected}), label)
        else:
            self.assertIsNone(helper, label)
            self.assertTrue(grounded, label)
            self.assertEqual(route, 'downgraded', label)
            self.assertIsNotNone(result['answer'], label)
            self.assertEqual(result['confidence'], 'medium', label)
            self.assertTrue(result['escalate'], label)
            self.assertTrue(result['answer'].endswith(phase.EXECUTION_PHASE_SUFFIX), label)
        return result

    async def assemble(self, question, draft, bodies, private=None, sources=None):
        backend = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        hits = [{'name': key, 'score': .9 - index * .001} for index, key in enumerate(bodies)]
        lookups = []

        def get(name):
            lookups.append(name)
            return {'body': bodies.get(name, '')}

        patch = (mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock(return_value=private))
                 if private is not None else mock.patch.object(wiki.idx, 'get', side_effect=get))
        with patch, mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=backend)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=hits), \
             mock.patch.object(wiki.idx, 'get', side_effect=get) if private is not None else mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': draft, 'confidence': 'high', 'escalate': False,
                 'sources': list(bodies) if sources is None else sources})) as distill, \
             mock.patch.object(graph, '_record_query') as event:
            result = await graph._graph_answer_impl(question)
            assembled = await graph._assemble_packet(question, 8, current_state=graph._current_state_question(question), answer_evidence=True)
            search = await graph._assemble_packet(question, 3)
        sent = json.loads(distill.call_args.args[1])
        return result, event.call_args.args[0], assembled, search, sent, lookups

    async def check_assembly(self, label, question, draft, bodies, expected=None, private=None, sources=None):
        result, event, assembled, search, sent, lookups = await self.assemble(question, draft, bodies, private, sources)
        REPORT.append({'label': label, 'stage': 'assembly', 'question': question,
                       'selected': phase.execution_phase_required(question), 'route': event['grounding_action'],
                       'answer': result['answer'], 'confidence': result['confidence'], 'escalate': result['escalate'],
                       'collected': 'source_validation_texts' in assembled, 'lookups': lookups, 'expected': expected})
        self.assertNotIn('source_validation_texts', search)
        self.assertNotIn('source_validation_texts', sent)
        self.assertEqual(event['temporal_mode'], 'current' if graph._current_state_question(question) else 'default')
        if expected:
            self.assertEqual((result.get('superseded_note'), event['grounding_action'], result['answer']),
                             (expected, 'execution_phase_mismatch', None), label)
        else:
            self.assertEqual(event['grounding_action'], 'downgraded', label)
            self.assertIsNotNone(result['answer'], label)
            self.assertEqual(result['confidence'], 'medium', label)
            self.assertTrue(result['escalate'], label)
            self.assertTrue(result['answer'].endswith(phase.EXECUTION_PHASE_SUFFIX), label)
        self.assertIn('source_validation_texts', assembled, label)
        return result, event, assembled, search, sent

    def test_independent_all_roles_and_all_facets(self):
        fields = {'phase': ('dry-run', 'actual-cleanup'), 'method': ('direct', 'brokered'),
                  'action': ('cache-clean', 'write-check'), 'command': ('toy clean -n -cache', 'toy clean -cache'),
                  'executable': ('/synthetic/Tool', '/synthetic/Other'), 'version': ('1.27', '1.28'),
                  'source identity': ('dated-path-record', 'other-record'), 'resource': ('cache-A', 'cache-B'),
                  'task': ('task-A', 'task-B'), 'app': ('app-A', 'app-B'), 'caller': ('caller-A', 'caller-B'),
                  'candidate': ('candidate-A', 'candidate-B'), 'TERM': ('10s', '60s'), 'kill': ('2s', '5s'),
                  'caller deadline': ('20s', '80s'), 'environment': ('recorded conditions only', 'unknown'),
                  'selection': ('spent', 'unselected'), 'observed effect': ('no deletion or headroom effect', 'unknown'),
                  'proof domain': ('address selection only', 'unknown'),
                  'start': ('2026-10-16T08:00:00Z', '2026-10-16T08:03:00Z'),
                  'end': ('2026-10-16T08:00:01Z', '2026-10-16T08:03:43Z'),
                  'operation date': ('2026-10-16', '2026-10-15')}
        for question in (QUESTION, CURRENT):
            for role, (same, different) in fields.items():
                with self.subTest(role=role, question=question):
                    same_q = question + ' For ' + role + ' "' + same + '"?'
                    other_q = question + ' For ' + role + ' "' + different + '"?'
                    self.check('same-role-' + role, same_q, answer(DRY), {SOURCE: DRY})
                    self.check('different-role-' + role, other_q, answer(DRY), {SOURCE: DRY}, phase.EXECUTION_PHASE_REASON)

    async def test_explicit_quoted_long_roles_have_positive_source_assembly(self):
        for role, value in (('caller deadline', '20s'), ('operation date', '2026-10-16')):
            for base in (QUESTION, CURRENT):
                question = base + ' For ' + role + ' "' + value + '"?'
                for stage in ('helper', 'assembly'):
                    with self.subTest(role=role, question=question, stage=stage):
                        if stage == 'helper':
                            self.check('genuine-quoted-long-role', question, answer(DRY), {SOURCE: DRY})
                        else:
                            await self.check_assembly('genuine-quoted-long-role', question, answer(DRY), {SOURCE: DRY})

    async def test_natural_version_and_direct_method_query_roles(self):
        pairs = (('What does the Go 1.27 dry-run prove?', DRY.replace('version "1.27"', 'version "1.28"')),
                 ('What does the direct dry-run prove?', DRY.replace('method "direct"', 'method "brokered"')))
        for base, other in pairs:
            for question in (base, 'Currently, ' + base):
                for stage in ('helper', 'assembly'):
                    with self.subTest(question=question, stage=stage):
                        if stage == 'helper':
                            self.check('natural-question-role-control', question, answer(DRY), {SOURCE: DRY})
                            self.check('natural-question-role-mismatch', question, answer(other), {SOURCE: other}, phase.EXECUTION_PHASE_REASON)
                        else:
                            await self.check_assembly('natural-question-role-control', question, answer(DRY), {SOURCE: DRY})
                            await self.check_assembly('natural-question-role-mismatch', question, answer(other), {SOURCE: other}, phase.EXECUTION_PHASE_REASON)

    async def test_reordered_version_facets_remain_operative(self):
        for base in ('What version limits apply to the dry-run?', 'Which version deadlines constrain the dry-run?'):
            for question in (base, 'Currently, ' + base):
                for stage in ('helper', 'assembly'):
                    with self.subTest(question=question, stage=stage):
                        if stage == 'helper':
                            self.check('reordered-version-facet', question, WRONG, {SOURCE: DRY}, phase.EXECUTION_PHASE_REASON)
                        else:
                            await self.check_assembly('reordered-version-facet', question, WRONG, {SOURCE: DRY}, phase.EXECUTION_PHASE_REASON)

    async def test_supported_version_facet_is_not_a_literal_constraint(self):
        for base in ('What are the dry-run version limits?', 'What are the dry-run version deadlines?'):
            for question in (base, 'Currently, ' + base):
                for stage in ('helper', 'assembly'):
                    with self.subTest(question=question, stage=stage):
                        if stage == 'helper':
                            self.check('genuine-version-facet', question, answer(DRY), {SOURCE: DRY})
                        else:
                            await self.check_assembly('genuine-version-facet', question, answer(DRY), {SOURCE: DRY})

    async def test_independent_positive_history_and_contrast(self):
        for question in (QUESTION, CURRENT):
            for body in (DRY, ACTUAL, PROBE, PATH, BROKER, PROPOSAL, PARTIAL):
                with self.subTest(phase=body.split('records')[0], question=question):
                    self.check('dated-positive', question, answer(body), {SOURCE: body})
                    await self.check_assembly('dated-positive', question, answer(body), {SOURCE: body})
            self.check('dated-phase-contrast', question, answer(DRY, ACTUAL), {SOURCE: DRY + '. ' + ACTUAL})
            await self.check_assembly('dated-phase-contrast', question, answer(DRY, ACTUAL), {SOURCE: DRY + '. ' + ACTUAL})
            for safe in (phase.SAFE_PERMISSION, phase.SAFE_IDENTITY):
                self.check('bounded-unknown-facet', question, safe, {SOURCE: HISTORY})
                await self.check_assembly('bounded-unknown-facet', question, safe, {SOURCE: HISTORY})

    async def test_independent_retained_wrong_draft_and_exact_missing_body(self):
        fake = DRY + '. ' + ACTUAL + '. ' + PROBE + '. ' + BROKER
        for question in (RETAINED_QUESTION, 'Currently, ' + RETAINED_QUESTION):
            with self.subTest(question=question):
                for draft in (WRONG, RETAINED_WRONG):
                    self.check('one-synthetic-full-body-wrong', question, draft, {RETAINED_SOURCE: fake}, phase.EXECUTION_PHASE_REASON)
                    await self.check_assembly('one-synthetic-full-body-wrong', question, draft,
                                              {RETAINED_SOURCE: fake}, phase.EXECUTION_PHASE_REASON)
                bodies = {RETAINED_SOURCE: fake, UNAVAILABLE_SOURCE: HISTORY}
                private = {RETAINED_SOURCE: fake, UNAVAILABLE_SOURCE: ''}
                self.check('two-cites-missing-170453', question, RETAINED_WRONG, bodies,
                           phase.EXECUTION_PHASE_BODY_REASON, private)
                await self.check_assembly('two-cites-missing-170453', question, RETAINED_WRONG, bodies,
                                          phase.EXECUTION_PHASE_BODY_REASON, private)

    async def test_selector_formatted_version_limits_and_flag_intent(self):
        questions = ('What does the `Go 1.27 dry-run` prove?',
                     'What executable identity does the dry-run version record?',
                     'What are the dry-run version limits?',
                     'What does Go 1.27 clean -n -cache prove?')
        for base in questions:
            for question in (base, 'Currently, ' + base):
                for stage in ('helper', 'assembly'):
                    with self.subTest(question=question, stage=stage):
                        if stage == 'helper':
                            self.check('selector-gap', question, WRONG, {SOURCE: DRY}, phase.EXECUTION_PHASE_REASON)
                        else:
                            await self.check_assembly('selector-gap', question, WRONG, {SOURCE: DRY}, phase.EXECUTION_PHASE_REASON)

    async def test_operative_proof_claim_inside_environment_field(self):
        cases = ((PROBE, 'The write/fsync probe confirms executable identity and a clean environment'),
                 (BROKER, 'The broker staging proves no side effects'))
        for body, promoted in cases:
            corrupt = body.replace('environment "recorded conditions only"', 'environment "' + promoted + '"')
            for question in (QUESTION, CURRENT):
                for stage in ('helper', 'assembly'):
                    with self.subTest(phase=body.split('records')[0], question=question, stage=stage):
                        if stage == 'helper':
                            self.check('promoted-environment-field', question, answer(corrupt), {SOURCE: corrupt},
                                       phase.EXECUTION_PHASE_REASON)
                        else:
                            await self.check_assembly('promoted-environment-field', question, answer(corrupt),
                                                      {SOURCE: corrupt}, phase.EXECUTION_PHASE_REASON)

    async def test_unknown_quoted_scope_cannot_erase_source_anchor(self):
        fields = (('method', 'unknown method'), ('phase', 'unverified phase'),
                  ('executable', 'unavailable executable'), ('version', 'unknown version'),
                  ('command', 'unknown command'), ('resource', 'unknown resource'))
        for role, value in fields:
            body = DRY + '.\n\n' + role + ': "' + value + '"\nproof domain: unknown'
            for question in (QUESTION, CURRENT):
                for stage in ('helper', 'assembly'):
                    with self.subTest(role=role, question=question, stage=stage):
                        if stage == 'helper':
                            self.check('unknown-scope-anchor', question, answer(DRY), {SOURCE: body}, phase.EXECUTION_PHASE_REASON)
                        else:
                            await self.check_assembly('unknown-scope-anchor', question, answer(DRY), {SOURCE: body},
                                                      phase.EXECUTION_PHASE_REASON)

    async def test_raw_known_scope_boundaries_and_other_scope_controls(self):
        wrappers = ('{}', '# {}', 'summary: >\n  {}', '```text\n{}\n```')
        same = ('method: direct\nproof domain: unknown',
                "'method' = 'direct'\n'proof domain' = 'unknown'",
                'method: >\n  direct\nTERM: 60s',
                'method: |\n  direct\nobserved effect: unknown',
                'method: direct\nThe cleanup failed.')
        for raw in same:
            for wrapper in wrappers:
                wrapped = wrapper.format(raw.replace('\n', '\n  ') if wrapper.startswith('summary') else raw)
                for body in (DRY + '.\n\n' + wrapped, wrapped + '\n\n' + DRY):
                    with self.subTest(raw=wrapped, order=body.startswith('On')):
                        self.check('raw-same-scope', QUESTION, answer(DRY), {SOURCE: body}, phase.EXECUTION_PHASE_REASON)
                other = wrapped.replace('direct', 'brokered')
                with self.subTest(raw=other):
                    self.check('raw-other-method', QUESTION, answer(DRY), {SOURCE: DRY + '.\n\n' + other})
        for scope in ('method: brokered', 'method: direct\nphase: actual-cleanup', 'method: direct\nresource: cache-B'):
            body = DRY + '.\n\n' + scope + '\nproof domain: unknown'
            for question in (QUESTION, CURRENT):
                self.check('explicit-other-scope', question, answer(DRY), {SOURCE: body})
                await self.check_assembly('explicit-other-scope', question, answer(DRY), {SOURCE: body})

    async def test_full_late_citation_and_ten_private_views(self):
        sentinel = 'INDEPENDENT_PRIVATE_EXECUTION_BODY_SENTINEL'
        identity = 'INDEPENDENT_PRIVATE_EXECUTION_SOURCE_KEY'
        bodies = {SOURCE + '-' + str(index): DRY for index in range(6)}
        private = dict(bodies)
        private[list(bodies)[-1]] += '.\n\n' + ('Recorded context. ' * 300) + '\n\n' + sentinel + '\nmethod: direct\nproof domain: unknown'
        private[identity] = 'method: direct\nobserved effect: unknown'
        self.assertGreater(private[list(bodies)[-1]].index(sentinel), 4096)
        for question in (QUESTION, CURRENT):
            for sources in (list(bodies), [list(bodies)[0]]):
                with self.subTest(question=question, sources=len(sources)):
                    self.check('late-sixth-private', question, answer(DRY), bodies, phase.EXECUTION_PHASE_REASON, private, sources)
                    result, event, assembled, search, sent = await self.check_assembly(
                        'late-sixth-private', question, answer(DRY), bodies, phase.EXECUTION_PHASE_REASON, private, sources)
                    current = dict(assembled)
                    current['temporal_context'] = graph._temporal_context(current)
                    views = (result, sent, event, search, search.get('facts'), search.get('entities'),
                             search.get('semantic_notes'), search.get('source_excerpt'),
                             graph._current_evidence_packet(current), graph._subject_evidence_packet(search, ['unrelated']))
                    self.assertEqual(len(views), 10)
                    for view in views:
                        serialized = json.dumps(view)
                        self.assertNotIn(sentinel, serialized)
                        self.assertNotIn(identity, serialized)
                        self.assertNotIn('source_validation_texts', serialized)
            missing = {**private, list(bodies)[-1]: ''}
            self.check('missing-sixth-body', question, answer(DRY), bodies, phase.EXECUTION_PHASE_BODY_REASON, missing)
            await self.check_assembly('missing-sixth-body', question, answer(DRY), bodies, phase.EXECUTION_PHASE_BODY_REASON, missing)
            long_draft = answer(DRY) + ' ' + ('Recorded context. ' * 130) + ' It proves executable identity.'
            self.assertGreater(len(long_draft), 2000)
            self.check('full-uncapped-draft', question, long_draft, {SOURCE: DRY}, phase.EXECUTION_PHASE_REASON)

    async def test_metadata_only_does_not_collect_and_old_guard_remains(self):
        questions = ('What is the dry-run version?', 'What version of the dry-run method is recorded?', 'What is the filename of the dry-run record?',
                     'What does method "dry-run" mean?', 'What is path "/synthetic/execution-phase"?')
        backend = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=backend)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as collector:
            for question in questions:
                self.assertFalse(phase.execution_phase_required(question))
                value = await graph._assemble_packet(question, 3, answer_evidence=True)
                self.assertNotIn('source_validation_texts', value)
            collector.assert_not_awaited()
        from resource_scope_qualifiers import RESOURCE_SCOPE_REASON
        question = QUESTION + ' For method `direct`, resource `cache-A`, task `task-A`?'
        self.assertIsNone(phase.execution_phase_problem(question, answer(DRY), {SOURCE: DRY}))
        result, grounded, route = validate(question, answer(DRY), {SOURCE: DRY})
        self.assertTrue(grounded)
        self.assertEqual(route, 'qualifier_mismatch')
        self.assertEqual(result['superseded_note'], RESOURCE_SCOPE_REASON)

    async def test_literal_paths_spent_and_source_only_proof_limits(self):
        for path in ('/synthetic/Tool.Name', '/synthetic/Tool*Name', "/synthetic/Tool's.Name", '/synthetic/Tool Name'):
            body = DRY.replace('/synthetic/Tool', path)
            question = QUESTION + ' For executable "' + path + '"?'
            with self.subTest(path=path):
                self.check('exact-literal-path', question, answer(body), {SOURCE: body})
                self.check('changed-literal-path', question.replace(path, path.lower()), answer(body), {SOURCE: body}, phase.EXECUTION_PHASE_REASON)
        for old, new in (('selection "spent"', 'selection "one-shot"'),
                         ('resource "cache-A"', 'resource "new-home"'),
                         ('caller "caller-A"', 'caller "new-caller"'),
                         ('10s', '60s'), ('2s', '5s'), ('20s', '80s')):
            for question in (QUESTION, CURRENT):
                self.check('no-new-selection-or-limit', question, answer(DRY.replace(old, new)), {SOURCE: DRY}, phase.EXECUTION_PHASE_REASON)
        for body in (PROBE, PATH, BROKER):
            for tail in (' It proves executable identity.', ' It proves a clean environment.',
                         ' It permits a new cleanup.', ' No earlier broker writes occurred.'):
                self.check('no-proof-or-authority-transfer', QUESTION, answer(body) + tail, {SOURCE: body}, phase.EXECUTION_PHASE_REASON)
        for wrapper in ('# {}', 'title: {}', 'summary: >\n\n  {}', '```text\n{}\n```', '> {}'):
            self.check('raw-not-positive', QUESTION, answer(DRY), {SOURCE: wrapper.format(DRY)}, phase.EXECUTION_PHASE_REASON)
        self.check('uncited-not-positive', QUESTION, answer(DRY), {SOURCE: HISTORY}, phase.EXECUTION_PHASE_REASON,
                   {SOURCE: HISTORY, 'uncited': DRY})

"""Inert synthetic phase records. No command or source artifact is accessed."""
import unittest

from execution_phase_qualifiers import (
    EXECUTION_PHASE_BODY_REASON, EXECUTION_PHASE_REASON, PREFIX, SAFE_IDENTITY,
    SAFE_PERMISSION, execution_phase_problem, execution_phase_required,
)

QUESTION = 'What do the recorded execution phases establish about effects and proof domains?'
CURRENT = 'Currently, ' + QUESTION
SOURCE = '2026-10-17-090000-synthetic-phase-record'
# This retained question selects a guard. These fixtures are not the actual note bodies.
RETAINED_QUESTION = ('What exact direct Go 1.27 cache-only dry-run method and limits were recorded for k3s#30? '
                     'Distinguish one-shot authority, clean environment, executable identity, and what the dry run proves.')
WRONG = ('The cache-only dry run executed the actual cleanup with 60s/5s/caller80 deadlines. '
         'The dry run proves current headroom recovery without side effects. '
         'A write/fsync probe confirms executable identity and a clean environment.')


def record(phase='dry-run', *, method='direct', actors=True):
    details = {
        'dry-run': ('08:00:00', '08:00:01', 'cache-clean', 'toy clean -n -cache', '10s', '2s', '20s',
                    'no deletion or headroom effect', 'address selection only'),
        'actual-cleanup': ('08:03:00', '08:03:43', 'cache-clean', 'toy clean -cache', '60s', '5s', '80s',
                           'dated free-block relief', 'cache cleanup only'),
        'filesystem-probe': ('08:08:00', '08:08:01', 'write-check', 'write/fsync', 'unknown', 'unknown', 'unknown',
                             'write/fsync success', 'UID/device/write success only'),
        'path-record': ('07:00:00', '07:00:01', 'path-metadata', 'path metadata', 'not applicable', 'not applicable', 'not applicable',
                        'recorded path/version', 'dated path metadata only'),
        'broker-staging': ('07:30:00', '07:30:01', 'stage', 'broker Ensure', 'unknown', 'unknown', 'unknown',
                           'unknown', 'staging effects unknown'),
        'proposal': ('09:00:00', '09:00:01', 'cache-clean', 'toy clean -cache', 'unknown', 'unknown', 'unknown',
                     'not executed', 'no execution proof'),
    }[phase]
    start, end, action, command, term, kill, deadline, effect, proof = details
    return (f'On 2026-10-16T{start}Z to 2026-10-16T{end}Z, execution phase "{phase}" records '
            f'method "{method}", action "{action}", command "{command}", executable "/synthetic/Tool", '
            'version "1.27", source identity "dated-path-record", resource "cache-A"'
            + (', task "task-A", app "app-A", caller "caller-A", candidate "candidate-A"' if actors else '')
            + f': TERM "{term}"; kill "{kill}"; caller deadline "{deadline}"; '
              'environment "recorded conditions only"; selection "spent"; '
            f'observed effect "{effect}"; proof domain "{proof}"')


DRY = record()
ACTUAL = record('actual-cleanup')
PROBE = record('filesystem-probe')
PATH = record('path-record')
BROKER = record('broker-staging', method='brokered')
PROPOSAL = record('proposal').replace('selection "spent"', 'selection "unselected"').replace('resource "cache-A"', 'resource "cache-B"')
PARTIAL = DRY.replace('TERM "10s"', 'TERM "unknown"').replace('environment "recorded conditions only"', 'environment "unknown"')
HISTORY = 'A dated preview and a separate cleanup occurred. The earlier broker effects remain unknown.'


def answer(*records):
    return PREFIX + '. '.join(records) + '.'


def problem(draft, bodies=None, *, question=QUESTION, retrieved=()):
    return execution_phase_problem(question, draft, {SOURCE: DRY} if bodies is None else bodies,
                                   retrieved_texts=retrieved)


def raw_failures():
    values = [
        'method: direct\nobserved effect: unknown',
        'method = direct\nobserved effect = unknown',
        'method: >\n  direct\nobserved effect: unknown',
        'method: |\n  direct\nobserved effect: unknown',
        'method: direct\nThe cleanup failed.',
        'method: direct; observed effect: unknown',
        'direct has a failed cleanup.',
    ]
    for quote in ('`', '"', "'", ''):
        for relation in (': ', ' = '):
            values.append(quote + 'method' + quote + relation + quote + 'direct' + quote
                          + '\n' + quote + 'proof domain' + quote + relation + quote + 'unknown' + quote)
    return values


class ExecutionPhaseTests(unittest.TestCase):
    def reject(self, draft, bodies=None, **kwargs):
        self.assertEqual(problem(draft, bodies, **kwargs), EXECUTION_PHASE_REASON)

    def test_selector_retained_question_and_bounded_positive_records(self):
        for question in (QUESTION, CURRENT, RETAINED_QUESTION):
            self.assertTrue(execution_phase_required(question))
            self.reject(WRONG, {SOURCE: DRY + '. ' + ACTUAL + '. ' + PROBE}, question=question)
        for body in (DRY, ACTUAL, PROBE, PATH, BROKER, PROPOSAL, PARTIAL):
            for question in (QUESTION, CURRENT):
                self.assertIsNone(problem(answer(body), {SOURCE: body}, question=question))
        self.assertIsNone(problem(answer(DRY, ACTUAL), {SOURCE: DRY + '. ' + ACTUAL}))

    def test_wrong_phase_cannot_inherit_actual_flags_time_deadlines_or_effects(self):
        for old, new in (('toy clean -n -cache', 'toy clean -cache'), ('10s', '60s'), ('2s', '5s'),
                         ('20s', '80s'), ('08:00:00', '08:03:00'),
                         ('no deletion or headroom effect', 'dated free-block relief'),
                         ('address selection only', 'cache cleanup only')):
            self.reject(answer(DRY.replace(old, new)), {SOURCE: DRY + '. ' + ACTUAL})
        for claim in ('The dry run restored headroom.', 'The dry run caused no future effects.',
                      'The actual cleanup proves an exact cache size.', 'The actual cleanup proves its producer identity.'):
            self.reject(claim, {SOURCE: DRY + '. ' + ACTUAL})

    def test_filesystem_and_path_records_cannot_prove_executable_or_environment(self):
        for body in (PROBE, PATH):
            for proof in ('fresh executable identity', 'verified hash', 'fresh lease', 'clean environment', 'executable integrity'):
                changed = body.replace('UID/device/write success only' if body == PROBE else 'dated path metadata only', proof)
                self.reject(answer(changed), {SOURCE: changed})
                self.reject(answer(body) + ' It confirms ' + proof + '.', {SOURCE: body})
        self.assertIsNone(problem(SAFE_IDENTITY, {SOURCE: PROBE + '. ' + PATH}))

    def test_broker_effects_and_spent_selection_do_not_transfer_to_new_resources(self):
        for body in (BROKER, PROPOSAL, DRY):
            for tail in (' There were no earlier broker writes.', ' It permits another cleanup.',
                         ' It authorizes cache-B.', ' The new caller can use it now.'):
                self.reject(answer(body) + tail, {SOURCE: body})
        self.reject(answer(BROKER.replace('observed effect "unknown"', 'observed effect "no side effects"')),
                    {SOURCE: BROKER.replace('observed effect "unknown"', 'observed effect "no side effects"')})

    def test_all_identity_roles_remain_in_one_body_record(self):
        for old, new in (('"direct"', '"brokered"'), ('"cache-A"', '"cache-B"'),
                         ('"/synthetic/Tool"', '"/synthetic/tool"'), ('"1.27"', '"1.27.0"'),
                         ('"dated-path-record"', '"new-record"'), ('"task-A"', '"task-B"'),
                         ('"app-A"', '"app-B"'), ('"caller-A"', '"caller-B"'), ('"candidate-A"', '"candidate-B"'),
                         ('"recorded conditions only"', '"clean environment"'), ('"spent"', '"one-shot"')):
            other = DRY.replace(old, new)
            self.reject(answer(other))
            self.assertIsNone(problem(answer(other), {SOURCE: other}))

    def test_same_tuple_contradictions_refuse_without_cross_phase_vetoes(self):
        for changed in (DRY.replace('TERM "10s"', 'TERM "60s"'),
                        DRY.replace('environment "recorded conditions only"', 'environment "unknown"'),
                        DRY.replace('selection "spent"', 'selection "unknown"')):
            self.reject(answer(DRY), {SOURCE: DRY + '. ' + changed})
            self.reject(answer(changed), {SOURCE: DRY + '. ' + changed})
            self.reject(answer(DRY), retrieved={'raw-only': '```text\n' + changed + '\n```'})
        self.assertIsNone(problem(answer(DRY), {SOURCE: DRY + '. ' + ACTUAL}))
        for body in (PROBE, PATH, BROKER):
            changed = body.replace('action "', 'action "other-')
            self.reject(answer(changed), {SOURCE: changed})

    def test_split_fields_other_phases_and_newest_note_cannot_supply_union(self):
        pieces = DRY.split('; ')
        self.reject(answer(DRY), {str(index): piece for index, piece in enumerate(pieces)})
        self.reject(answer(DRY), {SOURCE: DRY[:DRY.index(': TERM')], 'newest': ACTUAL})
        self.reject(answer(DRY), {SOURCE: HISTORY}, retrieved={'uncited': DRY})
        self.reject(answer(DRY), {DRY: HISTORY})

    def test_full_raw_citation_bodies_are_required(self):
        for bodies in ({}, {SOURCE: ''}, {SOURCE: ' '}, {SOURCE: DRY, 'sixth': ''}):
            for draft in (answer(DRY), SAFE_PERMISSION, SAFE_IDENTITY):
                self.assertEqual(problem(draft, bodies), EXECUTION_PHASE_BODY_REASON)

    def test_only_narrative_body_can_supply_positive_support(self):
        for wrapper in ('# {}', 'title: {}', 'summary: >\n\n  {}', '```text\n{}\n```',
                        '---\nsummary: {}\n---', '> {}', '<pre>\n{}\n</pre>'):
            self.reject(answer(DRY), {SOURCE: wrapper.format(DRY)})
        self.assertIsNone(problem(answer(DRY), {SOURCE: '## Operation record\n\n' + DRY}))
        self.assertIsNone(problem(answer(DRY), {SOURCE: DRY.replace(', action', ',\n action')}))

    def test_raw_property_fence_heading_scalar_and_softwrap_order(self):
        for raw in raw_failures():
            for wrapper in ('{}', '# {}', 'summary: >\n  {}', '```text\n{}\n```'):
                wrapped = wrapper.format(raw.replace('\n', '\n  ') if wrapper.startswith('summary') else raw)
                for body in (DRY + '.\n\n' + wrapped, wrapped + '\n\n' + DRY):
                    self.reject(answer(DRY), {SOURCE: body})
                self.reject(answer(DRY), retrieved={'private': wrapped})

    def test_wrong_method_scope_and_date_negatives_do_not_transfer(self):
        for raw in raw_failures():
            other = raw.replace('direct', 'brokered')
            self.assertIsNone(problem(answer(DRY), {SOURCE: DRY + '.\n\n' + other}))
        for role, value in (('task', 'task-B'), ('app', 'app-B'), ('resource', 'cache-B'),
                            ('phase', 'actual-cleanup'), ('start', '2026-10-15T08:00:00Z'),
                            ('source identity', 'other-record'), ('executable', '/synthetic/Other')):
            raw = 'method: direct\n' + role + ': ' + value + '\nobserved effect: unknown'
            self.assertIsNone(problem(answer(DRY), {SOURCE: DRY + '.\n\n' + raw}))
            self.assertIsNone(problem(answer(DRY), retrieved={'different': raw}))
        self.assertIsNone(problem(answer(DRY), retrieved={'unbound': 'The cleanup failed.'}))
        self.assertIsNone(problem(answer(DRY), retrieved={'different': 'direct has a failed cleanup for task "task-B".'}))

    def test_late_uncited_and_sixth_raw_fields_can_veto_but_not_support(self):
        bodies = {str(index): DRY for index in range(6)}
        bodies['5'] += '.\n\n' + ('Recorded context. ' * 300) + '\n\nmethod: direct\nTERM: 60s'
        self.assertGreater(bodies['5'].index('TERM: 60s'), 4096)
        self.reject(answer(DRY), bodies)
        self.reject(answer(DRY), retrieved=bodies)
        self.assertIsNone(problem(answer(DRY), retrieved={DRY: HISTORY}))

    def test_unknown_source_fields_do_not_disable_a_known_limit(self):
        for value in ('unknown', '"unknown"', '>\n  direct\n  extra-data', '>', 'direct unexpected-data'):
            self.reject(answer(DRY), {SOURCE: DRY + '.\n\nmethod: ' + value + '\nobserved effect: unknown'})
        for wrapper in ('{}', '# {}', 'summary: >\n  {}', '```text\n{}\n```'):
            self.reject(answer(DRY), {SOURCE: DRY + '.\n\n' + wrapper.format('method: direct\nproof domain: mystery')})

    def test_explicit_query_roles_never_become_absent_constraints(self):
        for role, value in (('method', 'brokered'), ('task', 'task-B'), ('app', 'app-B'), ('resource', 'cache-B'),
                            ('caller', 'caller-B'), ('candidate', 'candidate-B'), ('executable', '/synthetic/tool'),
                            ('source identity', 'Other.Record'), ('version', '1.27.0'), ('scope', 'unexpected')):
            for quoted in (value, '"' + value + '"', '`' + value + '`'):
                q = QUESTION[:-1] + ' for ' + role + ' ' + quoted + '?'
                self.reject(answer(DRY), question=q)
        self.reject(answer(record(actors=False)), {SOURCE: record(actors=False)}, question=QUESTION + ' For caller "caller-A"?')
        for role, value in (('method', 'direct'), ('task', 'task-A'), ('app', 'app-A'), ('resource', 'cache-A')):
            self.assertIsNone(problem(answer(DRY), question=QUESTION[:-1] + ' for ' + role + ' "' + value + '"?'))

    def test_identity_case_punctuation_and_literal_spaces_remain_exact(self):
        for value in ('/synthetic/tool', '/synthetic/Tool/', '/synthetic/Tool ', '/synthetic/Too.l'):
            self.reject(answer(DRY), question=QUESTION[:-1] + ' for executable "' + value + '"?')
        self.assertIsNone(problem(answer(DRY.replace('"', '`')), {SOURCE: DRY}))
        body = DRY.replace('/synthetic/Tool', "/synthetic/Tool's.Name")
        self.assertIsNone(problem(answer(body), {SOURCE: body}))
        self.reject(answer(body.replace("Tool's", 'Tools')), {SOURCE: body})

    def test_partial_unknown_facets_do_not_claim_missing_sources_or_permission(self):
        for body in (DRY, PROBE, HISTORY):
            for draft in (SAFE_PERMISSION, SAFE_IDENTITY):
                self.assertIsNone(problem(draft, {SOURCE: body}))
        self.assertIsNone(problem(answer(PARTIAL), {SOURCE: PARTIAL}))
        for draft in ('No source exists.', 'No method is installed.', 'No execution is possible.',
                      SAFE_PERMISSION + ' No historical cleanup occurred.'):
            self.reject(draft)
        for raw in ('direct has current execution permission granted.',
                    'method: direct\nThe caller execution permission is allowed.'):
            self.reject(SAFE_PERMISSION, {SOURCE: DRY + '.\n\n' + raw})
        self.reject(SAFE_IDENTITY, {SOURCE: PROBE + '.\n\nIt proves executable identity.'})

    def test_explicit_path_source_identity_target_and_unknown_roles_stay_constraints(self):
        for role, value in (('path', '/synthetic/Tool'), ('sourceIdentity', 'dated-path-record'), ('target', 'cache-A')):
            for literal in (value, '"' + value + '"'):
                self.assertIsNone(problem(answer(DRY), question=QUESTION[:-1] + ' for ' + role + ' ' + literal + '?'))
                self.reject(answer(DRY), question=QUESTION[:-1] + ' for ' + role + ' "different"?')
        for role in ('root', 'object', 'operation', 'scope', 'who'):
            for literal in ('different', '"different"'):
                self.reject(answer(DRY), question=QUESTION[:-1] + ' for ' + role + ' ' + literal + '?')
        self.assertIsNone(problem(answer(DRY), retrieved={'other': 'method: direct\npath: /other/Tool\nproof domain: unknown'}))
        self.reject(answer(DRY), retrieved={'same': 'method: direct\npath: /synthetic/Tool\nproof domain: unknown'})

    def test_operation_time_is_not_capture_time_and_invalid_dates_refuse(self):
        self.assertIsNone(problem(answer(DRY), {'2026-10-20-120000-later-capture': DRY}))
        self.reject(answer(DRY.replace('2026-10-16T08:00:00Z', '2026-10-17T09:00:00Z')))
        for old, new in (('2026-10-16', '2026-02-30'), ('08:00:00', '99:00:00')):
            changed = DRY.replace(old, new)
            self.reject(answer(changed), {SOURCE: changed})

    def test_natural_negative_operation_dates_and_unknown_dates_remain_scoped(self):
        for when in ('2026-10-15', '2026-10-15T08:00:00Z'):
            raw = 'direct has a failed cleanup on ' + when + '.'
            self.assertIsNone(problem(answer(DRY), retrieved={'other-time': raw}))
        for when in ('2026-10-16', '2026-10-16T08:00:00Z'):
            self.reject(answer(DRY), retrieved={'same-time': 'direct has a failed cleanup on ' + when + '.'})
        for value in ('unknown', '2026-02-30T08:00:00Z', '?'):
            self.reject(answer(DRY), {SOURCE: DRY + '.\n\nmethod: direct\nstart: ' + value + '\nproof domain: unknown'})
        self.reject(answer(DRY), {SOURCE: DRY + '.\n\nmethod: direct cleanup failed'})

    def test_question_operation_date_is_not_the_source_capture_date(self):
        self.assertIsNone(problem(answer(DRY), question=QUESTION + ' On 2026-10-16?'))
        self.reject(answer(DRY), question=QUESTION + ' On 2026-10-17?')
        self.assertIsNone(problem(answer(DRY), question=QUESTION + ' At 2026-10-16T08:00:00Z?'))
        self.reject(answer(DRY), question=QUESTION + ' At 2026-10-16T08:03:00Z?')
        subsecond = DRY.replace('08:00:00Z', '08:00:00.123456Z')
        self.assertIsNone(problem(answer(subsecond), {SOURCE: subsecond}))

    def test_bounded_unknown_facet_keeps_explicit_method_and_task_scope(self):
        q = QUESTION[:-1] + ' for method "direct", task "task-A"?'
        for draft, claim in ((SAFE_PERMISSION, 'current execution permission granted'),
                             (SAFE_IDENTITY, 'proof confirms executable identity')):
            self.assertIsNone(problem(draft, question=q, retrieved={
                'other': 'brokered has ' + claim + ' for task "task-A".'}))
            self.assertIsNone(problem(draft, question=q, retrieved={
                'other': 'direct has ' + claim + ' for task "task-B".'}))
            self.reject(draft, question=q, retrieved={'same': 'direct has ' + claim + ' for task "task-A".'})
        for q in ('What proof domain does the dry-run establish?', 'Which execution phase was recorded?'):
            self.assertIsNone(problem(answer(DRY), question=q))

    def test_complete_draft_tail_is_not_hidden_by_display_cap(self):
        draft = answer(DRY) + ' ' + ('Recorded context. ' * 130) + ' Use it now.'
        self.assertGreater(len(draft), 2000)
        self.reject(draft)

    def test_selector_keeps_version_bearing_concepts_and_operative_facets(self):
        pairs = (
            ('What does the Go 1.27 dry-run prove?', 'What does the `Go 1.27 dry-run` prove?'),
            ('What executable identity does the dry-run record?',
             'What executable identity does the dry-run version record?'),
            ('What are the dry-run limits?', 'What are the dry-run version limits?'),
        )
        for pair in pairs:
            for question in pair:
                self.assertTrue(execution_phase_required(question), question)
                self.reject(WRONG, question=question)
                self.assertTrue(execution_phase_required(question + '; what is the filename?'), question)
        for quote in ('`', '"', "'"):
            for concept in ('Go 1.27 dry-run', 'Go v1.27.0 dry-run', 'Go 1.27 execution phase'):
                question = 'What does the ' + quote + concept + quote + ' prove?'
                self.assertTrue(execution_phase_required(question), question)
                self.reject(WRONG, question=question)
        for predicate in ('provides', 'requires', 'supports'):
            question = 'Which version ' + predicate + ' `Go 1.27 dry-run` limits?'
            self.assertTrue(execution_phase_required(question), question)
            self.reject(WRONG, question=question)

    def test_selector_keeps_version_and_facet_metadata_and_explicit_identities_opaque(self):
        questions = [
            'What is the dry-run version?', 'Which version is the dry-run record?',
            'What is the version of the dry-run limits?', 'What is the definition of dry-run limits?',
            'What is the filename of the Go 1.27 dry-run?', 'What is the title of the dry-run limits?',
            'What does "/synthetic/dry-run" prove?',
        ]
        for role in ('method', 'source', 'source identity', 'path', 'version', 'filename'):
            for quote in ('`', '"', "'"):
                questions.append('What does ' + role + ' ' + quote + 'Go 1.27 dry-run' + quote + ' prove?')
        for question in questions:
            self.assertFalse(execution_phase_required(question), question)

    def test_query_selector_formatted_concepts_and_metadata_appendices(self):
        for concept in ('execution phase', '**execution phase**', '`execution phase`', '"execution phase"',
                        'dry-run', '**dry-run**', '`dry-run`', '"proof domain"'):
            for predicate in ('provides', 'requires', 'supports'):
                q = 'What does the ' + concept + ' version ' + predicate + '?'
                self.assertTrue(execution_phase_required(q), q)
                self.reject(WRONG, question=q)
        for appendix in ('; what is the filename?', '; which version is it?', '; what is the definition?'):
            self.assertTrue(execution_phase_required(QUESTION + appendix))
        for q in ('What is the filename of the dry-run record?', 'What is the definition of an execution phase?',
                  'Which version is the execution-phase note?', 'What does method "dry-run" mean?',
                  'What is path "/synthetic/execution-phase"?', 'What is source identity "dry-run"?',
                  'What is the title "proof domain"?', 'Which version is named "execution phase"?',
                  'How do I format a table?', 'What does task "execution-phase" mean?'):
            self.assertFalse(execution_phase_required(q), q)

"""Synthetic, inert storage records. No filesystem or operational action occurs."""
import unittest

from storage_authority_qualifiers import (
    GUARDS, LIMITS, STORAGE_AUTHORITY_BODY_REASON, STORAGE_AUTHORITY_REASON,
    storage_authority_problem, storage_authority_required,
)

QUESTION = ('Which project and existing tickets own the Devbox shared-home filesystem capacity risk, '
            'and what protection constraints apply to cleanup or expansion?')
CURRENT = 'What is the current storage owner and protection limits for the Devbox shared-home filesystem cleanup or expansion?'
SOURCE = '2026-10-17-040333-storage-record'
OBJECT = '`Devbox shared-home filesystem`'
OWNER = (f'Project `K3s` owns ticket `k3s#29` for expansion of {OBJECT} as the sole writer, '
         f'conditional on {GUARDS}; {LIMITS}; '
         'existing partials, worktrees, custody, and hold `EEE#15` stay unchanged')
COMPLETE = (f'On 2026-10-17T04:02:40.594847Z, project `K3s` completed expansion of {OBJECT} '
            'via `guarded-patch` under ticket `k3s#29` from `160Gi` to `180Gi`, '
            f'with {GUARDS}; {LIMITS}; '
            'existing partials, worktrees, custody, and hold `EEE#15` stay unchanged; '
            'this is temporary logical relief, not independence, durability, or a cause diagnosis; '
            'this completed operation grants no new operation or protection release')
METADATA = (f'{OBJECT} has recorded capacity `160Gi`; '
            'this is metadata only, not an actor or permission record')
HISTORY = (f'On 2026-09-29, project `OldOwner` completed cleanup of {OBJECT} under ticket `old#7`; '
           'this historical record grants no new cleanup or deletion permission')
RETENTION = (f'{OBJECT} retention requires 72 hours after final verified activation '
             'and an explicit guarded retirement decision by project `EEE`; blocked activation leaves '
             'the timestamp unset; elapsed time alone grants no unpin permission; '
             'current candidate and rollback roots stay protected; activation requires '
             'the explicit release of hold `EEE#15` and cleanup requires fresh UID/RV guards')
READ = (f'`metadata-client` supports read-only metadata for {OBJECT}; '
        'method acceptance grants no write permission or protection release')
UNKNOWN = 'The cited source text does not establish the storage owner and protection gates.'
SAFE_UNKNOWN = 'The cited source text does not establish current storage permission.'
AUTONOMY = ('Project leads decide and implement or close tickets within their own project. '
            'The ticket process has no human approval loop. This does not select a storage method.')
WRONG = ('The project lead can clean up or expand the Devbox shared-home filesystem '
         'without additional approval because it has 160Gi capacity and leads own their tickets.')


def answer(record):
    return 'The cited source records: ' + record + '.'


def problem(draft, bodies=None, question=QUESTION, retrieved=()):
    return storage_authority_problem(question, draft, bodies if bodies is not None else {SOURCE: OWNER},
                                     retrieved_texts=retrieved)


class StorageAuthorityTests(unittest.TestCase):
    def assert_rejected(self, draft, bodies=None, **kwargs):
        self.assertEqual(problem(draft, bodies, **kwargs), STORAGE_AUTHORITY_REASON)

    def test_exact_and_formatted_questions_select_the_guard(self):
        for question in (QUESTION, CURRENT, 'Who owns storage cleanup?',
                         'What are the `filesystem capacity` protection constraints?',
                         'Who owns **storage** cleanup?', 'Who owns `storage` `expansion`?',
                         'Which project owns filesystem expansion via `/opt/metadata-client`?'):
            with self.subTest(question=question):
                self.assertTrue(storage_authority_required(question))
                self.assert_rejected(WRONG, question=question)

    def test_unrelated_governance_and_literal_names_do_not_select(self):
        for question in ('Can project leads decide which tickets to implement?',
                         'What does `/opt/storage/cleanup` mean?',
                         'What is the filename `storage`?',
                         'Can `scope-reader` read PVC metadata?',
                         'What is the shared archive cleanup status?',
                         'What did Diego authorize for the historical storage retirement?'):
            self.assertFalse(storage_authority_required(question), question)
            self.assertIsNone(problem(WRONG, question=question))

    def test_generic_autonomy_and_metadata_cannot_grant_operations(self):
        bodies = {SOURCE: AUTONOMY + '\n\n' + METADATA}
        for draft in (WRONG, 'The lead owns capacity and can expand it.',
                      'No extra approval is necessary for cleanup.',
                      answer(AUTONOMY), answer(METADATA) + ' The lead can proceed.'):
            self.assert_rejected(draft, bodies)
        self.assertIsNone(problem(answer(METADATA), bodies))

    def test_complete_narrative_records_are_positive_controls(self):
        for record in (OWNER, COMPLETE, METADATA, HISTORY, RETENTION, READ):
            with self.subTest(record=record):
                self.assertIsNone(problem(answer(record), {SOURCE: record}))

    def test_unknown_is_source_scoped_not_universal_absence(self):
        self.assertIsNone(problem(SAFE_UNKNOWN, {SOURCE: AUTONOMY}))
        self.assert_rejected(UNKNOWN, {SOURCE: OWNER})
        for draft in ('No storage authority exists.', 'No project owns the capacity risk.',
                      'The sources prove no storage permission exists anywhere.',
                      UNKNOWN + ' Therefore cleanup is allowed.'):
            self.assert_rejected(draft, {SOURCE: AUTONOMY})

    def test_every_citation_needs_a_body_even_for_unknown(self):
        for bodies in ({}, {SOURCE: ''}, {SOURCE: ' '}, {SOURCE: OWNER, 'sixth': ''}):
            for draft in (answer(OWNER), UNKNOWN):
                self.assertEqual(problem(draft, bodies), STORAGE_AUTHORITY_BODY_REASON)

    def test_mapping_values_not_keys_supply_evidence(self):
        self.assert_rejected(answer(OWNER), {OWNER: 'Unrelated prose.'})
        self.assertIsNone(problem(answer(OWNER), {'Unrelated key': OWNER}))

    def test_metadata_headings_scalars_and_code_cannot_supply_records(self):
        for raw in ('# ' + OWNER, 'title: ' + OWNER, 'summary: >\n\n  ' + OWNER,
                    'description: |\n\n\t' + OWNER, '```text\n' + OWNER + '\n```',
                    '---\nsummary: ' + OWNER + '\n---', '<!-- ' + OWNER + ' -->',
                    '## Examples\n\n' + OWNER):
            self.assert_rejected(answer(OWNER), {SOURCE: raw})
        self.assertIsNone(problem(answer(OWNER), {SOURCE: 'summary: >\n  Other text.\n\n## Record\n\n' + OWNER}))

    def test_raw_late_sixth_and_uncited_holds_veto_only(self):
        contrary = OBJECT + ' remains blocked by `EEE#15`; no new operation is permitted.'
        for wrapper in (lambda text: text, lambda text: 'summary: >\n\n  ' + text,
                        lambda text: '```text\n' + text + '\n```', lambda text: '# ' + text):
            bodies = {f'source-{index}': OWNER for index in range(6)}
            bodies['source-5'] += '\n\n' + ('Background context. ' * 300) + '\n\n' + wrapper(contrary)
            self.assertGreater(bodies['source-5'].index(contrary), 4096)
            self.assert_rejected(answer(OWNER), bodies)
            self.assert_rejected(answer(OWNER), {SOURCE: OWNER}, retrieved={'uncited': bodies['source-5']})
        self.assert_rejected(answer(OWNER), {SOURCE: contrary})

    def test_unrelated_newest_date_does_not_hide_a_veto(self):
        retrieved = {'2026-10-17-relevant': OBJECT + ' remains blocked by `EEE#15`.',
                     '2099-01-01-unrelated': '`Other PVC` remains protected.'}
        self.assert_rejected(answer(OWNER), retrieved=retrieved)
        self.assertIsNone(problem(answer(OWNER), retrieved={'2099-01-01-unrelated': '`Other PVC` remains protected.'}))

    def test_owner_method_target_ticket_case_and_punctuation_are_literal(self):
        for old, new in (('`K3s`', '`k3s`'), ('`k3s#29`', '`k3s#29.`'),
                         (OBJECT, '`Devbox Shared-home filesystem`'), ('`guarded-patch`', '`Guarded-patch`'),
                         ('`180Gi`', '`180gi`'), ('`EEE#15`', '`Other#15`'),
                         ('2026-10-17T04:02:40.594847Z', '2026-10-17T04:03:33Z')):
            source = COMPLETE if old in COMPLETE else OWNER
            self.assert_rejected(answer(source.replace(old, new)), {SOURCE: source})
        path = OWNER.replace(OBJECT, '`/vol/Home.*`')
        path_query = 'Who owns storage expansion of `/vol/Home.*`?'
        self.assertIsNone(problem(answer(path), {SOURCE: path}, question=path_query))
        for alias in ('`/vol/home.*`', '`/vol/Home`', '`/vol/Home.*.`'):
            self.assert_rejected(answer(path.replace('`/vol/Home.*`', alias)), {SOURCE: path}, question=path_query)

    def test_explicit_question_roles_bind_not_incidental_mentions(self):
        base = 'Which project owns expansion of ' + OBJECT + ' via `guarded-patch`?'
        self.assertIsNone(problem(answer(COMPLETE), {SOURCE: COMPLETE}, question=base))
        for question in ('Does project `Other` own storage expansion?',
                         'Does ticket `other#29` own storage expansion?',
                         'Who owns expansion of `Other PVC`?',
                         'Who owns storage expansion via `Other-method`?'):
            self.assert_rejected(answer(COMPLETE), {SOURCE: COMPLETE}, question=question)
        incidental = 'For reference `Other`, which project owns storage expansion?'
        self.assertIsNone(problem(answer(OWNER), question=incidental))

    def test_named_question_object_cannot_transfer_an_unrelated_source(self):
        for alias in ('`Other PVC`', '`Devbox home PVC`', '`devbox shared-home filesystem`'):
            wrong_object = OWNER.replace(OBJECT, alias)
            self.assert_rejected(answer(wrong_object), {SOURCE: wrong_object})
        for method in ('through', 'using'):
            query = 'Who owns storage expansion ' + method + ' `Other-method`?'
            self.assert_rejected(answer(COMPLETE), {SOURCE: COMPLETE}, question=query)
        wrong_hold = OWNER.replace('`EEE#15`', '`Other#15`')
        self.assert_rejected(answer(OWNER), {'one': wrong_hold, 'two': '`EEE#15` stays protected.'})
        self.assert_rejected(answer(OWNER), question='Who owns storage expansion under hold `Other#15`?')
        self.assertIsNone(problem(answer(OWNER), question='Who owns storage expansion under hold `EEE#15`?'))

    def test_roles_conditions_and_protection_limits_cannot_union_sources(self):
        for marker in (' as the sole writer', ', conditional on ' + GUARDS, '; ' + LIMITS):
            partial = OWNER.replace(marker, '')
            self.assert_rejected(answer(OWNER), {'one': partial, 'two': marker})
        self.assert_rejected(answer(OWNER), {'one': OWNER.replace('`K3s`', '`Other`'),
                                            'two': 'Project `K3s` owns an unrelated ticket.'})
        self.assert_rejected(answer(OWNER), {'one': OWNER.replace(OBJECT, '`Other PVC`'),
                                            'two': OBJECT + ' has a different owner.'})
        contrary_owner = OWNER.replace('`K3s`', '`Other`')
        self.assert_rejected(answer(OWNER), retrieved={'owner': 'summary: >\n  ' + contrary_owner})

    def test_completed_mutation_is_not_a_future_grant_or_an_inferred_event_time(self):
        for draft in ('Project `K3s` can repeat the expansion.', answer(COMPLETE) + ' Repeat it.',
                      answer(COMPLETE).replace('grants no new operation', 'grants a new operation')):
            self.assert_rejected(draft, {SOURCE: COMPLETE})
        no_time = COMPLETE.replace('2026-10-17T04:02:40.594847Z', '2026-10-17')
        self.assert_rejected(answer(COMPLETE), {SOURCE: no_time})
        self.assertIsNone(problem(answer(no_time), {SOURCE: no_time}))

    def test_history_and_metadata_never_supply_new_deletion_or_actor(self):
        for record in (HISTORY, METADATA):
            for tail in (' The lead can delete caches.', ' It permits cleanup.',
                         ' This permits a new expansion.', ' The operator can act now.'):
                self.assert_rejected(answer(record) + tail, {SOURCE: record})
        self.assert_rejected(answer(METADATA.replace('metadata only', 'permission')),
                             {SOURCE: METADATA})

    def test_retention_gates_and_read_method_limits_cannot_disappear(self):
        for marker in ('and an explicit guarded retirement decision by project `EEE`; ',
                       'blocked activation leaves the timestamp unset; ',
                       'elapsed time alone grants no unpin permission; ',
                       'current candidate and rollback roots stay protected; ',
                       'activation requires the explicit release of hold `EEE#15` and cleanup requires fresh UID/RV guards'):
            self.assert_rejected(answer(RETENTION.replace(marker, '')), {SOURCE: RETENTION})
        self.assert_rejected(answer(RETENTION) + ' After 72 hours unpin automatically.', {SOURCE: RETENTION})
        self.assert_rejected(answer(READ) + ' Use it to write the PVC.', {SOURCE: READ})
        self.assert_rejected(answer(OWNER), {SOURCE: READ + '\n\n' + AUTONOMY})

    def test_entire_draft_and_unknown_clauses_precede_display_caps(self):
        for tail in (' It can proceed now.', ' Delete it.', ' Approval is unnecessary.',
                     ' Erasure is possible.', ' The result is safe.'):
            self.assert_rejected(answer(OWNER) + tail)
            draft = answer(OWNER) + ' ' + ('Background context. ' * 110) + tail
            self.assertGreater(len(draft), 2000)
            self.assert_rejected(draft)

    def test_contextual_raw_holds_and_owner_fields_are_veto_only(self):
        for contrary in ('Activation remains blocked by `EEE#15`.',
                         'All partials and worktrees stay protected.',
                         'Owner: `Other`', 'Project is `Other`', 'Ticket = `other#1`',
                         'Scope: metadata only', 'Conditions: none'):
            self.assert_rejected(answer(OWNER), {SOURCE: OWNER + '\n\n' + contrary})
        self.assert_rejected(answer(OWNER), {SOURCE: 'Owner: `Other`\n\n' + OWNER})
        reset = OWNER + '\n\n' + '`Other PVC` remains protected. Activation remains blocked.'
        self.assertIsNone(problem(answer(OWNER), {SOURCE: reset}))
        self.assert_rejected(answer(OWNER), {SOURCE: 'Owner: `K3s`\nTicket: `k3s#29`'})

    def test_metadata_and_completed_history_do_not_release_current_holds(self):
        hold = OBJECT + ' remains blocked by `EEE#15`.'
        for record in (METADATA, HISTORY, COMPLETE):
            self.assertIsNone(problem(answer(record), {SOURCE: record + '\n\n' + OWNER + '\n\n' + hold}))
        conflict = COMPLETE.replace('`K3s`', '`Other`')
        self.assert_rejected(answer(COMPLETE), {SOURCE: COMPLETE}, retrieved={'conflict': conflict})
        later = conflict.replace('2026-10-17T04:02:40.594847Z', '2026-10-18T04:02:40.594847Z')
        self.assertIsNone(problem(answer(COMPLETE), {SOURCE: COMPLETE}, retrieved={'later': later}))

    def test_negative_retrieval_facets_abstain_without_erasing_owner_records(self):
        for draft in (UNKNOWN, 'The cited source text does not establish a storage operation method.',
                      'The cited source text does not establish the requested storage scope.'):
            self.assert_rejected(draft, {SOURCE: OWNER + '. ' + COMPLETE})
        for record in (OWNER, COMPLETE, METADATA, RETENTION):
            self.assertIsNone(problem(SAFE_UNKNOWN, {SOURCE: record}))
        raw = OWNER + '\n\nCurrent caller permission is approved.'
        self.assert_rejected(SAFE_UNKNOWN, {SOURCE: raw})

    def test_requested_plain_and_quoted_roles_and_actions_cannot_disappear(self):
        for wrong, correct in (
            ('Does project Other own filesystem expansion?', 'Does project K3s own filesystem expansion?'),
            ('Who owns filesystem expansion under ticket wrong#9?', 'Who owns filesystem expansion under ticket k3s#29?'),
            ('Who owns storage volume `Other PVC` for expansion?', 'Who owns storage volume ' + OBJECT + ' for expansion?'),
            ('Who owns filesystem expansion via other-method?', 'Who owns filesystem expansion via guarded-patch?'),
            ('Who owns filesystem expansion under hold Other#15?', 'Who owns filesystem expansion under hold EEE#15?'),
            ('Which project owns `other-filesystem` filesystem capacity for expansion?',
             'Which project owns ' + OBJECT + ' filesystem capacity for expansion?'),
        ):
            self.assert_rejected(answer(COMPLETE), {SOURCE: COMPLETE}, question=wrong)
            self.assertIsNone(problem(answer(COMPLETE), {SOURCE: COMPLETE}, question=correct))
        for query in ('Who owns filesystem expansion under ticket [wrong]?',
                      'Which project other owns filesystem expansion?',
                      'Who owns filesystem expansion for project other?',
                      'Who owns filesystem expansion via [other-method]?',
                      'Does project K3s or project Other own filesystem expansion?'):
            self.assert_rejected(answer(COMPLETE), {SOURCE: COMPLETE}, question=query)
        cleanup = 'Which project owns cleanup of ' + OBJECT + '?'
        self.assert_rejected(answer(COMPLETE), {SOURCE: COMPLETE}, question=cleanup)
        self.assert_rejected(answer(OWNER), {SOURCE: OWNER}, question=cleanup)
        self.assertIsNone(problem(answer(HISTORY), {SOURCE: HISTORY}, question=cleanup))

    def test_quoted_mutation_concepts_select_but_names_do_not_supply_intent(self):
        for query in ('What storage mutation authority does generic lead autonomy provide?',
                      'What `storage mutation authority` does generic lead autonomy provide?',
                      'What storage-mutation permission applies here?',
                      'What `storage-mutation` `permission` applies here?'):
            self.assertTrue(storage_authority_required(query))
            self.assert_rejected(WRONG, {SOURCE: METADATA}, question=query)
        for query in ('What does method `storage-mutation` mean?',
                      'What is the filename `storage-mutation`?',
                      'Which method is named `storage-mutation`?',
                      'What `storage-mutation` method is installed?'):
            self.assertFalse(storage_authority_required(query), query)

    def test_quoted_raw_property_keys_preserve_values_and_context(self):
        for quote in ('', '`', '"', "'"):
            for value_quote in ('`', '"', "'"):
                bad = quote + 'Owner' + quote + ': ' + value_quote + 'Other' + value_quote + '.'
                good = quote + 'Owner' + quote + ': ' + value_quote + 'K3s' + value_quote + '.'
                self.assert_rejected(answer(OWNER), {SOURCE: OWNER + '.\n\n' + bad})
                self.assertIsNone(problem(answer(OWNER), {SOURCE: OWNER + '.\n\n' + good}))
                reset = OWNER + '.\n\n`Other PVC` remains protected.\n\n' + bad
                self.assertIsNone(problem(answer(OWNER), {SOURCE: reset}))
        self.assert_rejected(answer(OWNER), {SOURCE: OWNER + '.\n\n"protection limits": "none".'})
        matching = OWNER + '.\n\n"protection limits": "' + LIMITS + '".'
        self.assertIsNone(problem(answer(OWNER), {SOURCE: matching}))

    def test_operation_holds_bind_the_declared_action_not_another_action(self):
        for raw in ('Expansion remains blocked.', '# Expansion remains blocked.',
                    'summary: >\n  Expansion remains blocked.', '```text\nExpansion remains blocked.\n```'):
            self.assert_rejected(answer(OWNER), {SOURCE: OWNER + '.\n\n' + raw})
        for reset in ('Cleanup remains blocked.', 'Action: cleanup.\n\nCleanup remains blocked.',
                      '`Other PVC` remains protected.\n\nExpansion remains blocked.'):
            self.assertIsNone(problem(answer(OWNER), {SOURCE: OWNER + '.\n\n' + reset}))
        self.assertIsNone(problem(answer(COMPLETE), {SOURCE: COMPLETE + '.\n\nExpansion remains blocked.'}))

    def test_source_order_does_not_erase_an_earlier_owner_property(self):
        other = METADATA.replace(OBJECT, '`Other PVC`')
        for key in ('Owner', '`Owner`', '"Owner"'):
            prefix = key + ': `Other`.\n\n'
            body = prefix + OWNER + '.\n\n' + other
            self.assert_rejected(answer(OWNER), {SOURCE: body})
            late = ('Background context. ' * 300) + '\n\n' + body
            self.assertGreater(late.index(prefix), 4096)
            self.assert_rejected(answer(OWNER), {SOURCE: OWNER, 'sixth': late})
            self.assertIsNone(problem(answer(OWNER), {SOURCE: key + ': `K3s`.\n\n' + OWNER + '.\n\n' + other}))
            reset = '`Other PVC` remains protected.\n\n' + prefix + OWNER
            self.assertIsNone(problem(answer(OWNER), {SOURCE: reset}))

    def test_source_only_qualifiers_cannot_hide_protection_in_a_tail(self):
        body = OWNER + '\n\n' + OBJECT + ' custody release remains blocked.'
        self.assert_rejected(answer(OWNER), {SOURCE: body})
        self.assertIsNone(problem(SAFE_UNKNOWN, {SOURCE: body}))


if __name__ == '__main__':
    unittest.main()

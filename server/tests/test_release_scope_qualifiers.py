"""Synthetic release records. No command or external request occurs."""
import unittest

from release_scope_qualifiers import (
    RELEASE_SCOPE_BODY_REASON, RELEASE_SCOPE_REASON, release_scope_problem, release_scope_required,
)

APP = '`ComputeHours`'
REV = '`a71c9e23d4f6b80123567890abcdef123456789a`'
PREVIOUS = '`b81c9e23d4f6b80123567890abcdef123456789a`'
METHOD = '`maintenance/example-admission/deploy-dev.py`'
OLD_METHOD = '`maintenance/example-admission/isolated-preview.py`'
OLD = '2026-10-16-134200-compute-preview'
NEW = '2026-10-16-135200-compute-dev-receipt'
BASELINE = '2026-10-13-090000-compute-old-dev'
DATES = {OLD: '2026-10-16T13:42:00+00:00', NEW: '2026-10-16T13:52:00+00:00',
         BASELINE: '2026-10-13T09:00:00+00:00'}
QUESTION = ('What is the current ComputeHours dev app-only release interface after the October 16 '
            'deployment, and which scope does it cover?')
NONCURRENT = 'Which ComputeHours app-only dev release interface and scope did the source record?'
EXACT_QUESTION = ('What is the current GPUHours dev app-only release interface after the October 16 '
                  'deployment of bc37ed25, and which scope does it cover?')
COMPLETE = (
    APP + ' completed and verified an app-only dev release of ' + REV + ' via ' + METHOD
    + ', limited to fixed-app metadata, code ConfigMaps, and a conditional Deployment patch; '
    'this dev-only release supersedes the earlier dev hold, not production durability, backup, '
    'or restore verification; it provides no exec or backup interface and transfers no permission.'
)
PREVIEW = (
    APP + ' proposed a disposable manual-test preview of ' + REV
    + ', limited to isolated scratch and loopback resources, with separate admission required '
    'and no admission receipt or execution; the existing instance, PVC, workers, and routes stay unchanged.'
)
WRONG = (
    'ComputeHours deployed ' + REV + ' via ' + METHOD + ' for the disposable manual-test preview '
    'scope. Live state is not verified.'
)
PROPERTY_SEPARATORS = (' ', ' is ', ' was ', ' remains ', ': ', ':', ' = ', '=', ' IS: ', ' IS = ')
PROPERTY_CASES = tuple(
    METHOD + ' ' + role + separator + value + '.'
    for role in ('release status', 'Release SCOPE', 'RELEASE_PHASE', 'has release-status')
    for separator in PROPERTY_SEPARATORS
    for value in ('not completed', '`NOT COMPLETED`', 'preview.v2', '`preview.v2`')
)
PROPERTY_GOOD_CASES = tuple(
    METHOD + ' ' + role + separator + value + '.'
    for role, value in (
        ('release status', 'completed app-only dev'), ('RELEASE PHASE', '`COMPLETED APP-ONLY DEV`'),
        ('has release_scope', '`ONLY FIXED-APP METADATA, CODE CONFIGMAPS, AND A CONDITIONAL DEPLOYMENT PATCH`'),
    )
    for separator in PROPERTY_SEPARATORS
)


def problem(answer, bodies=None, *, retrieved=None, dates=None, question=QUESTION, current=True):
    return release_scope_problem(
        question, answer, {NEW: COMPLETE} if bodies is None else bodies,
        source_dates=DATES if dates is None else dates,
        retrieved_texts=retrieved, current=current,
    )


class ReleaseScopeQualifierTests(unittest.TestCase):
    def test_selector_is_bounded_and_preserves_concept_format(self):
        for question in (QUESTION, NONCURRENT, EXACT_QUESTION,
                         'Which ComputeHours `release` `scope` did the source record?'):
            self.assertTrue(release_scope_required(question))
        for question in ('What version does ComputeHours use?', 'What does release mean?',
                         'Where is `release/scope/deploy-dev.py`?',
                         'What is the weather?', 'What is the network origin for edge.example?'):
            self.assertFalse(release_scope_required(question))

    def test_combined_concept_literals_keep_the_selector_without_method_intent(self):
        for phrase in ('release scope', 'current release scope', 'dev app-only release interface',
                       'app-only dev release scope'):
            question = 'Which ComputeHours `' + phrase + '` did the source record?'
            self.assertTrue(release_scope_required(question))
            self.assertIsNone(problem(COMPLETE, question=question))
            self.assertEqual(problem(WRONG, question=question), RELEASE_SCOPE_REASON)
        for question in ('What is the filename of `release scope`?', 'What does `release scope` mean?',
                         'Which tool is named `release scope`?', 'What is `release-scope.py`?',
                         'Where is `release/scope/deploy-dev.py`?', 'What is the name of `release scope`?'):
            self.assertFalse(release_scope_required(question))

    def test_wrong_preview_transfer_uses_new_reason(self):
        self.assertEqual(problem(WRONG, {OLD: PREVIEW, NEW: COMPLETE}), RELEASE_SCOPE_REASON)
        exact = WRONG.replace('ComputeHours', 'GPUHours')
        bodies = {OLD: PREVIEW.replace('ComputeHours', 'GPUHours'), NEW: COMPLETE.replace('ComputeHours', 'GPUHours')}
        self.assertEqual(problem(exact, bodies, question=EXACT_QUESTION), RELEASE_SCOPE_REASON)

    def test_complete_dev_receipt_has_no_fabricated_event_time(self):
        self.assertIsNone(problem(COMPLETE))
        self.assertIsNone(problem(COMPLETE.replace('completed and verified', 'deployed and verified')))
        self.assertIsNone(problem('The later source records that ' + COMPLETE))
        wrapped = '- ' + COMPLETE.replace(', limited', ',\nlimited').replace('; this', ';\nthis')
        self.assertIsNone(problem(COMPLETE, {NEW: wrapped}))
        self.assertEqual(problem('On 2026-10-16, ' + COMPLETE), RELEASE_SCOPE_REASON)
        dated = 'On 2026-10-15, ' + COMPLETE
        self.assertIsNone(problem(dated, {NEW: dated}))
        self.assertEqual(problem(dated.replace('2026-10-15', '2026-10-16'), {NEW: dated}), RELEASE_SCOPE_REASON)

    def test_requested_revision_does_not_transfer_from_another_release(self):
        question = QUESTION.replace('deployment,', 'deployment of a71c9e23,')
        self.assertIsNone(problem(COMPLETE, question=question))
        self.assertEqual(problem(COMPLETE.replace(REV, PREVIOUS),
                                 {NEW: COMPLETE.replace(REV, PREVIOUS)}, question=question), RELEASE_SCOPE_REASON)

    def test_requested_app_method_and_revision_roles_do_not_use_any_mention(self):
        wrong_queries = (
            'Which ComputeHours app-only dev release interface uses ' + OLD_METHOD + ' and what scope does it cover?',
            QUESTION.replace('deployment,', 'deployment of `dev-interface-next`,'),
            'What is the current OtherHours release interface, not ComputeHours?',
            'Which `COMPUTEHOURS` release scope did the source record?',
            'Which ComputeHours release interface uses ' + METHOD.upper() + '?',
            QUESTION.replace('deployment,', 'deployment of [unknown],'),
            QUESTION.replace('deployment,', 'deployment of an unknown revision,'),
            'Which ComputeHours and OtherHours release interface did the source record?',
        )
        both = COMPLETE + '\n' + COMPLETE.replace('ComputeHours', 'OtherHours')
        for question in wrong_queries:
            self.assertEqual(problem(COMPLETE, {NEW: both}, question=question), RELEASE_SCOPE_REASON)
        right_method = 'Which ComputeHours release interface uses ' + METHOD + ' and what scope does it cover?'
        self.assertIsNone(problem(COMPLETE, question=right_method))
        next_release = COMPLETE.replace(REV, '`dev-interface-next`')
        next_query = QUESTION.replace('deployment,', 'deployment of `dev-interface-next`,')
        self.assertIsNone(problem(next_release, {NEW: next_release}, question=next_query))
        self.assertEqual(problem(next_release.replace('dev-interface-next', 'dev-interface-next-extra'),
                                 {NEW: next_release.replace('dev-interface-next', 'dev-interface-next-extra')},
                                 question=next_query), RELEASE_SCOPE_REASON)
        other = COMPLETE.replace('ComputeHours', 'OtherHours')
        self.assertIsNone(problem(other, {NEW: both}, question=wrong_queries[2]))
        self.assertIsNone(problem(COMPLETE, question=QUESTION.replace('deployment,', 'deployment of a71c9e23,')))
        self.assertEqual(problem(COMPLETE, question=QUESTION.replace('deployment,', 'deployment of a71,')), RELEASE_SCOPE_REASON)

    def test_historical_comparisons_keep_explicit_separate_identity_scopes(self):
        for question, other in (
            ('Compare ComputeHours and OtherHours release scopes.', COMPLETE.replace('ComputeHours', 'OtherHours')),
            ('Compare ComputeHours release scopes for revisions ' + PREVIOUS + ' and ' + REV + '?', COMPLETE.replace(REV, PREVIOUS)),
            ('Compare ComputeHours release interfaces using ' + METHOD + ' and ' + OLD_METHOD + '?', COMPLETE.replace(METHOD, OLD_METHOD)),
        ):
            answer = 'Historical record: ' + COMPLETE + ' Historical record: ' + other
            self.assertIsNone(problem(answer, {NEW: COMPLETE, OLD: other}, question=question, current=False))
            self.assertEqual(problem(COMPLETE, {NEW: COMPLETE, OLD: other}, question=question), RELEASE_SCOPE_REASON)

    def test_source_date_is_not_a_completion_receipt(self):
        for text in (PREVIEW, 'ComputeHours has a later source date.', 'ComputeHours release is planned.'):
            self.assertEqual(problem(COMPLETE, {NEW: text}), RELEASE_SCOPE_REASON)
        self.assertEqual(problem(COMPLETE, dates={}), RELEASE_SCOPE_REASON)
        self.assertEqual(problem(COMPLETE, dates={NEW: 'unknown'}), RELEASE_SCOPE_REASON)

    def test_old_preview_is_limited_history_not_completion(self):
        self.assertIsNone(problem(PREVIEW, {OLD: PREVIEW}))
        self.assertIsNone(problem('Historical record: ' + PREVIEW, {OLD: PREVIEW},
                                  retrieved={NEW: COMPLETE}))
        self.assertEqual(problem(COMPLETE, {OLD: PREVIEW}), RELEASE_SCOPE_REASON)
        self.assertEqual(problem(PREVIEW, {OLD: PREVIEW}, retrieved={NEW: COMPLETE}), RELEASE_SCOPE_REASON)

    def test_later_proposal_does_not_undo_completed_dev_receipt(self):
        self.assertIsNone(problem(COMPLETE, retrieved={NEW: PREVIEW},
                                  dates={NEW: DATES[NEW]}))
        self.assertIsNone(problem(COMPLETE, retrieved={OLD: PREVIEW}))

    def test_newer_completed_body_vetoes_old_current_baseline(self):
        old = COMPLETE.replace(REV, PREVIOUS)
        self.assertEqual(problem(old, {BASELINE: old}, retrieved={NEW: COMPLETE}), RELEASE_SCOPE_REASON)
        self.assertIsNone(problem('Historical record: ' + old, {BASELINE: old}, retrieved={NEW: COMPLETE}))
        self.assertIsNone(problem(old, {BASELINE: old}, retrieved={NEW: COMPLETE}, current=False))
        self.assertIsNone(problem(COMPLETE, retrieved={BASELINE: old}))

    def test_newer_metadata_is_not_positive_or_an_automatic_winner(self):
        old = COMPLETE.replace(REV, PREVIOUS)
        for text in ('# ' + COMPLETE, 'summary: ' + COMPLETE, '```text\n' + COMPLETE + '\n```'):
            self.assertIsNone(problem(old, {BASELINE: old}, retrieved={NEW: text}))
            self.assertEqual(problem(COMPLETE, {NEW: text}), RELEASE_SCOPE_REASON)
        self.assertEqual(problem(old, {BASELINE: old}, retrieved={NEW: COMPLETE.replace(METHOD, OLD_METHOD)}),
                         RELEASE_SCOPE_REASON)

    def test_each_phase_resource_and_limit_stays_in_one_record(self):
        for old, new in (
            ('app-only dev', 'production'),
            ('fixed-app metadata', 'all cluster metadata'),
            ('a conditional Deployment patch', 'all Deployment patches'),
            ('not production durability', 'including production durability'),
            ('no exec', 'exec'),
            ('transfers no permission', 'transfers caller permission'),
            ('this dev-only release supersedes the earlier dev hold, ', ''),
        ):
            self.assertEqual(problem(COMPLETE.replace(old, new)), RELEASE_SCOPE_REASON)
        self.assertEqual(problem(COMPLETE.replace('completed and verified', 'planned')), RELEASE_SCOPE_REASON)

    def test_same_hash_does_not_union_apps_methods_or_scopes(self):
        for source in (COMPLETE.replace('ComputeHours', 'OtherHours'), COMPLETE.replace(METHOD, OLD_METHOD), PREVIEW):
            self.assertEqual(problem(COMPLETE, {NEW: source}), RELEASE_SCOPE_REASON)
        pieces = {OLD: PREVIEW.replace(OLD_METHOD, METHOD), NEW: COMPLETE.replace(REV, PREVIOUS)}
        self.assertEqual(problem(COMPLETE, pieces), RELEASE_SCOPE_REASON)
        for source in (COMPLETE.replace(METHOD, OLD_METHOD), COMPLETE.replace(REV, PREVIOUS)):
            self.assertIsNone(problem(source, {NEW: source}))

    def test_metadata_fences_titles_and_scalars_never_supply_positive_support(self):
        wrappers = ('# {}', 'title: {}', 'description: >\n\n  {}', 'summary: |\n\n  {}',
                    '```text\n{}\n```', '<pre>\n{}\n</pre>', '> {}', '    {}',
                    '## Examples\n{}', '---\nsummary: {}\n---', '{}\n===')
        for wrapper in wrappers:
            with self.subTest(wrapper=wrapper):
                self.assertEqual(problem(COMPLETE, {NEW: wrapper.format(COMPLETE)}), RELEASE_SCOPE_REASON)

    def test_missing_and_blank_citations_fail_closed(self):
        for text in ('', ' ', None):
            self.assertEqual(problem(COMPLETE, {NEW: COMPLETE, OLD: text}), RELEASE_SCOPE_BODY_REASON)
        self.assertEqual(problem(COMPLETE, {}), RELEASE_SCOPE_BODY_REASON)

    def test_mapping_values_not_keys_supply_support_and_veto(self):
        self.assertEqual(problem(COMPLETE, {COMPLETE: 'ComputeHours release is planned.'}), RELEASE_SCOPE_REASON)
        contrary = METHOD + ' release status is `proposal`.'
        self.assertIsNone(problem(COMPLETE, {contrary: COMPLETE}, dates={contrary: DATES[NEW]}))

    def test_raw_properties_in_metadata_code_and_scalars_keep_the_veto(self):
        for field in (' release status is `proposal`.', ' release status is `unknown`.',
                      ' release scope is `disposable manual-test preview`.', ' release status is `not completed`.'):
            contrary = METHOD + field
            for raw in ('# ' + contrary, 'summary: >\n\n  ' + contrary,
                        '```text\n' + contrary + '\n```', '<pre>\n' + contrary + '\n</pre>'):
                with self.subTest(raw=raw):
                    self.assertEqual(problem(COMPLETE, {NEW: COMPLETE + '\n' + raw}), RELEASE_SCOPE_REASON)

    def test_named_role_separator_and_value_policy_does_not_need_global_identity(self):
        for contrary in PROPERTY_CASES:
            with self.subTest(contrary=contrary):
                self.assertEqual(problem(COMPLETE, {NEW: COMPLETE, OLD: contrary}), RELEASE_SCOPE_REASON)
        for safe in PROPERTY_GOOD_CASES:
            with self.subTest(safe=safe):
                self.assertIsNone(problem(COMPLETE, {NEW: COMPLETE, OLD: safe}))

    def test_named_role_policy_keeps_literal_method_app_and_revision_identity(self):
        for contrary in PROPERTY_CASES[::8]:
            for source in (contrary.replace(METHOD, OLD_METHOD), contrary.replace(METHOD, METHOD.upper()),
                           contrary.rstrip('.') + ' for `OtherHours` revision ' + REV + '.',
                           contrary.rstrip('.') + ' for ' + APP + ' revision ' + PREVIOUS + '.',
                           contrary.rstrip('.') + ' for ' + APP.upper() + ' revision ' + REV + '.'):
                self.assertIsNone(problem(COMPLETE, {NEW: COMPLETE, OLD: source}))

    def test_named_role_policy_survives_sixth_metadata_code_scalar_and_late_text(self):
        for contrary in PROPERTY_CASES[::10]:
            bodies = {str(index): COMPLETE for index in range(6)}
            dates = {name: DATES[NEW] for name in bodies}
            for wrapper in ('# {}', 'summary: >\n\n  {}', 'description: |\n\n  {}', '```text\n{}\n```'):
                bodies['5'] = wrapper.format(contrary)
                self.assertEqual(problem(COMPLETE, bodies, dates=dates), RELEASE_SCOPE_REASON)
            late = COMPLETE + '\n' + 'Recorded context. ' * 300 + '\n' + contrary
            self.assertGreater(late.index(contrary), 4096)
            self.assertEqual(problem(COMPLETE, {NEW: late}), RELEASE_SCOPE_REASON)

    def test_ordered_local_properties_do_not_union_apps_or_borrow_draft_identity(self):
        other = COMPLETE.replace('ComputeHours', 'OtherHours')
        for value in ('`unknown`', 'unknown', '`NOT COMPLETED`'):
            limit = 'The release status = ' + value + '.'
            self.assertEqual(problem(COMPLETE, {NEW: other + '\n' + COMPLETE + '\n' + limit}), RELEASE_SCOPE_REASON)
            self.assertIsNone(problem(COMPLETE, {NEW: COMPLETE + '\n' + other + '\n' + limit}))
        self.assertIsNone(problem(COMPLETE, {NEW: other + '\n' + COMPLETE}))
        self.assertIsNone(problem(COMPLETE, {NEW: 'The release status is `unknown`.', OLD: COMPLETE}))
        for reset in ('App: `OtherHours`', 'Revision = ' + PREVIOUS, 'Method: ' + OLD_METHOD):
            text = COMPLETE + '\n' + reset + '\nThe release status is `unknown`.'
            self.assertIsNone(problem(COMPLETE, {NEW: text}))
        named = METHOD + ' release status = `completed app-only dev` for `OtherHours` revision ' + REV + '.'
        self.assertIsNone(problem(COMPLETE, {NEW: COMPLETE + '\n' + named + '\nThe release status is `unknown`.'}))

    def test_explicit_other_app_role_resets_keep_named_and_generic_properties_separate(self):
        for reset in ('App: `OtherHours`', '# OtherHours release record',
                      COMPLETE.replace('ComputeHours', 'OtherHours')):
            text = COMPLETE + '\n' + reset + '\n' + METHOD + ' release status = `unknown`.'
            self.assertIsNone(problem(COMPLETE, {NEW: text}))
        for raw in ('# ComputeHours release record\n', ''):
            raw += METHOD + ' release scope: `preview-only`.'
            dates = {**DATES, OLD: '2026-10-17T13:42:00Z'}
            self.assertEqual(problem(COMPLETE, {NEW: COMPLETE}, retrieved={OLD: raw}, dates=dates), RELEASE_SCOPE_REASON)
            self.assertEqual(problem(COMPLETE, {NEW: COMPLETE}, retrieved={OLD: raw}, dates=dates,
                                     current=False), RELEASE_SCOPE_REASON)

    def test_raw_canonical_soft_wrap_uses_the_same_complete_record_grammar(self):
        preview = PREVIEW.replace(REV, REV + ' via ' + METHOD)
        for raw in (preview, preview.replace(', limited', ',\nlimited'),
                    preview.replace(' via ', '\nvia ')):
            for wrapper in ('```text\n{}\n```', 'summary: >\n\n  {}', '# {}'):
                contrary = wrapper.format(raw)
                self.assertEqual(problem(COMPLETE, {NEW: COMPLETE, OLD: contrary}), RELEASE_SCOPE_REASON)
                late = COMPLETE + '\n' + 'Recorded context. ' * 300 + '\n' + contrary
                self.assertGreater(late.index(contrary), 4096)
                self.assertEqual(problem(COMPLETE, {NEW: late}, current=False), RELEASE_SCOPE_REASON)
                bodies = {str(index): COMPLETE for index in range(6)}
                bodies['5'] = contrary
                self.assertEqual(problem(COMPLETE, bodies, dates={name: DATES[NEW] for name in bodies}),
                                 RELEASE_SCOPE_REASON)
        historical = 'Historical record: ' + preview
        self.assertIsNone(problem(COMPLETE, {NEW: COMPLETE, OLD: historical}))

    def test_ordered_local_limit_retains_late_and_sixth_raw_vetoes(self):
        other = COMPLETE.replace('ComputeHours', 'OtherHours')
        for wrapper in ('{}', '# {}', 'summary: >\n\n  {}', '```text\n{}\n```'):
            limit = wrapper.format('The release status = `unknown`.')
            body = other + '\n' + COMPLETE + '\n' + 'Recorded context. ' * 300 + '\n' + limit
            self.assertGreater(body.index(limit), 4096)
            self.assertEqual(problem(COMPLETE, {NEW: body}), RELEASE_SCOPE_REASON)
            bodies = {str(index): COMPLETE for index in range(6)}
            bodies['5'] = body
            self.assertEqual(problem(COMPLETE, bodies, dates={name: DATES[NEW] for name in bodies}),
                             RELEASE_SCOPE_REASON)

    def test_newer_retrieved_raw_limit_can_veto_but_cannot_support(self):
        raw = 'summary: >\n\n  ' + APP + '\n  ' + METHOD + ' release status is `unknown`.'
        self.assertEqual(problem(COMPLETE, {OLD: COMPLETE}, retrieved={NEW: raw}), RELEASE_SCOPE_REASON)
        self.assertEqual(problem(COMPLETE, {OLD: PREVIEW}, retrieved={NEW: COMPLETE}), RELEASE_SCOPE_REASON)

    def test_local_generic_raw_scope_and_phase_fields_keep_the_veto(self):
        for contrary in ('The release is `not completed`.', 'This release status is `unknown`.',
                         'release_scope: `disposable manual-test preview`', 'release_phase = `proposal`'):
            for wrapper in ('{}', '# {}', 'summary: >\n\n  {}', '```text\n{}\n```'):
                self.assertEqual(problem(COMPLETE, {NEW: COMPLETE + '\n' + wrapper.format(contrary)}),
                                 RELEASE_SCOPE_REASON)
        self.assertIsNone(problem(COMPLETE, {NEW: COMPLETE + '\nThe release scope is `'
                                            + 'fixed-app metadata, code ConfigMaps, and a conditional Deployment patch`.'}))

    def test_raw_properties_keep_app_revision_and_method_boundaries(self):
        for suffix in (' for `OtherHours` revision ' + REV, ' for ' + APP + ' revision ' + PREVIOUS):
            contrary = METHOD + ' release status is `proposal`' + suffix + '.'
            self.assertIsNone(problem(COMPLETE, {NEW: COMPLETE + '\n' + contrary}))
        contrary = OLD_METHOD + ' release status is `proposal`.'
        self.assertIsNone(problem(COMPLETE, {NEW: COMPLETE + '\n' + contrary}))

    def test_every_raw_sixth_source_and_late_text_is_checked(self):
        bodies = {str(index): COMPLETE for index in range(6)}
        dates = {name: DATES[NEW] for name in bodies}
        self.assertIsNone(problem(COMPLETE, bodies, dates=dates))
        bodies['5'] = '```text\n' + METHOD + ' release status is `proposal`.\n```'
        self.assertEqual(problem(COMPLETE, bodies, dates=dates), RELEASE_SCOPE_REASON)
        text = COMPLETE + '\n' + 'Recorded context. ' * 300 + '\n' + METHOD + ' release status is `proposal`.'
        self.assertGreater(text.index(' release status'), 4096)
        self.assertEqual(problem(COMPLETE, {NEW: text}), RELEASE_SCOPE_REASON)

    def test_full_draft_tail_and_production_promotion_are_not_qualification(self):
        for tail in (' Production durability is verified.', ' Use the driver now.',
                     ' The preview is admitted.', ' All future state is verified.',
                     ' The caller has exec permission.', ' Live state is not verified.'):
            self.assertEqual(problem(COMPLETE + tail), RELEASE_SCOPE_REASON)
        answer = COMPLETE + ' ' + ('Historical record: ' + COMPLETE + ' ') * 6 + ' The caller has permission.'
        self.assertGreater(answer.index('The caller'), 2000)
        self.assertEqual(problem(answer), RELEASE_SCOPE_REASON)

    def test_unrelated_question_adds_no_guard(self):
        self.assertIsNone(problem('Unknown text.', {}, question='What is the weather?'))


if __name__ == '__main__':
    unittest.main()

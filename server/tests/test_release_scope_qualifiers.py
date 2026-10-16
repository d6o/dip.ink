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

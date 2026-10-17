"""Synthetic inert sandbox records. No sandbox or external request occurs."""
import unittest

from sandbox_contract_qualifiers import (
    BOUNDED_LACK, FEATURES, LIMIT, PARTIAL_FEATURES, PREFIX, SAFE_UNKNOWN,
    SANDBOX_CONTRACT_BODY_REASON, SANDBOX_CONTRACT_REASON,
    sandbox_contract_problem, sandbox_contract_required,
)

QUESTION = ('What exact existing Devbox disposable test sandbox contract provides private mount/PID/network namespaces, '
            'a new proc mount, kernel denial of AF_INET/AF_INET6 socket creation, allowlisted Node/Git/libraries/source '
            'only, no host credential or container-socket access, and external timeout plus owned-process cleanup?')
CURRENT = 'Currently, ' + QUESTION
SOURCE = '2026-10-17-090000-synthetic-sandbox'
HEADER = ('On 2026-10-16T08:00:00Z, method `synth-method` records a disposable test sandbox contract '
          'for task `synth-task`, app `synth-app`, root `/synthetic/root`, phase `test`, and scope `offline-unit`: ')
COMPLETE = HEADER + '; '.join(FEATURES) + '; ' + LIMIT
PARTIAL = HEADER.replace('a disposable test', 'a partial historical disposable test') + PARTIAL_FEATURES
HISTORY = ('The synthetic private Wine checks passed isolation and display start. '
           'The proc mount probes returned errno 13. Wine exited 134 before a builtin command succeeded. '
           'The allocation cause remains unknown. Retained files grant no execution permission.')
WRONG = ('The historical Companion 0.3.14 private Wine QA root supplies the full current sandbox contract. '
         'Use that method for the current candidate.')
AUTONOMY = 'A project lead can implement project tickets. This does not select an execution method.'


def answer(record):
    return PREFIX + record + '.'


def problem(draft, bodies=None, *, question=QUESTION, retrieved=()):
    return sandbox_contract_problem(question, draft, {SOURCE: COMPLETE} if bodies is None else bodies,
                                    retrieved_texts=retrieved)


class SandboxContractTests(unittest.TestCase):
    def reject(self, draft, bodies=None, **kwargs):
        self.assertEqual(problem(draft, bodies, **kwargs), SANDBOX_CONTRACT_REASON)

    def test_exact_wrong_history_and_complete_positive_controls(self):
        self.assertTrue(sandbox_contract_required(QUESTION))
        self.reject(WRONG, {SOURCE: HISTORY})
        for question in (QUESTION, CURRENT):
            for record in (COMPLETE, PARTIAL):
                self.assertIsNone(problem(answer(record), {SOURCE: record}, question=question))
        self.assertIsNone(problem(BOUNDED_LACK, {SOURCE: HISTORY}))
        self.assertIsNone(problem(SAFE_UNKNOWN))
        self.reject(BOUNDED_LACK)

    def test_every_required_conjunct_is_mandatory_in_one_record(self):
        for feature in FEATURES:
            omitted = COMPLETE.replace(feature + '; ', '')
            self.reject(answer(omitted), {SOURCE: omitted})
            self.reject(answer(COMPLETE), {SOURCE: omitted})
        self.reject(answer(COMPLETE), {SOURCE: COMPLETE.replace('; ' + LIMIT, '')})
        plan = COMPLETE.replace('phase `test`', 'phase `plan`')
        self.reject(answer(plan), {SOURCE: plan})
        pieces = {str(i): HEADER + feature + '; ' + LIMIT for i, feature in enumerate(FEATURES)}
        self.reject(answer(COMPLETE), pieces)

    def test_similar_features_cannot_replace_exact_kernel_or_mount_contracts(self):
        replacements = (
            (FEATURES[0], 'private user/network/PID/IPC/UTS namespaces and chroot'),
            (FEATURES[1], 'an existing empty /proc path'),
            (FEATURES[1], 'no host proc access'),
            (FEATURES[1], 'a failed proc mount probe with errno 13'),
            (FEATURES[2], 'no external interface'),
            (FEATURES[2], 'a private network namespace'),
            (FEATURES[2], 'RuntimeDefault seccomp and proxy egress'),
            (FEATURES[2], 'kernel denial of AF_INET socket creation'),
            (FEATURES[2], 'kernel denial of AF_INET6 socket creation'),
            (FEATURES[2], FEATURES[2].replace('AF_INET6', 'af_inet6')),
            (FEATURES[3], 'isolated Wine with retained libraries'),
            (FEATURES[6], 'a soft timeout'),
            (FEATURES[7], 'best-effort caller cleanup'),
        )
        for old, new in replacements:
            body = COMPLETE.replace(old, new)
            self.reject(answer(body), {SOURCE: body})
            self.reject(answer(COMPLETE), {SOURCE: body})

    def test_task_app_root_method_phase_scope_stay_together(self):
        for old, new in (('`synth-method`', '`other-method`'), ('`synth-task`', '`other-task`'),
                         ('`synth-app`', '`other-app`'), ('`/synthetic/root`', '`/synthetic/other`'),
                         ('`test`', '`preflight`'), ('`offline-unit`', '`other-scope`')):
            other = COMPLETE.replace(old, new)
            self.reject(answer(COMPLETE), {SOURCE: other})
            self.assertIsNone(problem(answer(other), {SOURCE: other}))
        left = COMPLETE.replace(FEATURES[1], 'a missing proc mount')
        right = COMPLETE.replace('`synth-task`', '`other-task`').replace(FEATURES[2], 'proxy egress')
        self.reject(answer(COMPLETE), {SOURCE: left, 'other': right})

    def test_partial_history_preserves_failure_without_cause_or_method_absence(self):
        self.assertIsNone(problem(answer(PARTIAL), {SOURCE: PARTIAL + '. ' + HISTORY}))
        for tail in (' It is the full current gate.', ' Use this root now.',
                     ' The missing proc mount caused the allocation failure.',
                     ' No Devbox sandbox exists.', ' No method can run tests.'):
            self.reject(answer(PARTIAL) + tail, {SOURCE: PARTIAL})
        self.reject(answer(PARTIAL.replace('allocation cause remains unknown', 'missing proc caused the allocation failure')),
                    {SOURCE: PARTIAL})

    def test_mapping_values_and_body_only_support(self):
        self.reject(answer(COMPLETE), {COMPLETE: HISTORY})
        for raw in ('# ' + COMPLETE, 'title: ' + COMPLETE,
                    'description: >\n\n  ' + COMPLETE, '```text\n' + COMPLETE + '\n```',
                    '---\nsummary: ' + COMPLETE + '\n---', '> ' + COMPLETE):
            self.reject(answer(COMPLETE), {SOURCE: raw})
        self.assertIsNone(problem(answer(COMPLETE), {SOURCE: '## Contract record\n\n' + COMPLETE}))
        wrapped = COMPLETE.replace('private mount/PID/network namespaces', 'private mount/PID/network\n  namespaces')
        self.assertIsNone(problem(answer(COMPLETE), {SOURCE: wrapped}))

    def test_all_raw_citations_need_nonblank_full_bodies(self):
        for bodies in ({}, {SOURCE: ''}, {SOURCE: ' '}, {SOURCE: COMPLETE, 'sixth': ''}):
            for draft in (answer(COMPLETE), SAFE_UNKNOWN, BOUNDED_LACK):
                self.assertEqual(problem(draft, bodies), SANDBOX_CONTRACT_BODY_REASON)

    def test_raw_failure_and_qualifier_wrappers_are_veto_only(self):
        restrictions = ('Proc mount failed with errno 13.', 'No normal private proc mount exists.',
                        'AF_INET6 socket creation remains unknown.', 'The allowlist is unverified.',
                        'The external timeout is disabled.', 'Owned-process cleanup failed.', '`proc mount` failed.', 'Candidate execution remains blocked.',
                        '"proc mount": "failed".', '`socket denial`: `unknown`.',
                        'AF_INET6 socket creation is allowed.', 'Host credentials are accessible.',
                        'The allowlist includes Wine.', 'Phase: `plan`.', '"proc mount": "mystery".', '"scope": "other-scope".')
        for restriction in restrictions:
            for wrapper in ('{}', '# {}', 'summary: >\n\n  {}', '```text\n{}\n```', '```text {}\n```'):
                raw = COMPLETE + '.\n\n' + wrapper.format(restriction)
                self.reject(answer(COMPLETE), {SOURCE: raw})
                self.reject(answer(COMPLETE), {SOURCE: COMPLETE}, retrieved={SOURCE: raw})
        # The source, not the draft, binds an otherwise unbound limit.
        self.assertIsNone(problem(answer(COMPLETE), retrieved={'unbound': 'Proc mount failed.'}))
        self.assertIsNone(problem(answer(COMPLETE), retrieved={'other': '`other-method` has a failed proc mount.'}))
        self.reject(answer(COMPLETE), retrieved={'related': '`synth-method` has a failed proc mount.'})
        self.assertIsNone(problem(answer(COMPLETE), retrieved={
            'other-task': '`synth-method` has a failed proc mount for task `other-task`.'}))
        self.assertIsNone(problem(answer(COMPLETE), retrieved={
            'other-method': 'other-method has a failed proc mount.'}))

    def test_late_sixth_uncited_and_source_order_context_cannot_hide_vetoes(self):
        bodies = {str(i): COMPLETE for i in range(6)}
        bodies['5'] += '.\n\n' + ('Recorded context. ' * 300) + '\n\nProc mount failed.'
        self.reject(answer(COMPLETE), bodies)
        self.reject(answer(COMPLETE), {SOURCE: COMPLETE}, retrieved=bodies)
        bad = 'Proc mount failed.\n\n' + COMPLETE + '.\n\n' + COMPLETE.replace('`synth-method`', '`other-method`')
        self.reject(answer(COMPLETE), {SOURCE: bad})
        good = COMPLETE.replace('`synth-method`', '`other-method`') + '.\n\nProc mount failed.\n\n' + COMPLETE
        self.assertIsNone(problem(answer(COMPLETE), {SOURCE: good}))
        self.reject(answer(COMPLETE), {SOURCE: COMPLETE}, retrieved={
            '2099-01-01-other': COMPLETE.replace('`synth-method`', '`other-method`'), 'related': bad})

    def test_source_capture_labels_cannot_supply_or_replace_event_dates(self):
        self.assertIsNone(problem(answer(COMPLETE), {'2099-01-01-capture-label': COMPLETE}))
        for body in (COMPLETE.replace('2026-10-16T08:00:00Z', '2026-02-30'),
                     COMPLETE.replace('On 2026-10-16T08:00:00Z, ', ''),
                     COMPLETE.replace('On 2026-10-16T08:00:00Z, ', 'Captured: 2026-10-16T08:00:00Z\n')):
            self.reject(answer(body), {SOURCE: body})

    def test_complete_draft_selection_permission_and_unknown_tails_refuse(self):
        for tail in (' Use it now.', ' It is installed for the current candidate.',
                     ' The lead has permission.', ' This grants a new live check.'):
            self.reject(answer(COMPLETE) + tail)
            self.reject(answer(COMPLETE) + ' ' + ('Recorded context. ' * 130) + tail)
        for draft in ('No sandbox exists.', 'No sandbox method is installed.', 'No current client exists.',
                      'The proxy guarantees no socket creation.', BOUNDED_LACK + ' Use the retained root.'):
            self.reject(draft, {SOURCE: HISTORY})
        self.assertIsNone(problem(SAFE_UNKNOWN, {SOURCE: COMPLETE}))
        self.reject(SAFE_UNKNOWN, {SOURCE: COMPLETE + '. Current sandbox execution permission is approved.'})

    def test_explicit_request_roles_preserve_exact_identity_bytes(self):
        roles = {'method': 'synth-method', 'task': 'synth-task', 'app': 'synth-app',
                 'root': '/synthetic/root', 'phase': 'test', 'scope': 'offline-unit'}
        for role, identity in roles.items():
            for quote in ('`', '"', "'", ''):
                right = QUESTION + ' For ' + role + ' ' + quote + identity + quote + '?'
                wrong = right.replace(quote + identity + quote, quote + identity.upper() + quote)
                self.assertIsNone(problem(answer(COMPLETE), question=right), right)
                self.reject(answer(COMPLETE), question=wrong)
                self.assertIsNone(problem(answer(COMPLETE), question=right.replace('For ', 'For the ')))
                self.reject(answer(COMPLETE), question=wrong.replace('For ', 'For the '))
        for question in (QUESTION + ' Via `other-method`?', QUESTION + ' For `synth-task`?',
                         QUESTION + ' For task `synth-task `?', QUESTION + ' For root `/synthetic//root`?',
                         QUESTION + ' For object `synth-task`?', QUESTION + ' For action `execute`?'):
            self.reject(answer(COMPLETE), question=question)

    def test_formatted_and_plural_concepts_select_private_validation(self):
        for question in ('What `sandbox contracts` apply here?', 'What **sandbox** contract provides isolation?',
                         'Which `disposable-test` `sandbox-contract` provides isolation?',
                         'What `urgent sandbox permissions` apply here?', 'What sandbox execution gates apply here?'):
            self.assertTrue(sandbox_contract_required(question), question)
            self.reject(WRONG, {SOURCE: HISTORY}, question=question)
        modified = 'What `urgent sandbox permissions` apply here?'
        self.reject(answer(COMPLETE), question=modified)
        self.reject(BOUNDED_LACK, question=modified)
        self.assertIsNone(problem(SAFE_UNKNOWN, question=modified))

    def test_metadata_appendices_cannot_disable_an_operative_contract_clause(self):
        questions = (
            QUESTION + ' Which version is that?',
            QUESTION + ' What is the filename `synth-record`?',
            QUESTION + ' Source version: `synth-v1`.',
            'What sandbox contract version provides isolation?',
            'What `sandbox contract` version provides isolation?',
            'What sandbox contract filename provides the required isolation features?',
            'What is method sandbox-contract and what sandbox contract provides isolation?',
            'What is method sandbox-contract; what sandbox contract provides isolation?',
        )
        for question in questions:
            for prefix in ('', 'Currently, '):
                question = prefix + question
                self.assertTrue(sandbox_contract_required(question), question)
                self.reject(WRONG, {SOURCE: HISTORY}, question=question)

    def test_metadata_only_contract_names_do_not_supply_operative_intent(self):
        for question in ('What is the sandbox contract version?',
                         'What sandbox contract filename is installed?',
                         'What is the title of the sandbox contract?',
                         'What is the version of method sandbox-contract?',
                         'What `sandbox permissions` version is installed?',
                         'What is method sandbox-contract? Which version is that?'):
            for prefix in ('', 'Currently, '):
                self.assertFalse(sandbox_contract_required(prefix + question), prefix + question)
                self.assertIsNone(problem('The record contains metadata.', question=prefix + question))

    def test_identity_words_cannot_supply_intent_or_force_private_collection(self):
        for question in ('What is the filename `sandbox-contract`?', 'What does method `sandbox-contract` mean?',
                         'What is the version of `current sandbox permissions`?',
                         'Which `sandbox-contract` method is installed?',
                         'Can method `sandbox contract provides execution` read metadata?',
                         'What is `/opt/sandbox-contract`?', 'What is the method sandbox-contract?',
                         'What is method Devbox sandbox contract?',
                         'Can method sandbox-contract-provides-execution read metadata?',
                         'Can a project lead close a ticket?'):
            self.assertFalse(sandbox_contract_required(question), question)
            self.assertIsNone(problem(WRONG, question=question))


if __name__ == '__main__':
    unittest.main()

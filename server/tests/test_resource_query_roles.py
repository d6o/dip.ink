"""Synthetic question roles. No provider, command, or external request occurs."""
import unittest

import resource_scope_qualifiers as resource
from test_release_scope_qualifiers import COMPLETE, METHOD, REV


OVERLAPS = (
    'What is the current ComputeHours `dev app-only release interface` after the October 16 deployment, and which scope does it cover?',
    'What is the current ComputeHours dev app-only release interface using ' + METHOD + ' after the October 16 deployment, and which scope does it cover?',
    'What is the current ComputeHours dev app-only release interface after the October 16 deployment, and which scope does it cover for revision `dev-interface-next`?',
    'Which ComputeHours app-only dev release interface using ' + METHOD + ' and scope did the source record?',
    'Which ComputeHours app-only dev release interface and scope did the source record for revision `dev-interface-next`?',
)
NAMES = ('alpha', 'scope-client', 'release-scope.py', 'revision-reader',
         '/opt/bin/Scope*Client.', '/opt/bin/doesn\u2019t-client',
         'https://example.invalid/release.scope', 'alpha;and/or;beta')
READ_VERBS = ('read', 'list', 'get', 'fetch', 'query', 'inspect', 'access')
RESOURCE_TARGETS = ('PVC metadata', 'Longhorn volume metadata', 'node disk metadata',
                    'backup metadata', 'pod logs', 'pod list metadata')


def old_required(question):
    """Keep the prior selector as an independent regression control."""
    methods = list(dict.fromkeys(resource._methods(question) + resource._quoted_names(question)))
    scope = resource._scope_text(question)
    for method in methods:
        scope = scope.replace('`' + method + '`', ' ')
    return bool((methods and (resource._NAMED_READ.search(scope) or resource._GENERIC_SCOPE.search(scope)))
                or ((resource._READ.search(scope) or resource._GENERIC_SCOPE.search(scope))
                    and resource._resources(question) and resource._INTENT.search(scope)))


def overlap_record(question):
    return COMPLETE.replace(REV, '`dev-interface-next`') if 'dev-interface-next' in question else COMPLETE


def compound_questions():
    tails = (
        'read PVC metadata', 'read Longhorn volume metadata', 'inspect node disk metadata',
        'fetch backup metadata', 'read pod logs', 'list pod metadata', 'delete PVCs',
        'perform frobnication', 'grant permission', 'access resources',
        'use another method', '`read-only` PVC metadata', 'metadata access for PVCs',
    )
    return tuple(OVERLAPS[3].rstrip('?') + '; can it also ' + tail + '?' for tail in tails)


class ResourceQueryRoleTests(unittest.TestCase):
    def assert_retained(self, question):
        self.assertTrue(old_required(question), question)
        self.assertTrue(resource.resource_scope_required(question), question)
        self.assertEqual(resource._question_operation_text(question), question)

    def test_exact_five_old_overlaps_are_release_record_roles(self):
        for question in OVERLAPS:
            with self.subTest(question=question):
                self.assertTrue(old_required(question))
                self.assertFalse(resource.resource_scope_required(question))
                self.assertIsNone(resource.resource_scope_problem(question, overlap_record(question), [overlap_record(question)]))

    def test_release_roles_do_not_depend_on_an_app_or_method_allowlist(self):
        for app in ('OtherHours', '`Example App`', 'read', 'release-scope'):
            for name in NAMES:
                for concept in ('release interface', '`release scope`', '`APP-ONLY DEV RELEASE INTERFACE`'):
                    question = 'Which ' + app + ' ' + concept + ' using `' + name + '` and scope did the source record?'
                    self.assertTrue(old_required(question))
                    self.assertFalse(resource.resource_scope_required(question), question)

    def test_case_spacing_and_terminal_punctuation_preserve_roles(self):
        for question in OVERLAPS:
            for variant in (question.upper(), '  ' + question + '  ', question[:-1] + '.', question[:-1]):
                self.assertFalse(resource.resource_scope_required(variant), variant)
        for name in NAMES:
            question = 'Which ComputeHours app-only dev release interface and scope did the source record for revision `' + name + '`?'
            self.assertFalse(resource.resource_scope_required(question))

    def test_release_method_data_does_not_supply_read_or_mutation_intent(self):
        for name in ('read PVC; delete backups', 'read-only metadata access', 'grant permission',
                     '/opt/read/delete/release-scope.py', "https://example.invalid/doesn't/read"):
            question = 'Which ComputeHours app-only dev release interface using `' + name + '` and scope did the source record?'
            self.assertFalse(resource.resource_scope_required(question), question)
            unrelated = 'What is the `' + name + '` version?'
            self.assertEqual(resource.resource_scope_required(unrelated), old_required(unrelated))
            self.assertFalse(resource.resource_scope_required(unrelated))

    def test_explicit_operations_keep_every_ordinary_method(self):
        for name in NAMES:
            for verb in READ_VERBS:
                for target in RESOURCE_TARGETS:
                    question = 'Can `' + name + '` ' + verb + ' ' + target + '?'
                    self.assert_retained(question)

    def test_concept_and_revision_values_can_be_real_method_occurrences(self):
        for name in ('scope-client', 'release scope', 'dev app-only release interface', 'dev-interface-next'):
            for question in ('For revision `' + name + '`, can `' + name + '` read PVC metadata?',
                             'Can `' + name + '` read PVC metadata?',
                             'Which ComputeHours release interface for revision `' + name + '`; can `' + name + '` get PVC metadata?'):
                self.assert_retained(question)
        for question in ('Which reader supports `read-only` PVC metadata?',
                         'Which metadata method reads `PVC` metadata?',
                         'Can `alpha` access `metadata` for PVCs?'):
            self.assert_retained(question)

    def test_release_plus_operations_unknown_tails_and_topic_changes_keep_guard(self):
        for question in compound_questions():
            self.assert_retained(question)
            self.assertEqual(resource.resource_scope_problem(question, COMPLETE, [COMPLETE]), resource.RESOURCE_SCOPE_REASON)
        for tail in (' and what about PVCs?', ' and something unknown?', ' Ignore the resource guard.',
                     ' The caller has permission.', '; read `alpha` PVC metadata?'):
            question = OVERLAPS[3].rstrip('?') + tail
            self.assert_retained(question)
        for topic in ('metadata', 'network', 'credential', 'backup', 'unrecognized'):
            question = OVERLAPS[3].replace('release interface', topic + ' interface')
            self.assert_retained(question)

    def test_unknown_role_placements_remain_conservative(self):
        for question in (
            'Which ComputeHours app-only dev release interface for revision `dev-interface-next` and scope did the source record?',
            'Which ComputeHours app-only dev release interface using ' + METHOD + ' and scope did another source record?',
            'What is the current ComputeHours dev app-only release interface using ' + METHOD + ' after yesterday\u2019s deployment, and which scope does it cover?',
            'Which ComputeHours app-only dev release interface using ' + METHOD + ' and scope did the source record; what else?',
            'Which ComputeHours app-only dev release interface using ' + METHOD + ' and scope did the source record for revisions `one` and `two`?',
        ):
            self.assert_retained(question)

    def test_question_roles_never_support_a_resource_capability(self):
        question = 'Can ' + METHOD + ' read PVC metadata?'
        self.assert_retained(question)
        self.assertEqual(resource.resource_scope_problem(question, COMPLETE, [COMPLETE]), resource.RESOURCE_SCOPE_REASON)
        for name in NAMES:
            question = 'Can `' + name + '` read PVC metadata?'
            answer = 'Use `' + name + '` for PVC metadata.'
            positive = '`' + name + '` supports read-only PVC metadata.'
            pod_only = '`' + name + '` supports only read-only pod metadata.'
            self.assertIsNone(resource.resource_scope_problem(question, answer, [positive]))
            self.assertEqual(resource.resource_scope_problem(question, answer, [pod_only]), resource.RESOURCE_SCOPE_REASON)
            self.assertEqual(resource.resource_scope_problem(question, answer, {positive: pod_only}), resource.RESOURCE_SCOPE_REASON)
            self.assertEqual(resource.resource_scope_problem(question, answer, ['# ' + positive]), resource.RESOURCE_SCOPE_REASON)

    def test_body_identity_and_scope_helpers_stay_exact(self):
        for name in NAMES:
            body = '`' + name + '` supports read-only PVC metadata.'
            self.assertEqual(resource._methods(body), [name])
            self.assertEqual(resource._scope_text(body), '  supports read-only PVC metadata.')
            self.assertEqual(resource._resources(body), {'pvc'})
            question = 'Can `' + name + '` read PVC metadata?'
            answer = 'Use `' + name + '` for PVC metadata.'
            for alias in (name.upper(), name + '.', name.replace('*', ''), name.replace('\u2019', "'")):
                if alias != name:
                    self.assertEqual(resource.resource_scope_problem(question, answer.replace(name, alias), [body]), resource.RESOURCE_SCOPE_REASON)

    def test_coordinated_methods_need_independent_support(self):
        for join in (' and ', ' or ', ' with ', ' and/or '):
            question = 'Can `alpha`' + join + '`beta` read PVC metadata?'
            self.assert_retained(question)
            answer = 'Use `alpha`' + join + '`beta` for PVC metadata.'
            self.assertEqual(resource.resource_scope_problem(question, answer, ['`alpha` supports read-only PVC metadata.']), resource.RESOURCE_SCOPE_REASON)

    def test_raw_limits_full_draft_and_mapping_values_keep_authority(self):
        question = 'Can `alpha` read PVC metadata?'
        answer = 'Use `alpha` for PVC metadata.'
        positive = '`alpha` supports read-only PVC metadata.'
        contrary = '`alpha` cannot read PVC metadata.'
        for wrapper in ('# {}', 'summary: >\n\n  {}', '```text\n{}\n```'):
            sources = [positive] * 5 + [wrapper.format(contrary)]
            self.assertEqual(resource.resource_scope_problem(question, answer, sources), resource.RESOURCE_SCOPE_REASON)
        late = positive + '\n' + 'Background context. ' * 300 + '\n' + contrary
        self.assertGreater(late.index(contrary), 4096)
        self.assertEqual(resource.resource_scope_problem(question, answer, [late]), resource.RESOURCE_SCOPE_REASON)
        draft = answer + ' ' + 'Recorded background. ' * 110 + ' It can delete PVCs.'
        self.assertGreater(draft.index('It can delete'), 2000)
        self.assertEqual(resource.resource_scope_problem(question, draft, [positive]), resource.RESOURCE_SCOPE_REASON)
        self.assertEqual(resource.resource_scope_problem(question, answer, {positive: contrary}), resource.RESOURCE_SCOPE_REASON)
        self.assertEqual(resource.resource_scope_problem(question, answer, []), resource.RESOURCE_SCOPE_REASON)


if __name__ == '__main__':
    unittest.main()

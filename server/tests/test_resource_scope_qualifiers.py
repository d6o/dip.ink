"""Synthetic scope checks. No reader, endpoint, credential, or provider access."""
import unittest

from resource_scope_qualifiers import (
    RESOURCE_SCOPE_REASON,
    resource_scope_problem,
    resource_scope_required,
)

QUESTION = (
    "What supported read-only Longhorn metadata channel does the k3s lead use "
    "from the Devbox for volume, replica, node disk capacity and backup metadata, "
    "without importing or widening devbox-kube-read?"
)
BASE = "devbox-kube-read"
BASE_SCOPE = (
    "`devbox-kube-read` is the installed supported read-only method for Kubernetes workload metadata. "
    "`devbox-kube-read` supports only pod/deployment/statefulset metadata. "
    "It admits narrow pod-list/log shapes. Actual logs fail binding_window_unsupported. "
    "It has no Longhorn, PV/PVC, node-disk or backup operation."
)
# This is synthetic source text, not a claim about an installed channel.
SUPPORT = (
    "`scope-reader` is the supported installed read-only method for Longhorn "
    "volume, replica, node disk capacity and backup metadata."
)
POSITIVE = "Use `scope-reader` for Longhorn volume, replica, node disk capacity and backup metadata."
BOUNDARY = "`devbox-kube-read` supports only pod/deployment/statefulset metadata."
BOUNDED_NEGATIVE = (
    "The cited text does not establish a supported Longhorn metadata channel "
    "for `devbox-kube-read`."
)


class IntentTests(unittest.TestCase):
    def test_exact_question_requires_scope_check(self):
        self.assertTrue(resource_scope_required(QUESTION))

    def test_explicit_resource_method_questions(self):
        for question in (
            "Which supported method reads Longhorn volume metadata?",
            "What read-only endpoint lists backup metadata?",
            "Which reader supports PV and PVC metadata?",
            "Which tool supports node disk metadata?",
            "Can devbox-kube-read read workload metadata?",
            "What resource kinds does devbox-kube-read support?",
            "Which tool supports pod logs?",
        ):
            with self.subTest(question=question):
                self.assertTrue(resource_scope_required(question))

    def test_unrelated_questions_pass_without_scope_check(self):
        for question in (
            "What is the replica count?",
            "Where is the backup?",
            "What is Longhorn?",
            "What is the current devbox-kube-read version?",
            "Where is /home/example/bin/devbox-kube-read?",
            "Which color does the page use?",
            "How does a volume work?",
            "What is the approved method for live VaultDesk credential reads?",
        ):
            with self.subTest(question=question):
                self.assertFalse(resource_scope_required(question))
                self.assertIsNone(resource_scope_problem(question, "Unchanged answer.", []))


class ScopeSupportTests(unittest.TestCase):
    def problem(self, answer, *texts, question=QUESTION):
        return resource_scope_problem(question, answer, list(texts))

    def reject(self, answer, *texts, question=QUESTION):
        self.assertEqual(self.problem(answer, *texts, question=question), RESOURCE_SCOPE_REASON)

    def test_generic_metadata_does_not_support_longhorn(self):
        self.reject("Use `devbox-kube-read` for Longhorn volume, replica, node disk and backup metadata.", BASE_SCOPE)
        self.reject("The supported channel is `devbox-kube-read`.", BASE_SCOPE)
        self.reject("devbox-kube-read is the supported installed channel for Longhorn metadata.", BASE_SCOPE)

    def test_plain_and_path_names_cannot_hide_transfer(self):
        for method in (BASE, "`" + BASE + "`", "/home/example/bin/devbox-kube-read"):
            with self.subTest(method=method):
                self.reject("Use " + method + " for Longhorn metadata.", BASE_SCOPE)

    def test_supported_scoped_synthetic_method_passes(self):
        self.assertIsNone(self.problem(POSITIVE, SUPPORT))
        self.assertIsNone(self.problem(POSITIVE, SUPPORT, BASE_SCOPE))

    def test_unscoped_selection_needs_every_requested_resource(self):
        self.assertIsNone(self.problem("Use `scope-reader`.", SUPPORT))
        self.assertIsNone(self.problem("`scope-reader` is the supported channel for Longhorn metadata.", SUPPORT))
        for removed in ("volume, ", "replica, ", "node disk capacity and ", "backup "):
            with self.subTest(removed=removed):
                self.reject("Use `scope-reader`.", SUPPORT.replace(removed, ""))
                self.reject("Use `scope-reader` for Longhorn metadata.", SUPPORT.replace(removed, ""))

    def test_explicit_resource_subset_can_use_exact_source_scope(self):
        answer = "Use `scope-reader` for Longhorn volume metadata."
        source = "`scope-reader` is the supported installed read-only method for Longhorn volume metadata."
        self.assertIsNone(self.problem(answer, source))
        self.reject(POSITIVE, source)

    def test_missing_resource_cannot_transfer_between_sources(self):
        self.reject(POSITIVE,
                    SUPPORT.replace("replica, ", ""),
                    "`scope-reader` is the supported read-only method for Longhorn replica metadata.")

    def test_missing_resource_cannot_transfer_between_clauses(self):
        self.reject(POSITIVE,
                    "`scope-reader` is the supported read-only method for Longhorn volume metadata. "
                    "It also reads replica, node disk capacity and backup metadata.")
        self.reject(POSITIVE,
                    "Longhorn volume, replica, node disk capacity and backup metadata need a channel. "
                    "`scope-reader` is the supported read-only method.")

    def test_separate_method_cannot_supply_base_reader_scope(self):
        answer = "Use `devbox-kube-read` for Longhorn volume, replica, node disk and backup metadata."
        self.reject(answer, BASE_SCOPE, SUPPORT)
        self.reject(answer, BASE_SCOPE + " " + SUPPORT)
        self.reject("Use `devbox-kube-read`.", SUPPORT)
        self.assertIsNone(self.problem(POSITIVE, BASE_SCOPE, SUPPORT))

    def test_resource_scope_cannot_transfer_between_methods_in_one_clause(self):
        source = SUPPORT.replace("`scope-reader`", "`scope-reader` with `other-reader`")
        self.reject(POSITIVE, source)
        self.reject("Use `scope-reader` with `other-reader` for Longhorn metadata.", SUPPORT)

    def test_endpoint_and_tool_are_distinct_identities(self):
        source = "`/api/volumes` is the supported read-only endpoint for Longhorn volume metadata."
        self.assertIsNone(self.problem("Use `/api/volumes` for Longhorn volume metadata.", source))
        self.reject("Use `scope-reader` for Longhorn volume metadata.", source)
        self.reject("Use `/api/backups` for Longhorn volume metadata.", source)

    def test_different_paths_are_not_the_same_method(self):
        source = SUPPORT.replace("`scope-reader`", "`/opt/approved/scope-reader`")
        self.reject(POSITIVE, source)
        self.reject(POSITIVE.replace("scope-reader", "/tmp/scope-reader"), source)

    def test_draft_fixture_and_source_only_do_not_establish_capability(self):
        for modifier in ("draft", "fixture", "example", "stub", "source-only", "source code", "reviewed source", "planned", "unverified", "not", "may", "if"):
            with self.subTest(modifier=modifier):
                self.reject(POSITIVE, SUPPORT.replace("is the", "is the " + modifier))

    def test_capability_modifiers_need_same_source(self):
        for modifier in ("installed", "live", "native", "approved"):
            with self.subTest(modifier=modifier):
                answer = POSITIVE.replace("Use", "Use the " + modifier)
                if modifier == "installed":
                    self.assertIsNone(self.problem(answer, SUPPORT))
                else:
                    self.reject(answer, SUPPORT)
                answer = "`scope-reader` is the " + modifier + " supported channel for Longhorn metadata."
                if modifier != "installed":
                    self.reject(answer, SUPPORT)
                self.assertIsNone(self.problem(answer, SUPPORT.replace("supported installed", "supported installed " + modifier)))

    def test_read_only_does_not_transfer_from_another_source(self):
        self.reject(POSITIVE, SUPPORT.replace("read-only ", ""), "The metadata pass is read-only.")

    def test_no_affirmative_capability_does_not_supply_support(self):
        self.reject(POSITIVE, SUPPORT.replace("supported installed ", ""))
        self.reject(POSITIVE, "Longhorn scope-reader supported installed read-only volume replica node disk backup metadata")

    def test_method_name_cannot_supply_scope_or_capability(self):
        source = "`longhorn-volume-replica-disk-backup-reader` is the supported read-only method for metadata."
        answer = "Use `longhorn-volume-replica-disk-backup-reader` for Longhorn metadata."
        self.reject(answer, source)
        source = "`supported-reader` reads read-only Longhorn volume metadata."
        self.reject("Use `supported-reader` for Longhorn volume metadata.", source)

    def test_source_label_without_a_direct_predicate_cannot_supply_capability(self):
        self.reject(POSITIVE, SUPPORT.replace(" is the ", ": "))
        self.reject(POSITIVE, SUPPORT.replace(" is the ", " "))

    def test_resource_text_before_a_different_scope_binding_cannot_transfer(self):
        source = (
            "Longhorn volume, replica, node disk capacity and backup metadata are requested "
            "because `scope-reader` is the supported read-only method for pod metadata."
        )
        self.reject(POSITIVE, source)

    def test_direct_support_verb_can_supply_scoped_support(self):
        source = SUPPORT.replace("is the supported installed read-only method for", "supports read-only")
        self.assertIsNone(self.problem(POSITIVE, source))

    def test_generic_disk_scope_does_not_establish_node_disk_capacity(self):
        question = 'Which supported read-only method reads node disk capacity metadata?'
        answer = 'Use `disk-reader` for node disk capacity metadata.'
        for scope in ('disk', 'disk capacity', 'node disk'):
            source = '`disk-reader` is the supported read-only method for ' + scope + ' metadata.'
            self.reject(answer, source, question=question)
        source = '`disk-reader` is the supported read-only method for node disk capacity metadata.'
        self.assertIsNone(self.problem(answer, source, question=question))

    def test_empty_and_non_answer_fail_closed(self):
        self.reject("", SUPPORT)
        self.reject("See the source.", SUPPORT)
        self.reject(POSITIVE)

    def test_error_reason_has_no_source_or_answer_content(self):
        self.reject("Use `private-placeholder` for Longhorn metadata.", "PRIVATE PLACEHOLDER")
        self.assertNotIn("private", RESOURCE_SCOPE_REASON.casefold())


class ContraryScopeTests(unittest.TestCase):
    def test_every_source_can_supply_a_contrary_limit(self):
        contrary = "`scope-reader` supports only pod/deployment/statefulset metadata."
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT, contrary]), RESOURCE_SCOPE_REASON)
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, [contrary, SUPPORT]), RESOURCE_SCOPE_REASON)
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT + "\n" + contrary]), RESOURCE_SCOPE_REASON)

    def test_source_after_five_other_sources_still_limits_scope(self):
        sources = [SUPPORT] * 5 + ["`scope-reader` has no Longhorn operation."]
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, sources), RESOURCE_SCOPE_REASON)

    def test_plain_method_limit_with_only_before_supports_is_preserved(self):
        source = "scope-reader only supports pod metadata."
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT, source]), RESOURCE_SCOPE_REASON)
        source = "Longhorn metadata is unsupported by scope-reader."
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT, source]), RESOURCE_SCOPE_REASON)

    def test_explicit_same_method_unavailability_vetoes_a_positive_source(self):
        for condition in ('unavailable', 'unsupported', 'not installed', 'not approved'):
            with self.subTest(condition=condition):
                contrary = '`scope-reader` is ' + condition + '.'
                self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT, contrary]), RESOURCE_SCOPE_REASON)
        self.assertIsNone(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT, '`other-reader` is unavailable.']))

    def test_explicit_global_resource_absence_vetoes_a_positive_source(self):
        absent = 'No supported Longhorn metadata channel exists.'
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT, absent]), RESOURCE_SCOPE_REASON)
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT] * 5 + [absent]), RESOURCE_SCOPE_REASON)

    def test_unrelated_method_limit_does_not_remove_direct_support(self):
        self.assertIsNone(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT, BASE_SCOPE]))
        self.assertIsNone(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT, "`other-reader` has no Longhorn operation."]))

    def test_partial_resource_denial_blocks_full_selection(self):
        for resource in ("volume", "replica", "node disk", "backup", "Longhorn"):
            with self.subTest(resource=resource):
                denial = "`scope-reader` has no " + resource + " operation."
                self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, [SUPPORT, denial]), RESOURCE_SCOPE_REASON)

    def test_logs_failure_blocks_a_prior_positive_scope(self):
        question = "What supported read-only method reads pod logs?"
        source = (
            "`pod-reader` is the supported installed read-only method for pod logs. "
            "`pod-reader` supports only pod/deployment/statefulset metadata. "
            "Actual logs fail binding_window_unsupported."
        )
        answer = "Use `pod-reader` for pod logs."
        self.assertEqual(resource_scope_problem(question, answer, [source]), RESOURCE_SCOPE_REASON)

    def test_kind_limit_does_not_widen_or_remove_explicit_pod_list_scope(self):
        question = "What supported read-only method lists pod metadata?"
        source = (
            "`pod-reader` is the supported read-only method for a narrow pod list. "
            "`pod-reader` supports only pod/deployment/statefulset metadata."
        )
        self.assertIsNone(resource_scope_problem(question, "Use `pod-reader` for a narrow pod list.", [source]))
        self.assertEqual(resource_scope_problem(question, "Use `pod-reader` for a node list.", [source]), RESOURCE_SCOPE_REASON)

    def test_narrow_list_shape_cannot_supply_a_general_list_claim(self):
        question = "What supported read-only method lists pod metadata?"
        source = "`pod-reader` supports only narrow read-only pod list metadata."
        answer = "Use `pod-reader` for a pod list."
        self.assertEqual(resource_scope_problem(question, answer, [source]), RESOURCE_SCOPE_REASON)
        self.assertIsNone(resource_scope_problem(question, "Use `pod-reader` for a narrow pod list.", [source]))

    def test_named_pod_scope_cannot_supply_an_unrestricted_pod_claim(self):
        question = "Which supported read-only method reads pod metadata?"
        source = "`pod-reader` is the supported read-only method for named pod metadata."
        self.assertEqual(resource_scope_problem(question, "Use `pod-reader` for pod metadata.", [source]), RESOURCE_SCOPE_REASON)
        self.assertIsNone(resource_scope_problem(question, "Use `pod-reader` for named pod metadata.", [source]))

    def test_narrow_list_scope_does_not_establish_logs(self):
        question = "What supported read-only method reads pod logs?"
        source = "`pod-reader` supports only read-only pod list metadata."
        self.assertEqual(resource_scope_problem(question, "Use `pod-reader` for pod logs.", [source]), RESOURCE_SCOPE_REASON)

    def test_pv_pvc_node_disk_and_backups_are_not_workload_scope(self):
        for resource in ("PV", "PVC", "persistent volumes", "persistent volume claims", "node disk", "backup", "replica", "volume"):
            question = "Which supported read-only method reads " + resource + " metadata?"
            answer = "Use `devbox-kube-read` for " + resource + " metadata."
            with self.subTest(resource=resource):
                self.assertEqual(resource_scope_problem(question, answer, [BASE_SCOPE]), RESOURCE_SCOPE_REASON)

    def test_pv_and_pvc_do_not_transfer(self):
        for source_kind, answer_kind in (("PV", "PVC"), ("PVC", "PV"), ("persistent volume claims", "volume")):
            source = "`pv-reader` is the supported read-only method for " + source_kind + " metadata."
            question = "Which supported read-only method reads " + answer_kind + " metadata?"
            answer = "Use `pv-reader` for " + answer_kind + " metadata."
            self.assertEqual(resource_scope_problem(question, answer, [source]), RESOURCE_SCOPE_REASON)


class BatchResourceScopeTests(unittest.TestCase):
    # Synthetic static text. These tests do not inspect an installed reader.
    BASE_BATCH = BASE_SCOPE + ' `devbox-kube-read` has no CronJobs or Jobs metadata operation.'

    def test_job_and_cronjob_questions_require_scope_checks(self):
        for kind in ('Job', 'Jobs', 'CronJob', 'CronJobs', 'Cron-Job', 'Cron Job'):
            with self.subTest(kind=kind):
                self.assertTrue(resource_scope_required('Which supported metadata method reads ' + kind + ' metadata?'))

    def test_base_scope_and_generic_metadata_do_not_establish_batch_scope(self):
        generic = '`devbox-kube-read` is the supported read-only method for Kubernetes metadata.'
        for kind in ('Jobs', 'CronJobs'):
            question = 'Which supported read-only method reads ' + kind + ' metadata?'
            answer = 'Use `devbox-kube-read` for ' + kind + ' metadata.'
            for body in (generic, BOUNDARY, self.BASE_BATCH):
                with self.subTest(kind=kind, body=body):
                    self.assertEqual(resource_scope_problem(question, answer, [body]), RESOURCE_SCOPE_REASON)

    def test_static_base_batch_negatives_remain_source_limited(self):
        for kind in ('Jobs', 'CronJobs'):
            question = 'Which supported read-only method reads ' + kind + ' metadata?'
            answer = '`devbox-kube-read` has no ' + kind + ' metadata operation.'
            self.assertIsNone(resource_scope_problem(question, answer, [self.BASE_BATCH]))
            bounded = 'The cited text does not establish a supported ' + kind + ' method for `devbox-kube-read`.'
            self.assertIsNone(resource_scope_problem(question, bounded, [self.BASE_BATCH]))

    def test_job_and_cronjob_capabilities_do_not_transfer(self):
        for source_kind, answer_kind in (('Job', 'CronJob'), ('CronJob', 'Job')):
            question = 'Which supported read-only method reads ' + answer_kind + ' metadata?'
            answer = 'Use `batch-reader` for ' + answer_kind + ' metadata.'
            body = '`batch-reader` is the supported read-only method for ' + source_kind + ' metadata.'
            self.assertEqual(resource_scope_problem(question, answer, [body]), RESOURCE_SCOPE_REASON)

    def test_direct_synthetic_batch_scope_can_pass(self):
        for kind in ('Job', 'CronJob', 'Cron-Job', 'Cron Job'):
            question = 'Which supported read-only method reads ' + kind + ' metadata?'
            answer = 'Use `batch-reader` for ' + kind + ' metadata.'
            body = '`batch-reader` is the supported read-only method for ' + kind + ' metadata.'
            self.assertIsNone(resource_scope_problem(question, answer, [body]))

    def test_longhorn_rest_scope_does_not_supply_batch_scope(self):
        for kind in ('Jobs', 'CronJobs'):
            question = 'Which supported read-only method reads ' + kind + ' metadata?'
            answer = 'Use `devbox-kube-read` for ' + kind + ' metadata.'
            self.assertEqual(resource_scope_problem(question, answer, [self.BASE_BATCH, SUPPORT]), RESOURCE_SCOPE_REASON)
            self.assertEqual(resource_scope_problem(question, answer.replace('devbox-kube-read', 'scope-reader'),
                                                   [self.BASE_BATCH, SUPPORT]), RESOURCE_SCOPE_REASON)

    def test_yaml_intent_does_not_establish_a_live_batch_method(self):
        question = 'Which supported read-only method reads CronJob metadata?'
        answer = 'Use the installed live `batch-reader` for CronJob metadata.'
        body = ('`batch-reader` is the supported installed live read-only method for CronJob '
                'metadata in a public YAML intent.')
        self.assertEqual(resource_scope_problem(question, answer, [body]), RESOURCE_SCOPE_REASON)
        intent = ('Public CronJob YAML intent does not establish rendered state, live adoption, '
                  'or a failure phase.')
        self.assertEqual(resource_scope_problem(question, answer, [intent]), RESOURCE_SCOPE_REASON)

    def test_batch_mapping_values_not_keys_supply_scope(self):
        question = 'Which supported read-only method reads CronJob metadata?'
        answer = 'Use `batch-reader` for CronJob metadata.'
        body = '`batch-reader` is the supported read-only method for CronJob metadata.'
        self.assertIsNone(resource_scope_problem(question, answer, {'source': body}))
        self.assertEqual(resource_scope_problem(question, answer, {body: 'Public YAML intent only.'}), RESOURCE_SCOPE_REASON)

    def test_sixth_batch_denial_defeats_positive_support(self):
        question = 'Which supported read-only method reads CronJob metadata?'
        answer = 'Use `batch-reader` for CronJob metadata.'
        body = '`batch-reader` is the supported read-only method for CronJob metadata.'
        denied = '`batch-reader` has no CronJob metadata operation.'
        self.assertEqual(resource_scope_problem(question, answer, [body] * 5 + [denied]), RESOURCE_SCOPE_REASON)

    def test_yaml_location_is_not_a_method_question(self):
        question = 'Where is the public CronJob YAML?'
        self.assertFalse(resource_scope_required(question))
        self.assertIsNone(resource_scope_problem(question, 'Unchanged location.', []))


class NegativeScopeTests(unittest.TestCase):
    def test_actual_base_boundary_can_pass(self):
        self.assertIsNone(resource_scope_problem(QUESTION, BOUNDARY, [BASE_SCOPE]))
        self.assertIsNone(resource_scope_problem(QUESTION, BOUNDARY + " " + BOUNDED_NEGATIVE, [BASE_SCOPE]))

    def test_source_limited_negative_can_distinguish_a_separate_method(self):
        self.assertIsNone(resource_scope_problem(QUESTION, BOUNDED_NEGATIVE, [BASE_SCOPE, SUPPORT]))
        answer = BOUNDED_NEGATIVE + " " + POSITIVE
        self.assertIsNone(resource_scope_problem(QUESTION, answer, [BASE_SCOPE, SUPPORT]))

    def test_source_limited_negative_does_not_hide_existing_same_method_support(self):
        answer = "The cited text does not establish a supported Longhorn metadata channel for `scope-reader`."
        self.assertEqual(resource_scope_problem(QUESTION, answer, [SUPPORT]), RESOURCE_SCOPE_REASON)
        answer = "The cited evidence contains no supported Longhorn metadata channel."
        self.assertEqual(resource_scope_problem(QUESTION, answer, [BASE_SCOPE, SUPPORT]), RESOURCE_SCOPE_REASON)

    def test_source_limited_negative_needs_related_readable_evidence(self):
        self.assertEqual(resource_scope_problem(QUESTION, BOUNDED_NEGATIVE, []), RESOURCE_SCOPE_REASON)
        self.assertEqual(resource_scope_problem(QUESTION, BOUNDED_NEGATIVE, ["The weather is clear."]), RESOURCE_SCOPE_REASON)

    def test_supported_source_limit_can_establish_a_negative_resource_scope(self):
        answer = "`devbox-kube-read` has no Longhorn operation."
        self.assertIsNone(resource_scope_problem(QUESTION, answer, [BASE_SCOPE]))
        self.assertIsNone(resource_scope_problem(QUESTION, answer, [BOUNDARY]))
        self.assertEqual(resource_scope_problem(QUESTION, answer, [SUPPORT]), RESOURCE_SCOPE_REASON)

    def test_failed_logs_qualifier_preserves_the_exact_error(self):
        answer = BOUNDARY + " Actual logs fail binding_window_unsupported."
        self.assertIsNone(resource_scope_problem(QUESTION, answer, [BASE_SCOPE]))
        source = BASE_SCOPE.replace("binding_window_unsupported", "another_error")
        self.assertEqual(resource_scope_problem(QUESTION, answer, [source]), RESOURCE_SCOPE_REASON)

    def test_negative_cannot_hide_an_unsupported_recommendation(self):
        answer = BOUNDED_NEGATIVE + " Use `devbox-kube-read` for Longhorn metadata."
        self.assertEqual(resource_scope_problem(QUESTION, answer, [BASE_SCOPE]), RESOURCE_SCOPE_REASON)

    def test_unsourced_universal_negative_does_not_pass(self):
        answer = "No supported Longhorn metadata method exists."
        self.assertEqual(resource_scope_problem(QUESTION, answer, [BASE_SCOPE]), RESOURCE_SCOPE_REASON)

    def test_claimed_boundary_cannot_drop_other_allowed_kinds(self):
        answer = "`devbox-kube-read` supports only pod metadata."
        self.assertEqual(resource_scope_problem(QUESTION, answer, [BASE_SCOPE]), RESOURCE_SCOPE_REASON)


class MappingEvidenceTests(unittest.TestCase):
    def test_values_not_keys_supply_positive_evidence(self):
        self.assertIsNone(resource_scope_problem(QUESTION, POSITIVE, {"note": SUPPORT}))
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, {SUPPORT: "Metadata only."}), RESOURCE_SCOPE_REASON)

    def test_values_not_keys_supply_negative_evidence(self):
        self.assertIsNone(resource_scope_problem(QUESTION, BOUNDED_NEGATIVE, {"note": BASE_SCOPE}))
        self.assertEqual(resource_scope_problem(QUESTION, BOUNDED_NEGATIVE, {BASE_SCOPE: "Metadata only."}), RESOURCE_SCOPE_REASON)

    def test_sixth_mapping_value_can_supply_a_conflict(self):
        sources = {"source-" + str(i): SUPPORT for i in range(5)}
        sources["source-5"] = "`scope-reader` supports only pod metadata."
        self.assertEqual(resource_scope_problem(QUESTION, POSITIVE, sources), RESOURCE_SCOPE_REASON)


if __name__ == "__main__":
    unittest.main()

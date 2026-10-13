"""Synthetic credential-method regressions; no dependencies or service calls."""
import unittest

from credential_method_qualifiers import (
    CREDENTIAL_METHOD_REASON,
    credential_method_problem,
    credential_services,
)

EXACT_QUESTION = (
    "What is the current approved exact-User-Agent method for live Infisical "
    "credential reads in Devbox, and what remains blocked for devbox#14?"
)
QUESTION = "What is the current approved method for live VaultDesk credential reads?"
METHOD = "vault-reader"
POSITIVE = "Use `vault-reader` for live VaultDesk credential reads."
SUPPORT = "`vault-reader` is the supported installed live method for VaultDesk credential reads."
ABSENT = "VaultDesk has no approved installed live credential-read method."
METADATA = (
    "devbox-kube-read is the installed supported method for named Kubernetes workload metadata. "
    "Its exact-User-Agent fixture passes. It supports no Secret, exec, optionalConfigMap data, or provider operations. "
    "Its internal credential loader is not a supported fallback."
)
INFISICAL_ABSENT = (
    "Infisical has no approved installed live credential-read method. "
    "The Infisical source audit and reviewed source-only draft do not establish a native installed helper. "
    "The complete compiled closure and live header proof remain absent."
)


class ServiceIntentTests(unittest.TestCase):
    def test_exact_query_scopes_infisical_not_devbox(self):
        self.assertEqual(credential_services(EXACT_QUESTION), ["infisical"])

    def test_direct_named_operations(self):
        for question in (
            QUESTION,
            "Which method can read VaultDesk credentials?",
            "Which helper can access VaultDesk secrets?",
            "Which installed helper reads VaultDesk credentials?",
            "What is the approved credential-read method for VaultDesk?",
            "Which method can retrieve secrets from VaultDesk?",
            "What is VaultDesk's live secret access method?",
            "Which VaultDesk credential reader is installed?",
            "What method supports VaultDesk credential reads?",
        ):
            with self.subTest(question=question):
                self.assertEqual(credential_services(question), ["vaultdesk"])

    def test_location_subject_does_not_expand_credential_service(self):
        question = (
            "Where are the TokenBook credentials, and what is the approved method "
            "for live Infisical credential reads?"
        )
        self.assertEqual(credential_services(question), ["infisical"])

    def test_separate_method_requests_keep_service_order(self):
        question = "What is the VaultDesk secret read method; what is the KeyHouse credential access method?"
        self.assertEqual(credential_services(question), ["vaultdesk", "keyhouse"])

    def test_unrelated_and_timeless_controls(self):
        for question in (
            "What is the approved method for Kubernetes workload metadata?",
            "What is the TokenBook protected credential location?",
            "Where does TokenBook store its secrets?",
            "What is a credential read method?",
            "What is the current credential access method?",
            "What is the live Secret read method?",
            "What is the CLI credential read method?",
            "What is the SDK credential access method?",
            "What is the Native credential read method?",
            "What is the Server credential read method?",
            "What does a secret mean?",
            "Which method stores TokenBook credentials?",
        ):
            with self.subTest(question=question):
                self.assertEqual(credential_services(question), [])
                self.assertIsNone(credential_method_problem(question, "Unchanged answer.", []))

    def test_method_intent_cannot_transfer_between_clauses(self):
        self.assertEqual(credential_services("What are VaultDesk credential reads; which method lists metadata?"), [])


class MethodSupportTests(unittest.TestCase):
    def problem(self, answer, *sources, question=QUESTION):
        return credential_method_problem(question, answer, list(sources))

    def reject(self, answer, *sources, question=QUESTION):
        self.assertEqual(self.problem(answer, *sources, question=question), CREDENTIAL_METHOD_REASON)

    def test_exact_wrong_metadata_method_and_contradiction(self):
        answer = (
            "Use `devbox-kube-read` with the owned kubeconfig as the approved method for live Infisical credential reads. "
            "No live Infisical method is approved."
        )
        self.reject(answer, METADATA, INFISICAL_ABSENT, question=EXACT_QUESTION)

    def test_cli_header_filter_failure_is_not_method(self):
        answer = "The approved method is `Infisical CLI` custom-header filtering."
        source = (
            "The Infisical CLI filters custom headers. "
            "The Infisical CLI audit shows a filtering failure for the exact User-Agent on live credential reads."
        )
        self.reject(answer, source, question=EXACT_QUESTION)

    def test_exact_metadata_fixture_cannot_transfer(self):
        answer = "Use `devbox-kube-read` for live Infisical credential reads."
        self.reject(answer, METADATA, question=EXACT_QUESTION)

    def test_mixed_available_metadata_and_unavailable_service(self):
        self.reject(POSITIVE, METADATA, ABSENT)

    def test_supported_installed_live_reader_direct_binding(self):
        self.assertIsNone(self.problem(POSITIVE, SUPPORT))
        self.assertIsNone(self.problem(POSITIVE, SUPPORT.replace("supported installed", "installed and supported")))

    def test_approved_live_method_direct_binding(self):
        source = "`vault-reader` is the approved live method for VaultDesk credential reads."
        self.assertIsNone(self.problem(POSITIVE, source))

    def test_credential_reader_direct_binding(self):
        source = "`vault-reader` is the approved installed live credential reader for VaultDesk."
        self.assertIsNone(self.problem(POSITIVE, source))

    def test_positive_exact_header_binding(self):
        answer = "Use `vault-reader` for live Infisical credential reads."
        source = "`vault-reader` is the approved installed live exact-User-Agent method for Infisical credential reads."
        self.assertIsNone(self.problem(answer, source, question=EXACT_QUESTION))

    def test_exact_header_requirement_does_not_follow_from_metadata(self):
        source = SUPPORT.replace("VaultDesk", "Infisical")
        self.reject(POSITIVE.replace("VaultDesk", "Infisical"), source, METADATA, question=EXACT_QUESTION)

    def test_service_read_operation_is_required(self):
        for source in (
            "`vault-reader` is the supported installed live method for VaultDesk metadata reads.",
            "`vault-reader` is the supported installed live method for VaultDesk credential locations.",
            "`vault-reader` is the supported installed live method for VaultDesk configuration.",
        ):
            with self.subTest(source=source):
                self.reject(POSITIVE, source)

    def test_same_clause_metadata_capability_does_not_bind_credential_operation(self):
        source = (
            "VaultDesk credential reads use `vault-reader` as the approved installed live method "
            "for Kubernetes workload metadata."
        )
        self.reject(POSITIVE, source)

    def test_same_method_different_service(self):
        self.reject(POSITIVE, SUPPORT.replace("VaultDesk", "KeyHouse"))
        self.reject(POSITIVE, "VaultDesk credential reads use `other-reader`.", SUPPORT.replace("VaultDesk", "KeyHouse"))

    def test_answer_cannot_substitute_another_explicit_service(self):
        self.reject(POSITIVE.replace("VaultDesk", "KeyHouse"), SUPPORT)

    def test_other_method_same_service(self):
        self.reject(POSITIVE, SUPPORT.replace(METHOD, "other-reader"))

    def test_same_clause_multiple_services_cannot_transfer(self):
        self.reject(POSITIVE, SUPPORT.replace("VaultDesk credential reads", "KeyHouse credential reads for VaultDesk credential reads"))

    def test_same_clause_multiple_methods_cannot_transfer(self):
        source = "VaultDesk credential reads use `other-reader` with `vault-reader` as a supported installed live method."
        self.reject(POSITIVE, source)

    def test_source_only_native_claim_is_not_delivery(self):
        answer = "`vault-reader` is the approved native live method for VaultDesk credential reads."
        for modifier in ("reviewed", "draft", "source-only", "source code", "internal", "placeholder", "audit", "fixture", "stub"):
            with self.subTest(modifier=modifier):
                self.reject(answer, SUPPORT.replace("supported installed", f"approved native {modifier}"))

    def test_native_or_installed_claim_needs_same_explicit_capability(self):
        source = "`vault-reader` is the approved live method for VaultDesk credential reads."
        self.reject("`vault-reader` is the native approved live method for VaultDesk credential reads.", source)
        self.reject("`vault-reader` is the installed approved live method for VaultDesk credential reads.", source)

    def test_uncertain_or_negated_source_cannot_supply_delivery(self):
        for modifier in ("not", "never", "unverified", "unknown", "may", "might", "could", "if", "proposed", "planned"):
            with self.subTest(modifier=modifier):
                self.reject(POSITIVE, SUPPORT.replace("is the", f"is {modifier} the"))

    def test_live_and_capability_are_required(self):
        for source in (
            SUPPORT.replace("live ", ""),
            SUPPORT.replace("supported ", ""),
            SUPPORT.replace("installed ", ""),
        ):
            with self.subTest(source=source):
                self.reject(POSITIVE, source)

    def test_separate_clause_parts_do_not_supply_binding(self):
        for separator in (". ", "; ", "\n", ", ", " and ", " but ", " while "):
            with self.subTest(separator=separator):
                source = "VaultDesk credential reads need a method" + separator + "`vault-reader` is the supported installed live method."
                self.reject(POSITIVE, source)

    def test_name_or_description_only_is_not_support(self):
        self.reject(POSITIVE, "VaultDesk", "vault-reader: installed approved live method")
        self.reject(POSITIVE, "VaultDesk-vault-reader-approved-installed-live-credential-reads")

    def test_all_cited_texts_and_clauses_are_checked_for_conflicts(self):
        self.reject(POSITIVE, SUPPORT, ABSENT)
        self.reject(POSITIVE, SUPPORT + "\n" + ABSENT)
        self.assertIsNone(self.problem(POSITIVE, SUPPORT, ABSENT.replace("VaultDesk", "KeyHouse")))

    def test_draft_contradiction_in_answer_cannot_pass(self):
        answer = POSITIVE + " " + ABSENT
        self.reject(answer, ABSENT)
        # Even a cited positive clause does not remove the contradiction.
        self.reject(answer, SUPPORT, ABSENT)
        self.reject(answer, SUPPORT)
        self.reject(POSITIVE, SUPPORT + " No method is installed.")

    def test_unparsed_method_selection_cannot_hide_behind_absence(self):
        for selection in ("The method: vault-reader.", "The answer is vault-reader.",
                          "VaultDesk credential reads: vault-reader."):
            with self.subTest(selection=selection):
                self.reject(selection + " " + ABSENT, ABSENT)

    def test_qualified_answer_recommendation_fails_closed(self):
        self.reject("Maybe use `vault-reader` for VaultDesk credential reads.", SUPPORT)
        self.reject("Use `vault-reader` if VaultDesk credential reads are approved.", SUPPORT)

    def test_unrelated_method_negation_does_not_veto_direct_binding(self):
        other = "KeyHouse has no installed live credential-read method."
        self.assertIsNone(self.problem(POSITIVE, SUPPORT, other))

    def test_markdown_emphasis_does_not_break_direct_binding(self):
        self.assertIsNone(self.problem(POSITIVE, SUPPORT.replace("`vault-reader`", "**`vault-reader`**")))

    def test_safe_sourced_absence_passes(self):
        self.assertIsNone(self.problem(ABSENT, ABSENT, METADATA))
        answer = "No approved live Infisical credential-read method exists. devbox#14 remains blocked."
        self.assertIsNone(self.problem(answer, INFISICAL_ABSENT, METADATA, question=EXACT_QUESTION))

    def test_safe_sourced_unknown_passes(self):
        unknown = "The VaultDesk credential-read method is unknown."
        self.assertIsNone(self.problem(unknown, unknown))

    def test_unknown_does_not_authorize_recommendation(self):
        unknown = "The VaultDesk credential-read method is unknown."
        self.reject(unknown + " " + POSITIVE, unknown)
        self.reject("No approved method exists; use `vault-reader` for VaultDesk credential reads.", unknown)

    def test_absence_needs_readable_service_evidence(self):
        self.reject(ABSENT)
        self.reject(ABSENT, "VaultDesk", "No method exists.")
        self.reject(ABSENT, ABSENT.replace("VaultDesk", "KeyHouse"))

    def test_static_error_does_not_echo_source_or_answer(self):
        self.reject("Use `synthetic-private-placeholder` for VaultDesk credential reads.", "SYNTHETIC PRIVATE TEXT")
        self.assertNotIn("synthetic", CREDENTIAL_METHOD_REASON.casefold())

    def test_empty_or_non_answer_does_not_pass(self):
        self.reject("", SUPPORT)
        self.reject("See the source.", SUPPORT)


class ReadableMappingValuesTests(unittest.TestCase):
    def test_cited_mapping_uses_values_not_source_keys(self):
        self.assertIsNone(credential_method_problem(QUESTION, POSITIVE, {"source-note": SUPPORT}))
        self.assertEqual(credential_method_problem(QUESTION, POSITIVE, {SUPPORT: "Metadata only."}),
                         CREDENTIAL_METHOD_REASON)

    def test_safe_absence_mapping_has_service_bound_negative_text(self):
        self.assertIsNone(credential_method_problem(QUESTION, ABSENT, {"source-note": ABSENT}))
        self.assertEqual(credential_method_problem(QUESTION, ABSENT, {ABSENT: "No relevant source text."}),
                         CREDENTIAL_METHOD_REASON)


if __name__ == "__main__":
    unittest.main()

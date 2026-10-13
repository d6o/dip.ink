"""Synthetic regressions for dip.ink#11 and dip.ink#13; no service calls."""
import unittest

from answer_qualifiers import (
    HOLD_REASON,
    LEAD_REASON,
    REQUEST_REASON,
    qualifier_problem,
    qualifier_requirements,
)

REQUEST_QUESTION = (
    "What are the TokenBook self-hosted inference deployment, model, request limits, "
    "and protected credential location?"
)
LEAD_HOLD_QUESTION = (
    "What is the current k3s project lead address and what rollout or workload admission "
    "hold affects GPUHours, TokenBook, or new prototype deployments on October 13, 2026?"
)
LEAD_QUESTION = "What is the current k3s project lead address?"
HOLD_QUESTION = "What workload admission or rollout hold affects TokenBook?"
CAP = "TokenBook per-request caps are 2,048 UTF-8 text bytes and 96 output tokens."
ALLOWANCE = "TokenBook has cumulative allowances of 12,000 input tokens and 3,000 output tokens per unit."
ADDRESS = "k3s/test-worker~test-session:101"
LEAD = f"The k3s project lead address is {ADDRESS}."
HOLD = "k3s holds app rollouts and new workload admissions for TokenBook."


class IntentTests(unittest.TestCase):
    def test_exact_questions(self):
        self.assertEqual(qualifier_requirements(REQUEST_QUESTION), ("request_limits",))
        self.assertEqual(qualifier_requirements(LEAD_HOLD_QUESTION), ("project_lead", "workload_hold"))

    def test_generic_timeless_controls_do_not_change(self):
        for question in ("What is a token?", "How does a queue work?", "What model does TokenBook use?"):
            with self.subTest(question=question):
                self.assertEqual(qualifier_requirements(question), ())
                self.assertIsNone(qualifier_problem(question, "A generic answer.", {}))


class RequestTests(unittest.TestCase):
    def problem(self, answer, *texts):
        return qualifier_problem(REQUEST_QUESTION, answer, {str(i): text for i, text in enumerate(texts)})

    def test_exact_ticket_question_rejects_unit_allowance(self):
        answer = "TokenBook request limits are 12,000 input tokens and 3,000 output tokens per unit."
        self.assertEqual(self.problem(answer, ALLOWANCE), REQUEST_REASON)

    def test_correct_other_fields_cannot_supply_absent_request_bounds(self):
        source = ALLOWANCE + " TokenBook uses a small model on a local inference server."
        answer = "TokenBook uses a small local model. Request limits are 12,000 input tokens."
        self.assertEqual(self.problem(answer, source), REQUEST_REASON)

    def test_exact_request_bound_support(self):
        self.assertIsNone(self.problem(CAP, CAP))

    def test_separate_mixed_unit_and_request_evidence(self):
        self.assertIsNone(self.problem(CAP, ALLOWANCE, CAP))
        self.assertIsNone(self.problem(CAP, ALLOWANCE + "\n" + CAP))

    def test_number_only_in_unit_evidence_is_rejected(self):
        answer = "TokenBook per-request caps are 12,000 input tokens and 96 output tokens."
        self.assertEqual(self.problem(answer, ALLOWANCE, CAP), REQUEST_REASON)

    def test_mislabeled_allowance_cannot_hide_beside_valid_cap(self):
        answer = CAP + " TokenBook request limits are 12,000 input tokens per unit."
        self.assertEqual(self.problem(answer, CAP, ALLOWANCE), REQUEST_REASON)

    def test_correctly_labeled_allowance_can_accompany_valid_cap(self):
        self.assertIsNone(self.problem(CAP + " " + ALLOWANCE, CAP, ALLOWANCE))

    def test_units_encoding_and_direction_are_not_interchangeable(self):
        for answer in (
            "TokenBook per-request limits are 2,048 tokens and 96 output tokens.",
            "TokenBook per-request limits are 96 input tokens.",
            "TokenBook per-request limits are 2 MiB and 96 output tokens.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(self.problem(answer, CAP), REQUEST_REASON)
        self.assertEqual(self.problem(CAP, CAP.replace("UTF-8 text ", "")), REQUEST_REASON)

    def test_direction_before_number(self):
        source = "TokenBook request caps are input: 64 tokens and output: 16 tokens."
        self.assertIsNone(self.problem(source, source))
        self.assertEqual(self.problem(source.replace("input: 64", "input: 16"), source), REQUEST_REASON)

    def test_request_word_without_bound_marker_does_not_qualify(self):
        source = "TokenBook logs each request id and offers 12,000 input tokens per unit."
        self.assertEqual(self.problem(CAP, source), REQUEST_REASON)

    def test_semantic_description_is_not_a_bound(self):
        source = "TokenBook request limits protect the service and are documented elsewhere."
        self.assertEqual(self.problem(source, source), REQUEST_REASON)

    def test_unrelated_subject_bound_is_not_support(self):
        for source in (
            "TokenBook is available. GPUHours per-request caps are 2,048 UTF-8 text bytes and 96 output tokens.",
            "TokenBook uses GPUHours, whose per-request caps are 2,048 UTF-8 text bytes and 96 output tokens.",
            "# TokenBook\nGPUHours per-request caps are 2,048 UTF-8 text bytes and 96 output tokens.",
        ):
            with self.subTest(source=source):
                self.assertEqual(self.problem(CAP, source), REQUEST_REASON)

    def test_negated_uncertain_and_mixed_clause_support_is_rejected(self):
        for source in (
            "TokenBook request limits are not 2,048 UTF-8 text bytes and 96 output tokens.",
            "TokenBook request caps might be 2,048 UTF-8 text bytes and 96 output tokens.",
            "TokenBook per-request caps are 96 output tokens and the per-unit allowance is 12,000 input tokens.",
        ):
            with self.subTest(source=source):
                self.assertEqual(self.problem(CAP, source), REQUEST_REASON)

    def test_account_and_daily_caps_do_not_qualify(self):
        for period in ("per account", "per day", "daily", "per month"):
            source = f"TokenBook request limits are 96 output tokens {period}."
            self.assertEqual(self.problem(source, source), REQUEST_REASON)

    def test_source_key_cannot_supply_readable_subject(self):
        self.assertEqual(qualifier_problem(REQUEST_QUESTION, CAP, {"TokenBook": CAP.replace("TokenBook ", "")}), REQUEST_REASON)


class LeadTests(unittest.TestCase):
    def problem(self, answer, source):
        return qualifier_problem(LEAD_QUESTION, answer, {"fixture": source})

    def test_valid_explicit_binding(self):
        self.assertIsNone(self.problem(LEAD, LEAD))
        self.assertIsNone(self.problem(ADDRESS, LEAD))
        self.assertIsNone(self.problem(LEAD, f"{ADDRESS} is the k3s project lead address."))

    def test_wrong_project_address_even_if_source_mislabels_it(self):
        wrong = "k3s project lead address is gpuhours.voa.sh/test-worker~test-session:202."
        self.assertEqual(self.problem(wrong, wrong), LEAD_REASON)
        self.assertEqual(self.problem(wrong + " " + LEAD, LEAD), LEAD_REASON)

    def test_wrong_project_with_requested_project_elsewhere_in_source(self):
        wrong = "gpuhours.voa.sh/test-worker~test-session:202"
        source = f"GPUHours project lead address is {wrong}. The k3s project lead manages the cluster."
        self.assertEqual(self.problem(f"k3s project lead address is {wrong}.", source), LEAD_REASON)

    def test_unknown_complete_address(self):
        self.assertEqual(self.problem(LEAD.replace(":101", ":303"), LEAD), LEAD_REASON)
        self.assertEqual(self.problem(LEAD, "The k3s lead manages the cluster."), LEAD_REASON)

    def test_cooccurrence_is_not_a_role_binding(self):
        for source in (
            f"The k3s project lead contacted {ADDRESS}.",
            f"The k3s project lead works with GPUHours. Address: {ADDRESS}.",
            f"GPUHours project lead address is {ADDRESS}; k3s manages the cluster.",
            f"The k3s project lead address might be {ADDRESS}.",
            f"The k3s project lead address is not {ADDRESS}.",
        ):
            with self.subTest(source=source):
                self.assertEqual(self.problem(LEAD, source), LEAD_REASON)

    def test_shortened_native_identity_is_rejected(self):
        for address in ("k3s:101", "k3s/test-worker:101", "k3s/test-session:101", "k3s/test-worker~test-session"):
            with self.subTest(address=address):
                self.assertEqual(self.problem(f"k3s project lead address is {address}.", LEAD), LEAD_REASON)

    def test_only_project_case_is_insensitive(self):
        question = "What is the GPUHours.VOA.SH project lead address?"
        source = "GPUHours.VOA.SH project lead address is gpuhours.voa.sh/Test_Worker~Test_Session:101."
        self.assertIsNone(qualifier_problem(question, source.replace("gpuhours.voa.sh/", "GPUHOURS.VOA.SH/"), {"fixture": source}))
        self.assertEqual(qualifier_problem(question, source.replace("Test_Worker", "test_worker"), {"fixture": source}), LEAD_REASON)
        self.assertEqual(qualifier_problem(question, source.replace("Test_Session", "test_session"), {"fixture": source}), LEAD_REASON)


class HoldTests(unittest.TestCase):
    def test_exact_ticket_question_rejects_wrong_role_and_gateway_facts(self):
        source = "GPUHours project lead address is gpuhours.voa.sh/test-worker~test-session:202. TokenBook gateway access is retired."
        answer = "k3s project lead address is gpuhours.voa.sh/test-worker~test-session:202. TokenBook gateway access is retired."
        self.assertEqual(qualifier_problem(LEAD_HOLD_QUESTION, answer, {"fixture": source}), LEAD_REASON)

    def test_valid_role_cannot_replace_missing_hold(self):
        source = LEAD + " TokenBook gateway access is retired."
        self.assertEqual(qualifier_problem(LEAD_HOLD_QUESTION, LEAD + " " + HOLD, {"fixture": source}), HOLD_REASON)

    def test_supported_role_and_operational_hold(self):
        source = LEAD + " " + HOLD
        self.assertIsNone(qualifier_problem(LEAD_HOLD_QUESTION, source, {"fixture": source}))

    def test_hold_does_not_replace_missing_role(self):
        self.assertEqual(qualifier_problem(LEAD_HOLD_QUESTION, LEAD + " " + HOLD, {"fixture": HOLD}), LEAD_REASON)

    def test_project_specific_passive_and_nominal_hold(self):
        for source in (
            "TokenBook app rollouts are on hold.",
            "App rollouts are on hold for TokenBook.",
            "The workload admission hold affects TokenBook.",
        ):
            with self.subTest(source=source):
                self.assertIsNone(qualifier_problem(HOLD_QUESTION, source, {"fixture": source}))

    def test_gateway_retirement_and_cooccurrence_do_not_supply_hold(self):
        answer = "TokenBook app rollouts are on hold."
        for source in (
            "TokenBook gateway access is retired.",
            "TokenBook gateway access is on hold.",
            "GPUHours app rollouts are on hold. TokenBook is available.",
            "TokenBook is available while GPUHours app rollouts are on hold.",
            "TokenBook app rollouts are not on hold.",
            "TokenBook app rollouts might be on hold.",
            "The TokenBook rollout hold was lifted.",
            "TokenBook app rollouts are on hold?",
        ):
            with self.subTest(source=source):
                self.assertEqual(qualifier_problem(HOLD_QUESTION, answer, {"fixture": source}), HOLD_REASON)

    def test_hold_type_and_affected_project_do_not_transfer(self):
        for source in (
            "TokenBook workload admissions are on hold.",
            "GPUHours app rollouts are on hold.",
        ):
            self.assertEqual(qualifier_problem(HOLD_QUESTION, "TokenBook app rollouts are on hold.", {"fixture": source}), HOLD_REASON)

    def test_static_reasons_do_not_echo_sources(self):
        result = qualifier_problem(REQUEST_QUESTION, CAP, {"fixture": "arbitrary sensitive text"})
        self.assertEqual(result, REQUEST_REASON)
        self.assertNotIn("sensitive", result)


if __name__ == "__main__":
    unittest.main()

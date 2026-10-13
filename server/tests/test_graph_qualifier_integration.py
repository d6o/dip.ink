"""Actual answer-path regressions for qualifier scope and role assignment."""
import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")

import graph

REQUEST = "What are the TokenBook self-hosted inference deployment, model, request limits, and protected credential location?"
ROLE = "What is the current k3s project lead address and what rollout or workload admission hold affects GPUHours, TokenBook, or new prototype deployments on October 13, 2026?"
SOURCE = "2026-10-13-024536-tokenbook-self-hosted-inference"
ROLE_SOURCE = "2026-10-13-040127-gpuhours-project-launch"
ALLOWANCE = "TokenBook has 10,000 input tokens and 2,000 output tokens per unit."
CAPS = "TokenBook per-request caps are 6,000 UTF-8 text bytes and 512 output tokens."
BAD_ROLE = "The current k3s project lead address is gpuhours.voa.sh/test-worker~test-session:123."


def evidence(content, source=SOURCE):
    return {"facts": [], "entities": [], "source_excerpt": None,
            "semantic_notes": [{"name": source, "content": content}]}


class QualifierIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def run_answer(self, question, content, answer, source=SOURCE):
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=evidence(content, source))), \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock(return_value={
                 "answer": answer, "confidence": "high", "sources": [source], "escalate": False,
             })), mock.patch.object(graph, "_record_query") as record:
            result = await graph._graph_answer_impl(question)
        return result, record.call_args.args[0]

    async def test_exact_request_question_does_not_use_unit_allowances(self):
        result, event = await self.run_answer(REQUEST, ALLOWANCE,
            "TokenBook request limits are 10,000 input tokens and 2,000 output tokens per unit.")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["confidence"], "not_found")
        self.assertTrue(result["escalate"])
        self.assertEqual(event["grounding_action"], "qualifier_mismatch")

    async def test_exact_question_accepts_request_caps_when_explicitly_supported(self):
        result, _ = await self.run_answer(REQUEST, CAPS + " " + ALLOWANCE, CAPS)
        self.assertEqual(result["answer"], CAPS)
        self.assertEqual(result["confidence"], "high")

    async def test_same_subject_wrong_scope_fails_even_with_correct_cap_in_source(self):
        result, _ = await self.run_answer(REQUEST, CAPS + " " + ALLOWANCE,
            "TokenBook per-request caps are 10,000 input tokens and 512 output tokens.")
        self.assertIsNone(result["answer"])

    async def test_exact_role_query_rejects_gpu_lead_substitution(self):
        content = ("GPUHours project lead address is gpuhours.voa.sh/test-worker~test-session:123. "
                   "TokenBook gateway access is retired.")
        result, event = await self.run_answer(ROLE, content,
            BAD_ROLE + " TokenBook gateway access is retired.", ROLE_SOURCE)
        self.assertIsNone(result["answer"])
        self.assertTrue(result["escalate"])
        self.assertEqual(event["grounding_action"], "qualifier_mismatch")

    async def test_valid_role_does_not_admit_gateway_retirement_as_rollout_hold(self):
        lead = "The current k3s project lead address is k3s/test-worker~test-session:123."
        content = lead + " GPUHours has a separate deployment. TokenBook gateway access is retired."
        result, _ = await self.run_answer(ROLE, content,
            lead + " k3s holds app rollouts for TokenBook.", ROLE_SOURCE)
        self.assertIsNone(result["answer"])
        self.assertIn("operational hold", result["superseded_note"])

    async def test_supported_role_and_hold_are_dated_not_live_authority(self):
        question = "What is the k3s project lead address and what rollout hold affects TokenBook?"
        text = ("The k3s project lead address is k3s/test-worker~test-session:123. "
                "k3s holds app rollouts for TokenBook.")
        result, _ = await self.run_answer(question, text, text, ROLE_SOURCE)
        self.assertIsNotNone(result["answer"])
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["escalate"])
        self.assertIn("2026-10-13T04:01:27Z", result["answer"])
        self.assertIn("Live state is not verified", result["answer"])


class RawDraftAndRoleDateTests(unittest.IsolatedAsyncioTestCase):
    ROLE_QUERY = "What is the k3s project lead address and what rollout hold affects TokenBook?"
    LEAD = "The k3s project lead address is k3s/test-worker~test-session:123."
    HOLD = "k3s holds app rollouts for TokenBook."
    DATED = "2026-10-13-061000-tokenbook-other-fact"
    ROLE_DATED = "2026-10-12-120000-tokenbook-role"
    HOLD_DATED = "2026-10-12-130000-tokenbook-hold"

    def role_packet(self, notes):
        return {"facts": [], "entities": [], "source_excerpt": None,
                "semantic_notes": [{"name": name, "content": text} for name, text in notes.items()]}

    def validate_role(self, notes):
        return graph._validate_distilled_answer({
            "answer": self.LEAD + " " + self.HOLD, "sources": list(notes), "confidence": "high", "escalate": False,
        }, self.role_packet(notes), self.ROLE_QUERY)

    def test_unrelated_dated_citation_cannot_date_an_undated_role_and_hold(self):
        notes = {"TokenBook": self.LEAD + " " + self.HOLD,
                 self.DATED: "TokenBook uses a local server."}
        result, _, action = self.validate_role(notes)
        self.assertIsNone(result["answer"])
        self.assertEqual(action, "qualifier_mismatch")

    def test_each_requested_relation_needs_dated_evidence(self):
        for notes in (
            {"TokenBook": self.HOLD, self.ROLE_DATED: self.LEAD + " TokenBook has a service."},
            {"TokenBook": self.LEAD + " TokenBook has a service.", self.HOLD_DATED: self.HOLD},
        ):
            with self.subTest(notes=notes):
                result, _, _ = self.validate_role(notes)
                self.assertIsNone(result["answer"])

    def test_only_supporting_role_and_hold_sources_supply_observation_dates(self):
        notes = {self.ROLE_DATED: self.LEAD + " TokenBook has a service.",
                 self.HOLD_DATED: self.HOLD, self.DATED: "TokenBook uses a local server."}
        result, _, _ = self.validate_role(notes)
        self.assertIsNotNone(result["answer"])
        self.assertEqual(result["observation_dates"], {
            self.ROLE_DATED: "2026-10-12T12:00:00Z", self.HOLD_DATED: "2026-10-12T13:00:00Z",
        })
        self.assertNotIn("2026-10-13T06:10:00Z", result["answer"])

    async def test_actual_answer_path_rejects_wrong_limit_after_display_boundary(self):
        draft = CAPS + " " + ("Supporting context. " * 110)
        self.assertGreater(len(draft), 2000)
        draft += " TokenBook request limits are 10,000 input tokens and 2,000 output tokens per unit."
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=evidence(CAPS + " " + ALLOWANCE))), \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock(return_value={
                 "answer": draft, "confidence": "high", "sources": [SOURCE], "escalate": False,
             })), mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl(REQUEST)
        self.assertIsNone(result["answer"])
        self.assertTrue(result["escalate"])

    def test_valid_long_answer_keeps_output_bound(self):
        draft = "TokenBook uses its own service. " * 100
        result, _, _ = graph._validate_distilled_answer({
            "answer": draft, "sources": [SOURCE], "confidence": "high", "escalate": False,
        }, evidence("TokenBook uses its own service."), "What service does TokenBook use?")
        self.assertEqual(len(result["answer"]), 2000)

    def test_wrong_message_count_cannot_hide_beside_correct_byte_and_token_caps(self):
        source = "TokenBook per-request caps are 8 messages, 6,000 UTF-8 text bytes and 512 output tokens."
        result, _, _ = graph._validate_distilled_answer({
            "answer": source.replace("8 messages", "99 messages"), "sources": [SOURCE], "confidence": "high",
        }, evidence(source), REQUEST)
        self.assertIsNone(result["answer"])


class CurrentRoleDateTests(unittest.IsolatedAsyncioTestCase):
    QUESTION = "What is the current k3s project lead address and what rollout hold affects TokenBook?"
    ROLE = "The k3s project lead address is k3s/test-worker~test-session:123."
    HOLD = "k3s holds app rollouts for TokenBook."
    SOURCE = "2026-10-13-010000-tokenbook-role-and-hold"
    UNRELATED = "2026-10-13-020000-tokenbook-server"

    def packet(self, notes=None):
        notes = notes or {self.SOURCE: self.ROLE + " " + self.HOLD,
                          self.UNRELATED: "TokenBook uses a local server."}
        p = {"facts": [], "entities": [], "source_excerpt": None,
             "semantic_notes": [{"name": name, "content": text} for name, text in notes.items()]}
        p["temporal_context"] = graph._temporal_context(p)
        return p

    def draft(self, sources=None):
        return {"answer": self.ROLE + " " + self.HOLD, "confidence": "high", "escalate": False,
                "sources": sources or [self.SOURCE, self.UNRELATED]}

    def test_current_validator_ignores_unrelated_newer_date(self):
        p = self.packet()
        self.assertEqual(p["temporal_context"]["newest_evidence_at"], "2026-10-13T02:00:00Z")
        result, _, _ = graph._validate_distilled_answer(self.draft(), p, self.QUESTION)
        self.assertEqual(result["as_of"], "2026-10-13T01:00:00Z")
        self.assertEqual(result["observation_dates"], {self.SOURCE: "2026-10-13T01:00:00Z"})
        self.assertNotIn("2026-10-13T02:00:00Z", result["answer"])
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["escalate"])

    async def test_actual_current_answer_path_uses_relationship_dates(self):
        self.assertTrue(graph._current_state_question(self.QUESTION))
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=self.packet())), \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock(return_value=self.draft())), \
             mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl(self.QUESTION)
        self.assertEqual(result["as_of"], "2026-10-13T01:00:00Z")
        self.assertNotIn("2026-10-13T02:00:00Z", result["answer"])

    def test_temporal_eligibility_still_rejects_obsolete_role_support(self):
        old = "2026-10-01-010000-tokenbook-role-and-hold"
        notes = {old: self.ROLE + " " + self.HOLD, self.UNRELATED: "TokenBook uses a local server."}
        result, _, _ = graph._validate_distilled_answer(self.draft([old, self.UNRELATED]), self.packet(notes), self.QUESTION)
        self.assertIsNone(result["answer"])
        self.assertTrue(result["escalate"])

    def test_current_role_and_hold_can_have_separate_supporting_dates(self):
        hold_source = "2026-10-13-013000-tokenbook-hold"
        notes = {self.SOURCE: self.ROLE + " TokenBook has a service.", hold_source: self.HOLD,
                 self.UNRELATED: "TokenBook uses a local server."}
        result, _, _ = graph._validate_distilled_answer(self.draft(list(notes)), self.packet(notes), self.QUESTION)
        self.assertEqual(result["observation_dates"], {
            self.SOURCE: "2026-10-13T01:00:00Z", hold_source: "2026-10-13T01:30:00Z",
        })
        self.assertEqual(result["as_of"], "2026-10-13T01:30:00Z")
        self.assertNotIn("2026-10-13T02:00:00Z", result["answer"])

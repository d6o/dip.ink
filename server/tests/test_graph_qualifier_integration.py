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

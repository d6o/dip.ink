"""Qualify deployment snapshots without changing timeless answers."""
import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
import graph

QUESTION = "What is the TokenBook deployment procedure at tokenbook.voa.sh, including its Cloudflare Worker and self-hosted inference hostname?"
OLD = "2026-10-13-041536-tokenbook-prototype-live"
NEW = "2026-10-13-061000-tokenbook-platform-note"
VERSION = "12345678-1234-1234-1234-123456789abc"


def packet():
    return {
        "facts": [], "entities": [], "source_excerpt": None,
        "semantic_notes": [
            {"name": OLD, "content": f"TokenBook uses Worker version {VERSION}."},
            {"name": NEW, "content": "TokenBook uses its own inference deployment."},
        ],
    }


def answer(text=None, sources=None):
    return {"answer": text or f"TokenBook deploys Worker version {VERSION}.",
            "confidence": "high", "sources": sources or [OLD, NEW], "escalate": False}


class ObservationQualifierTests(unittest.IsolatedAsyncioTestCase):
    def test_procedure_answer_with_worker_snapshot_has_actual_source_date(self):
        result, _, action = graph._validate_distilled_answer(answer(), packet(), QUESTION)
        self.assertEqual(action, "downgraded")
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["escalate"])
        self.assertIn("2026-10-13T04:15:36Z", result["answer"])
        self.assertNotIn("2026-10-13T06:10:00Z", result["answer"])
        self.assertEqual(result["observation_dates"], {OLD: "2026-10-13T04:15:36Z"})
        self.assertIn("Live state is not verified", result["answer"])

    def test_undated_snapshot_abstains(self):
        p = packet()
        p["semantic_notes"][0]["name"] = "TokenBook"
        result, _, _ = graph._validate_distilled_answer(answer(sources=["TokenBook"]), p, QUESTION)
        self.assertIsNone(result["answer"])
        self.assertTrue(result["escalate"])

    def test_unrelated_dated_note_cannot_supply_snapshot_date(self):
        p = packet()
        p["semantic_notes"][0]["name"] = "TokenBook"
        result, _, _ = graph._validate_distilled_answer(answer(sources=["TokenBook", NEW]), p, QUESTION)
        self.assertIsNone(result["answer"])

    def test_unrequested_snapshot_must_still_be_in_cited_text(self):
        p = packet()
        p["semantic_notes"][0]["content"] = "TokenBook uses a Worker."
        result, _, _ = graph._validate_distilled_answer(answer(), p, QUESTION)
        self.assertIsNone(result["answer"])

    def test_procedure_without_release_identifier_keeps_normal_behavior(self):
        p = packet()
        p["semantic_notes"][0]["content"] = "TokenBook deploys through its repository workflow."
        parsed = answer("TokenBook deploys through its repository workflow.", [OLD])
        result, _, action = graph._validate_distilled_answer(parsed, p, QUESTION)
        self.assertEqual(result, parsed)
        self.assertEqual(action, "accepted")

    async def test_actual_ordinary_answer_path_qualifies_recorded_release(self):
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=packet())), \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock(return_value=answer())), \
             mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl(QUESTION)
        self.assertEqual(result["observation_dates"], {OLD: "2026-10-13T04:15:36Z"})
        self.assertTrue(result["escalate"])
        self.assertLessEqual(len(result["answer"]), 2000)

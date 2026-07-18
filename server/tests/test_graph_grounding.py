from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")

import graph  # noqa: E402


VALID_SLUG = "2026-07-18-120000-valid-source"
SEMANTIC_SLUG = "2026-07-18-120100-semantic-source"


def packet() -> dict:
    return {
        "query": "question",
        "facts": [{
            "fact": "The answer is 42.",
            "source_slug": VALID_SLUG,
            "current": True,
        }],
        "communities": [],
        "entities": [],
        "source_excerpt": None,
        "semantic_notes": [{"name": SEMANTIC_SLUG}],
    }


class GroundingValidationTests(unittest.TestCase):
    def test_invented_only_source_rejects_non_null_answer(self):
        result, grounded, action = graph._validate_distilled_answer({
            "answer": "made up",
            "confidence": "high",
            "sources": ["2026-07-18-999999-invented"],
            "escalate": False,
        }, packet())

        self.assertFalse(grounded)
        self.assertEqual(action, "rejected")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["confidence"], "not_found")
        self.assertEqual(result["sources"], [])
        self.assertTrue(result["escalate"])

    def test_valid_sourced_answer_is_unchanged(self):
        parsed = {
            "answer": "42",
            "confidence": "high",
            "sources": [VALID_SLUG],
            "escalate": False,
        }
        result, grounded, action = graph._validate_distilled_answer(parsed, packet())

        self.assertTrue(grounded)
        self.assertEqual(action, "accepted")
        self.assertEqual(result, parsed)

    def test_invented_source_is_discarded_when_valid_provenance_remains(self):
        result, grounded, action = graph._validate_distilled_answer({
            "answer": "42",
            "confidence": "high",
            "sources": ["invented", VALID_SLUG],
            "escalate": False,
        }, packet())

        self.assertTrue(grounded)
        self.assertEqual(action, "filtered")
        self.assertEqual(result["sources"], [VALID_SLUG])
        self.assertEqual(result["confidence"], "high")

    def test_high_confidence_with_only_semantic_hit_is_downgraded(self):
        result, grounded, action = graph._validate_distilled_answer({
            "answer": "indirect answer",
            "confidence": "high",
            "sources": [SEMANTIC_SLUG],
            "escalate": False,
        }, packet())

        self.assertTrue(grounded)
        self.assertEqual(action, "downgraded")
        self.assertEqual(result["confidence"], "medium")

    def test_not_found_remains_a_grounded_abstention(self):
        result, grounded, action = graph._validate_distilled_answer({
            "answer": None,
            "confidence": "not_found",
            "sources": ["invented"],
            "escalate": True,
        }, packet())

        self.assertTrue(grounded)
        self.assertEqual(action, "abstained")
        self.assertEqual(result, {
            "answer": None,
            "confidence": "not_found",
            "sources": [],
            "escalate": True,
        })


class AnswerCacheFreshnessTests(unittest.TestCase):
    def setUp(self):
        graph._ANSWER_CACHE.clear()
        graph._ANSWER_CACHE_WATERMARK = None
        for key in graph.GROUNDING_COUNTS:
            graph.GROUNDING_COUNTS[key] = 0

    def test_new_ingest_watermark_invalidates_cached_answer(self):
        async def run() -> None:
            distill_result = {
                "answer": "42",
                "confidence": "high",
                "sources": [VALID_SLUG],
                "escalate": False,
            }
            with mock.patch.object(
                graph,
                "_graph_ingest_watermark",
                new=mock.AsyncMock(side_effect=["watermark-1", "watermark-1", "watermark-2"]),
            ), mock.patch.object(
                graph,
                "_assemble_packet",
                new=mock.AsyncMock(return_value=packet()),
            ) as assemble, mock.patch.object(
                graph,
                "_distill",
                new=mock.AsyncMock(return_value=distill_result),
            ) as distill, mock.patch.object(graph, "_record_query") as record:
                first = await graph._graph_answer_impl("What is the answer?")
                second = await graph._graph_answer_impl(" what is the answer ")
                third = await graph._graph_answer_impl("What is the answer?")

            self.assertEqual(first, second)
            self.assertEqual(second, third)
            self.assertEqual(assemble.await_count, 2)
            self.assertEqual(distill.await_count, 2)
            events = [call.args[0] for call in record.call_args_list]
            self.assertEqual([event["cached"] for event in events], [False, True, False])
            self.assertTrue(all(event["grounded"] for event in events))
            self.assertEqual(len(graph._ANSWER_CACHE), 1)
            self.assertIn(("what is the answer", "watermark-2"), graph._ANSWER_CACHE)

        asyncio.run(run())

    def test_rejected_answer_is_counted_and_never_cached(self):
        async def run() -> None:
            with mock.patch.object(
                graph, "_graph_ingest_watermark", new=mock.AsyncMock(return_value="w1")
            ), mock.patch.object(
                graph, "_assemble_packet", new=mock.AsyncMock(return_value=packet())
            ), mock.patch.object(
                graph,
                "_distill",
                new=mock.AsyncMock(return_value={
                    "answer": "fabricated",
                    "confidence": "high",
                    "sources": ["invented"],
                    "escalate": False,
                }),
            ), mock.patch.object(graph, "_record_query") as record:
                result = await graph._graph_answer_impl("unsupported")

            self.assertEqual(result["confidence"], "not_found")
            self.assertEqual(graph.GROUNDING_COUNTS["rejected"], 1)
            self.assertEqual(graph._ANSWER_CACHE, {})
            event = record.call_args.args[0]
            self.assertFalse(event["grounded"])
            self.assertEqual(event["grounding_action"], "rejected")

        asyncio.run(run())

    def test_watermark_query_is_group_scoped(self):
        class Driver:
            def __init__(self):
                self.query = ""
                self.params = {}

            async def execute_query(self, query, **params):
                self.query = query
                self.params = params
                return [{"watermark": "2026-07-18T12:00:00Z"}], None, None

        async def run() -> None:
            driver = Driver()
            fake_graph = type("G", (), {"driver": driver})()
            with mock.patch.object(graph, "_get_graph", new=mock.AsyncMock(return_value=fake_graph)):
                watermark = await graph._graph_ingest_watermark()
            self.assertEqual(watermark, "2026-07-18T12:00:00Z")
            self.assertIn("group_id: $group_id", driver.query)
            self.assertEqual(driver.params["group_id"], graph.DEFAULT_GROUP_ID)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_EMBED_PROVIDER", "openai")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
os.environ.setdefault("WIKI_ROOT", "/tmp/wiki-mcp-test-global-root")

from prometheus_client.parser import text_string_to_metric_families  # noqa: E402

import core  # noqa: E402
import server  # noqa: E402


EXPECTED_METRICS = {
    "dipink_info",
    "dipink_tool_calls_total",
    "dipink_tool_duration_seconds",
    "dipink_note_drop_total",
    "dipink_graph_answer_total",
    "dipink_graph_answer_duration_seconds",
    "dipink_wiki_index_ready",
    "dipink_wiki_index_degraded",
    "dipink_wiki_pages_indexed",
    "dipink_wiki_index_age_seconds",
    "dipink_graph_ready",
    "dipink_inbox_notes",
    "dipink_deferred_notes",
    "dipink_blocked_notes",
    "dipink_review_queue_open",
    "dipink_ingest_pending_notes",
    "dipink_ingest_partial_notes",
    "dipink_ingest_lag_seconds",
    "dipink_community_age_seconds",
}


def snapshot() -> dict:
    return {
        "components": {
            "wiki": {"ready": True},
            "graph": {"ready": True},
            "git_clone": {"ready": True},
        },
        "index": {
            "pages_indexed": 12,
            "age_seconds": 30.0,
            "degraded": True,
        },
        "queues": {
            "inbox": {"count": 2},
            "deferred": {"count": 3},
            "blocked": {"count": 4},
            "review_queue_open": 5,
        },
        "ingest": {
            "pending": 6,
            "partial": 7,
            "lag_seconds": 8.0,
        },
        "communities": {
            "count": 2,
            "age_seconds": 9.0,
        },
    }


def samples_by_name(text: str):
    out = {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            out.setdefault(sample.name, []).append(sample)
    return out


class ObservabilityContractTests(unittest.TestCase):
    def test_prometheus_schema_and_status_gauges_match(self):
        text = core.render_prometheus(snapshot()).decode("utf-8")
        samples = samples_by_name(text)

        for name in EXPECTED_METRICS:
            self.assertIn(f"# HELP {name} ", text, name)
        self.assertNotIn("_created", text)
        gauge_expectations = {
            "dipink_wiki_index_ready": 1,
            "dipink_wiki_index_degraded": 1,
            "dipink_wiki_pages_indexed": 12,
            "dipink_wiki_index_age_seconds": 30,
            "dipink_graph_ready": 1,
            "dipink_inbox_notes": 2,
            "dipink_deferred_notes": 3,
            "dipink_blocked_notes": 4,
            "dipink_review_queue_open": 5,
            "dipink_ingest_pending_notes": 6,
            "dipink_ingest_partial_notes": 7,
            "dipink_ingest_lag_seconds": 8,
            "dipink_community_age_seconds": 9,
        }
        for name, expected in gauge_expectations.items():
            self.assertEqual(samples[name][0].value, expected, name)

    def test_metric_labels_are_exact_and_never_include_private_dimensions(self):
        core.record_query({
            "ts": time.time(),
            "tool": "graph_answer",
            "confidence": "high",
            "cached": False,
            "grounded": False,
            "grounding_action": "rejected",
            "assemble_ms": 10,
            "distill_ms": 20,
            "query": "super-private-query",
            "slug": "super-private-slug",
        })
        core.record_query({
            "ts": time.time(),
            "tool": "attacker-controlled-tool-name",
            "outcome": "strange-outcome",
            "page": "private-page",
        })
        core.record_query({
            "ts": time.time(),
            "tool": "wiki_note_drop",
            "outcome": "already_exists",
        })
        text = core.render_prometheus(snapshot()).decode("utf-8")
        samples = samples_by_name(text)

        allowed_by_prefix = {
            "dipink_info": {"version"},
            "dipink_tool_calls": {"tool", "outcome"},
            "dipink_tool_duration_seconds": {"tool", "le"},
            "dipink_note_drop": {"outcome"},
            "dipink_graph_answer_duration_seconds": {"phase", "le"},
            "dipink_graph_answer": {"confidence", "cached", "grounded"},
        }
        for sample_name, metric_samples in samples.items():
            if not sample_name.startswith("dipink_"):
                continue
            allowed = set()
            for prefix, labels in allowed_by_prefix.items():
                if sample_name.startswith(prefix):
                    allowed = labels
                    break
            for sample in metric_samples:
                self.assertTrue(set(sample.labels).issubset(allowed), (sample.name, sample.labels))
                self.assertFalse({"query", "page", "slug"} & set(sample.labels))

        self.assertNotIn("super-private-query", text)
        self.assertNotIn("super-private-slug", text)
        self.assertNotIn("private-page", text)
        self.assertIn('tool="other"', text)
        self.assertIn('outcome="already_exists"', text)
        self.assertIn('grounded="false"', text)

    def test_metrics_endpoint_is_prometheus_text_and_uses_shared_status(self):
        async def run():
            with mock.patch.object(
                server, "get_status", new=mock.AsyncMock(return_value=snapshot())
            ):
                return await server.prometheus_metrics(None)

        response = asyncio.run(run())
        text = response.body.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertIn("dipink_ingest_pending_notes 6.0", text)
        self.assertIn("dipink_info", text)

    def test_metrics_route_is_registered_separately_from_json_history(self):
        paths = {getattr(route, "path", "") for route in server.app.routes}
        self.assertIn("/metrics", paths)
        self.assertIn("/api/metrics", paths)

    def test_missing_communities_publish_infinite_age_for_stale_alerting(self):
        missing = snapshot()
        missing["communities"] = {"count": 0, "age_seconds": None}
        text = core.render_prometheus(missing).decode("utf-8")
        self.assertIn("dipink_community_age_seconds +Inf", text)


if __name__ == "__main__":
    unittest.main()

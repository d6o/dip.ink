"""Recoverable failure state on synthetic, unchanged note contents."""
import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
import ingest
import ingest_failures
from test_ingest_completion import FakeGraph, make_note, episode


class QuarantineTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_unchanged_note_pauses_and_operator_retry_recovers(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"INGEST_MAX_FAILURES": "3", "GROUP_ID": "main"}), \
             patch.object(ingest.ingest_metrics, "persist", AsyncMock()), \
             patch.object(ingest.ingest_metrics, "emit"), \
             patch.object(ingest.ingest_metrics, "prior_attempts", AsyncMock(return_value=0)):
            note = make_note(Path(tmp), "2026-01-01-000000-fixture", "PRIVATE_FIXTURE note remains unchanged")
            ts, slug, path = note
            g = FakeGraph()
            for count in range(1, 4):
                with patch.object(ingest, "add_episode_with_retry", AsyncMock(side_effect=ingest.ingest_metrics.StructuredOutputError("json_invalid"))):
                    with self.assertRaises(ingest.ingest_metrics.StructuredOutputError):
                        await ingest._ingest_note(g, ts, slug, path, group_id="main")
                self.assertEqual(g.driver.failures[("main", slug)]["failure_count"], count)
            # A failed extraction with mentions must never become legacy-complete.
            g.driver.episodes.append(episode(slug, path.read_text(), mention_count=1))
            assessment = await ingest.assess_ingest(g.driver, [note], upgrade_legacy=False)
            self.assertEqual(assessment.done, set())
            self.assertEqual(assessment.pending, {slug})
            self.assertEqual(assessment.retryable, set())
            self.assertEqual(assessment.quarantined, {slug})
            self.assertEqual(assessment.as_dict()["quarantined"], 1)
            import graph
            import server
            import core
            g.driver.health_check = AsyncMock()
            with patch.object(graph, "_get_graph", AsyncMock(return_value=g)), \
                 patch.object(ingest, "discover_notes", return_value=[note]):
                _, state = await server._graph_status()
            self.assertEqual(state["pending"], 1)
            self.assertEqual(state["quarantined"], 1)
            self.assertEqual(state["retryable"], 0)
            metrics = core.render_prometheus({"ingest": state}).decode()
            self.assertIn("dipink_ingest_quarantined_notes 1.0", metrics)
            with patch.object(ingest, "_ingest_note", AsyncMock()) as attempted:
                await ingest._run_pending_batch(g, assessment, [note], group_id="main", prefix="fixture")
                attempted.assert_not_called()
            self.assertEqual(await ingest_failures.retry(g.driver, "other-group", slug), 0)
            self.assertEqual(await ingest_failures.retry(g.driver, "main", slug), 1)
            assessment = await ingest.assess_ingest(g.driver, [note], upgrade_legacy=False)
            self.assertEqual(assessment.retryable, {slug})
            self.assertEqual(assessment.done, set())
            async def succeed(*args, **kwargs):
                g.driver.episodes.append(episode(slug, path.read_text(), uuid="success"))
                return SimpleNamespace(episode=SimpleNamespace(uuid="success"))
            with patch.object(ingest, "add_episode_with_retry", side_effect=succeed):
                await ingest._run_pending_batch(g, assessment, [note], group_id="main", prefix="fixture")
            self.assertEqual((await ingest.assess_ingest(g.driver, [note])).done, {slug})
            self.assertEqual(g.driver.failures, {})
            self.assertEqual(path.read_text(), "PRIVATE_FIXTURE note remains unchanged")

    async def test_content_change_reopens_and_resets_failure_budget_without_status_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = make_note(Path(tmp), "2026-01-01-000000-fixture", "first fixture")
            _, slug, path = note
            g = FakeGraph()
            for _ in range(3):
                await ingest_failures.record(g.driver, "main", slug, ingest.note_content_hash(path), "schema")
            self.assertEqual((await ingest.assess_ingest(g.driver, [note])).quarantined, {slug})
            path.write_text("changed fixture")
            g.driver.calls.clear()
            assessment = await ingest.assess_ingest(g.driver, [note], upgrade_legacy=False)
            self.assertEqual(assessment.retryable, {slug})
            self.assertEqual(assessment.quarantined, set())
            self.assertTrue(all(params.get("routing_") == "r" for _, params in g.driver.calls))
            count = await ingest_failures.record(g.driver, "main", slug, ingest.note_content_hash(path), "schema")
            self.assertEqual(count, 1)
            self.assertEqual((await ingest.assess_ingest(g.driver, [note], group_id="other")).quarantined, set())
            self.assertNotIn("changed fixture", str(g.driver.failures))

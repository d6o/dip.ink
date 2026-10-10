from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import tempfile

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")

import ingest  # noqa: E402
import ingest_metrics  # noqa: E402
from loops import ingest_report  # noqa: E402


class Usage:
    prompt_tokens = 1200
    completion_tokens = 300


def completion(text: str, finish: str = "stop"):
    return SimpleNamespace(
        model="router-picked",
        usage=Usage(),
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
    )


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)

    async def create(self, **kwargs):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class UnionTests(unittest.TestCase):
    def test_overlapping_intervals_count_once(self):
        self.assertEqual(ingest_metrics._union_seconds([(0, 2), (1, 3), (5, 6)]), 4)
        self.assertEqual(ingest_metrics._union_seconds([]), 0)


class FailureKindTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(ingest_metrics.failure_kind(json.JSONDecodeError("Unterminated string", "x", 1)), "json_truncated")
        self.assertEqual(ingest_metrics.failure_kind(ingest.EmptyResponseError("LLM returned an empty response")), "empty")
        err = Exception("boom")
        err.status_code = 503
        self.assertEqual(ingest_metrics.failure_kind(err), "provider_5xx")


class ClientInstrumentationTests(unittest.TestCase):
    def make_client(self, replies):
        client = ingest.CompactSchemaClient.__new__(ingest.CompactSchemaClient)
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(replies)))
        client.temperature = 0
        client.model_ladder = ingest.OrderedModelFallback(["m1"], context="test")
        client._build_response_format = lambda model: None
        client._strip_code_fences = lambda text: text
        client._clean_input = lambda text: text
        return client

    def run_call(self, client, metrics_obj):
        async def go():
            token = ingest_metrics._current.set(metrics_obj)
            ptoken = ingest_metrics.set_prompt("extract_nodes")
            try:
                return await client._generate_response([SimpleNamespace(role="user", content="hi")])
            finally:
                ingest_metrics.reset_prompt(ptoken)
                ingest_metrics._current.reset(token)
        return asyncio.run(go())

    def test_ok_call_records_tokens_model_and_prompt(self):
        metrics = ingest_metrics.NoteMetrics(slug="s", body_chars=10)
        result = self.run_call(self.make_client([completion('{"a": 1}')]), metrics)
        self.assertEqual(result, {"a": 1})
        call = metrics.llm_calls[0]
        self.assertEqual(call["prompt"], "extract_nodes")
        self.assertEqual(call["model"], "router-picked")
        self.assertEqual(call["outcome"], "ok")
        self.assertEqual((call["in_tok"], call["out_tok"], call["finish"]), (1200, 300, "stop"))

    def test_truncated_json_is_recorded_and_still_raised(self):
        metrics = ingest_metrics.NoteMetrics(slug="s", body_chars=10)
        with self.assertRaises(json.JSONDecodeError):
            self.run_call(self.make_client([completion('{"a": "unterminated', finish="length")]), metrics)
        call = metrics.llm_calls[0]
        self.assertEqual(call["outcome"], "json_truncated")
        self.assertEqual(call["finish"], "length")
        summary = metrics.summary(outcome="fail", error=json.JSONDecodeError("Unterminated", "x", 0))
        self.assertEqual(summary["llm_truncated"], 1)
        self.assertEqual(summary["llm_failures"], {"json_truncated": 1})
        self.assertEqual(summary["by_prompt"]["extract_nodes"]["fail"], 1)

    def test_no_metrics_context_changes_nothing(self):
        result = asyncio.run(self.make_client([completion('{"b": 2}')])._generate_response(
            [SimpleNamespace(role="user", content="hi")]))
        self.assertEqual(result, {"b": 2})


class IngestNoteRecordTests(unittest.TestCase):
    def test_success_and_failure_each_emit_and_persist_one_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "n.md"
            path.write_text("---\ntopic: t\n---\nhello world\n", encoding="utf-8")
            persisted, emitted = [], []

            async def fake_persist(driver, record):
                persisted.append(record)

            async def fake_prior(driver, slug):
                return 2

            async def add_ok(**kw):
                return SimpleNamespace(episode=SimpleNamespace(uuid="u1"), nodes=[1, 2], edges=[1])

            async def add_fail(**kw):
                raise json.JSONDecodeError("Unterminated string", "x", 5)

            async def mark(*a, **k):
                return None

            ts = datetime(2026, 10, 10, tzinfo=timezone.utc)
            with mock.patch.object(ingest_metrics, "persist", fake_persist), \
                 mock.patch.object(ingest_metrics, "prior_attempts", fake_prior), \
                 mock.patch.object(ingest_metrics, "emit", emitted.append), \
                 mock.patch.object(ingest, "_mark_episode_complete", mark):
                g = SimpleNamespace(driver=object(), add_episode=add_ok)
                asyncio.run(ingest._ingest_note(g, ts, "slug-a", path, group_id="g"))
                g.add_episode = add_fail
                with self.assertRaises(json.JSONDecodeError):
                    asyncio.run(ingest._ingest_note(g, ts, "slug-b", path, group_id="g"))

        self.assertEqual([r["outcome"] for r in persisted], ["ok", "fail"])
        self.assertEqual(persisted[0]["attempt"], 3)
        self.assertEqual((persisted[0]["nodes"], persisted[0]["edges"]), (2, 1))
        self.assertEqual(persisted[1]["error_kind"], "json_truncated")
        self.assertNotIn("hello world", json.dumps(persisted))
        self.assertEqual(len(emitted), 2)


class ReportTests(unittest.TestCase):
    def test_aggregate(self):
        rows = [
            {"slug": "a", "at": "2026-10-10T10:00:00Z", "outcome": "ok", "wall_s": 300, "llm_wall_s": 200,
             "llm_sum_s": 260, "embed_wall_s": 5, "neo4j_wall_s": 20, "idle_s": 75, "llm_calls": 12,
             "body_chars": 3000, "by_prompt": {"extract_nodes": {"calls": 2, "s": 80, "fail": 0,
             "in_tok": 4000, "out_tok": 900, "max_out_tok": 500}}, "by_model": {"glm": {"calls": 12, "s": 260, "fail": 0}},
             "llm_failures": {}},
            {"slug": "b", "at": "2026-10-10T11:00:00Z", "outcome": "fail", "wall_s": 100, "llm_wall_s": 50,
             "llm_sum_s": 50, "embed_wall_s": 0, "neo4j_wall_s": 0, "idle_s": 50, "llm_calls": 3,
             "body_chars": 12000, "error_kind": "json_truncated", "error": "JSONDecodeError: x",
             "by_prompt": {}, "by_model": {}, "llm_failures": {"json_truncated": 3}},
        ]
        out = ingest_report.aggregate(rows)
        self.assertEqual(out["outcomes"], {"ok": 1, "fail": 1})
        self.assertEqual(out["failed_attempt_time_share"], 0.25)
        self.assertEqual(out["prompts"][0]["prompt"], "extract_nodes")
        self.assertEqual(out["llm_failure_kinds"], {"json_truncated": 3})


if __name__ == "__main__":
    unittest.main()

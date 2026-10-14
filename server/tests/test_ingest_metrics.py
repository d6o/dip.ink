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
    completion_tokens_details = SimpleNamespace(reasoning_tokens=250)
    prompt_tokens_details = SimpleNamespace(cached_tokens=128)


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
        self.assertEqual((call["reason_tok"], call["cached_tok"]), (250, 128))
        summary = metrics.summary(outcome="ok")
        self.assertEqual(summary["llm_reason_tok"], 250)
        self.assertEqual(summary["by_prompt"]["extract_nodes"]["reason_tok"], 250)

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


class OutageWaitTests(unittest.TestCase):
    def client(self, replies):
        return ClientInstrumentationTests.make_client(None, replies)

    def test_provider_503_is_waited_out_on_the_same_call(self):
        err = Exception("all batch routes are cooling down")
        err.status_code = 503
        client = self.client([err, err, completion('{"ok": true}')])
        metrics = ingest_metrics.NoteMetrics(slug="s", body_chars=1)
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)

        async def go():
            token = ingest_metrics._current.set(metrics)
            try:
                return await client._generate_response([SimpleNamespace(role="user", content="hi")])
            finally:
                ingest_metrics._current.reset(token)

        with mock.patch.object(ingest, "LLM_RETRY_WAITS", (15.0, 30.0, 60.0)), \
             mock.patch.object(ingest.asyncio, "sleep", fake_sleep):
            self.assertEqual(asyncio.run(go()), {"ok": True})
        self.assertEqual(sleeps, [15.0, 30.0])
        summary = metrics.summary(outcome="ok")
        self.assertEqual((summary["llm_outage_waits"], summary["llm_outage_wait_s"]), (2, 45.0))

    def test_non_recoverable_error_is_not_retried(self):
        err = Exception("bad request")
        err.status_code = 400
        client = self.client([err])
        with mock.patch.object(ingest.asyncio, "sleep") as sleep:
            with self.assertRaises(Exception):
                asyncio.run(client._generate_response([SimpleNamespace(role="user", content="hi")]))
        sleep.assert_not_called()

    def test_wait_schedule_parsing(self):
        with mock.patch.dict(os.environ, {"X_WAITS": "1, 2,3"}):
            self.assertEqual(ingest._wait_schedule("X_WAITS", "9"), (1.0, 2.0, 3.0))
        with mock.patch.dict(os.environ, {"X_WAITS": "bad"}):
            self.assertEqual(ingest._wait_schedule("X_WAITS", "9,8"), (9.0, 8.0))


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


class QueryKindTests(unittest.TestCase):
    def test_kinds(self):
        k = ingest_metrics.query_kind
        self.assertEqual(k("MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) WITH e, vector.similarity.cosine(e.fact_embedding, $v) AS s"), "cosine_scan_edges")
        self.assertEqual(k("MATCH (n:Entity) WITH n, vector.similarity.cosine(n.name_embedding, $v) AS s"), "cosine_scan_nodes")
        self.assertEqual(k("CALL db.index.fulltext.queryRelationships('edge_name_and_fact', $q)"), "fulltext")
        self.assertEqual(k("UNWIND $edges AS edge MERGE (a)-[r:RELATES_TO]->(b)"), "write")
        self.assertEqual(k("MATCH (e:Episodic) RETURN e"), "read_episodes")

    def test_summary_groups_by_kind(self):
        m = ingest_metrics.NoteMetrics(slug="s", body_chars=1)
        m.record_neo4j(m.started, m.started + 2.0, "cosine_scan_edges")
        m.record_neo4j(m.started, m.started + 1.0, "cosine_scan_edges")
        out = m.summary(outcome="ok")["neo4j_by_kind"]["cosine_scan_edges"]
        self.assertEqual((out["n"], out["s"], out["max_s"]), (2, 3.0, 2.0))


class VectorAuditMetricTests(unittest.TestCase):
    def test_audits_are_counted_per_note(self):
        m = ingest_metrics.NoteMetrics(slug="s", body_chars=1)
        token = ingest_metrics._current.set(m)
        try:
            ingest_metrics.record_vector_audit("NODE", True, 1.0)
            ingest_metrics.record_vector_audit("RELATIONSHIP", False, 0.9)
        finally:
            ingest_metrics._current.reset(token)
        out = m.summary(outcome="ok")
        self.assertEqual((out["vector_audits"], out["vector_audit_mismatches"], out["vector_audit_min_recall"]), (2, 1, 0.9))


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


class PayloadFreeErrorTests(unittest.IsolatedAsyncioTestCase):
    MARKER = 'PRIVATE_MODEL_INPUT_DO_NOT_RECORD'

    def validation_error(self):
        from pydantic import BaseModel, ValidationError
        class Output(BaseModel):
            count: int
        try:
            Output(count=self.MARKER)
        except ValidationError as error:
            return error
        raise AssertionError('Expected a synthetic validation error')

    async def test_validation_input_never_reaches_metric_log_or_store(self):
        import io
        from contextlib import redirect_stdout
        error = self.validation_error()
        self.assertIn(self.MARKER,str(error))
        record=ingest_metrics.NoteMetrics(slug='fixture',body_chars=0).summary(outcome='fail',error=error)
        self.assertEqual(record['error'],'ValidationError (schema); validation_errors=1')
        driver=SimpleNamespace(client=SimpleNamespace(execute_query=mock.AsyncMock()))
        out=io.StringIO()
        with redirect_stdout(out):
            ingest_metrics.emit(record)
            await ingest_metrics.persist(driver,record)
        self.assertNotIn(self.MARKER,out.getvalue())
        self.assertNotIn(self.MARKER,json.dumps(driver.client.execute_query.call_args.kwargs))

    async def test_span_receives_only_safe_exception_and_original_type_survives(self):
        from contextlib import contextmanager
        error=self.validation_error()
        span=mock.Mock()
        auto=[]
        @contextmanager
        def scope(name):
            try:
                yield span
            except Exception as raw:
                auto.append(raw)
                raise
        client=ClientInstrumentationTests.make_client(None,[])
        client._apply_attribute_extraction_preamble=lambda *a:None
        client.structured_output_mode='json_schema'
        client.max_tokens=400
        client.tracer=SimpleNamespace(start_span=scope)
        client._generate_response_with_retry=mock.AsyncMock(side_effect=error)
        with self.assertRaises(type(error)) as caught:
            await client.generate_response([SimpleNamespace(role='system',content='fixture')])
        self.assertIs(caught.exception,error)
        self.assertEqual(auto,[])
        self.assertNotIn(self.MARKER,str(span.set_status.call_args))
        safe=span.record_exception.call_args.args[0]
        self.assertNotIn(self.MARKER,str(safe))
        self.assertIsNone(safe.__traceback__)
        self.assertIsNone(safe.__context__)

    async def test_historical_report_output_ignores_raw_error(self):
        row={'slug':'fixture','at':'2026-10-14T00:00:00Z','outcome':'fail','wall_s':1,
             'error_kind':'schema','error':self.MARKER,'body_chars':0}
        report=ingest_report.aggregate([row])
        self.assertNotIn(self.MARKER,json.dumps(report))
        self.assertEqual(report['errors'],[('schema',1)])
        driver=mock.Mock()
        driver.execute_query.return_value=([{'m':row}],None,None)
        with mock.patch('neo4j.GraphDatabase.driver',return_value=driver):
            rows=ingest_report.fetch(.05)
        self.assertNotIn(self.MARKER,json.dumps(rows))

    async def test_ingest_failure_log_does_not_print_exception_repr(self):
        import io
        from contextlib import redirect_stderr
        from datetime import datetime,timezone
        error=self.validation_error();out=io.StringIO()
        with mock.patch.object(ingest,'_prepare_note_for_reingest',new=mock.AsyncMock()), \
             mock.patch.object(ingest,'_ingest_note',new=mock.AsyncMock(side_effect=error)), redirect_stderr(out):
            result=await ingest._run_pending_batch(SimpleNamespace(),{},[(datetime.now(timezone.utc),'fixture',Path('/tmp/unused'))],group_id='fixture',prefix='fixture')
        self.assertNotIn(self.MARKER,out.getvalue())
        self.assertIn('ValidationError (schema)',out.getvalue())
        self.assertEqual(result,(0,1,False))

    async def test_provider_error_keeps_status_without_body(self):
        error=RuntimeError(self.MARKER);error.status_code=503
        self.assertEqual(ingest_metrics.error_summary(error),'RuntimeError (provider_5xx); status=503')


if __name__ == "__main__":
    unittest.main()

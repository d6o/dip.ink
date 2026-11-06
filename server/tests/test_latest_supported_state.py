"""Policy/data-flow regressions, not certification of model or backend semantics."""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
import graph
from evaluation_cases.present_state_v1 import build, reader_packet

OLD = "2020-01-01-120000-observed-state"
NEW = "2026-11-05-120000-new-record"


def packet(old, new):
    return {"facts": [], "entities": [], "source_excerpt": None,
            "semantic_notes": [{"name": OLD, "content": old}, {"name": NEW, "content": new}]}


class LatestSupportedPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_present_state_fixture_support_reaches_the_existing_reader(self):
        # Gold metadata is used only for this input-coverage assertion. It is not
        # put into packets or used to produce a simulated correct answer.
        for case in build()["cases"]:
            with self.subTest(case=case["id"]):
                incoming = reader_packet(case)
                async def inspect(question, encoded):
                    visible = json.loads(encoded)
                    names = set(graph._readable_source_texts(visible))
                    self.assertTrue(set(case["grader"]["supporting_source_ids"]).issubset(names))
                    self.assertEqual(question, case["question"])
                    self.assertNotIn("grader", encoded)
                    self.assertIsNone(visible["temporal_context"]["window_seconds"])
                    return {"answer": None, "confidence": "not_found", "sources": []}
                self.assertTrue(graph._current_state_question(case["question"]))
                with patch.object(graph, "_assemble_packet", AsyncMock(return_value=incoming)), \
                     patch.object(graph, "_distill", side_effect=inspect) as reader, patch.object(graph, "_record_query"):
                    await graph._graph_answer_impl(case["question"])
                reader.assert_awaited_once()

    def test_publication_plan_or_other_setting_does_not_numerically_supersede_deployment(self):
        old = "ServiceLedger production deployment v1.2.1 was completed and verified."
        for newer in (
            "ServiceLedger package v1.2.9 was published only; production deployment was not changed.",
            "ServiceLedger staging deployment v1.2.9 was completed; production was not changed.",
            "ServiceLedger audit component v1.2.9 was deployed; the production application setting was not changed.",
            "ServiceLedger plans production deployment v1.2.9; activation has not run.",
        ):
            with self.subTest(kind=newer.split()[1]):
                evidence = packet(old, newer)
                evidence["temporal_context"] = graph._temporal_context(evidence)
                parsed = {"answer": old, "sources": [OLD], "confidence": "high"}
                out, grounded, _ = graph._validate_distilled_answer(parsed, evidence,
                    "Which ServiceLedger deployment is in production?")
                self.assertTrue(grounded)
                self.assertIn("v1.2.1", out["answer"])
                self.assertEqual(out["sources"], [OLD])
                self.assertEqual(out["as_of"], "2020-01-01T12:00:00Z")

    def test_unsupported_actual_deployment_identifiers_still_reject(self):
        evidence = packet("ServiceLedger deployed registry.example/app:v1.2.1.", "ServiceLedger changed its report format.")
        evidence["temporal_context"] = graph._temporal_context(evidence)
        supported, _, _ = graph._validate_distilled_answer(
            {"answer": "ServiceLedger deployment uses v1.2.1.", "sources": [OLD], "confidence": "high"},
            evidence, "Which ServiceLedger deployment is in production?")
        self.assertIsNotNone(supported["answer"])
        for identifier in ("registry.example/other:v1.2.1", "12345678-1234-1234-1234-123456789abc", "sha256:" + "a" * 64):
            parsed = {"answer": f"ServiceLedger deployed {identifier}.", "sources": [OLD], "confidence": "high"}
            out, _, _ = graph._validate_distilled_answer(parsed, evidence, "Which ServiceLedger deployment is in production?")
            self.assertIsNone(out["answer"])
        undated = {"semantic_notes": [{"name": "ServiceLedger", "content": "ServiceLedger deployed v1.2.1."}]}
        undated["temporal_context"] = graph._temporal_context(undated)
        out, _, _ = graph._validate_distilled_answer({"answer": "ServiceLedger deployed v1.2.1.", "sources": ["ServiceLedger"], "confidence": "high"},
                                                    undated, "Which ServiceLedger deployment is in production?")
        self.assertIsNone(out["answer"])

    async def test_conflict_policy_preserves_both_reports_and_accepts_uncertainty_in_either_order(self):
        case = next(c for c in build()["cases"] if c["grader"]["outcome"] == "uncertain")
        evidence = reader_packet(case)
        for reverse in (False, True):
            incoming = copy.deepcopy(evidence)
            if reverse:
                incoming["semantic_notes"].reverse()
            async def inspect(question, encoded):
                names = set(graph._readable_source_texts(json.loads(encoded)))
                self.assertTrue(set(case["grader"]["supporting_source_ids"]).issubset(names))
                return {"answer": None, "confidence": "not_found", "sources": [], "escalate": True}
            with patch.object(graph, "_assemble_packet", AsyncMock(return_value=incoming)), \
                 patch.object(graph, "_distill", side_effect=inspect), patch.object(graph, "_record_query"):
                result = await graph._graph_answer_impl(case["question"])
            self.assertIsNone(result["answer"])
        self.assertIn("mutually exclusive active-state reports", graph._DISTILL_SYSTEM)
        self.assertIn("Do not break a tie by capture time, list order, mention count, or version magnitude", graph._DISTILL_SYSTEM)
        # No model accuracy claim follows from this mocked uncertainty control.


class ValidityIntervalTests(unittest.IsolatedAsyncioTestCase):
    def test_known_bounds_and_boundaries(self):
        now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
        for start, end, expected in (
            (now - timedelta(days=1), None, True), (now + timedelta(days=1), None, False),
            (now - timedelta(days=1), now + timedelta(days=1), True),
            (now - timedelta(days=1), now, False), (now, now + timedelta(days=1), True),
            (now, now, False), (None, None, True), (None, now + timedelta(days=1), True),
            ("malformed", None, False), (None, "malformed", False), (0, None, False),
            ("2026-11-05T12:00:00", "2026-11-07T12:00:00", True),
        ):
            with self.subTest(start=str(start), end=str(end)):
                self.assertEqual(graph._valid_at_window(SimpleNamespace(valid_at=start, invalid_at=end), now)["current"], expected)

    async def test_public_graph_search_uses_interval_flags_without_new_fields(self):
        edges = [SimpleNamespace(fact="fixture", valid_at=start, invalid_at=end,
                                 episodes=[SimpleNamespace(name="fixture-source")]) for start, end in
                 (("2099-01-01T00:00:00Z", None), ("2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z"),
                  (None, None), ("malformed", None))]
        client = SimpleNamespace(search_=AsyncMock(return_value=SimpleNamespace(edges=edges, nodes=[], episodes=[])))
        with patch.object(graph, "_get_graph", AsyncMock(return_value=client)), \
             patch.object(graph, "_wiki_semantic_hits", AsyncMock(return_value=[])), patch.object(graph, "_record_query"):
            result = await graph.graph_search("fixture")
        self.assertEqual([fact["current"] for fact in result["facts"]], [False, True, True, False])
        self.assertTrue(all(set(fact) == {"fact", "source_slug", "valid_at", "invalid_at", "current"} for fact in result["facts"]))

    async def test_changes_uses_one_request_time_and_excludes_future_events(self):
        driver = SimpleNamespace(execute_query=AsyncMock(side_effect=[([], None, None), ([], None, None)]))
        with patch.object(graph, "_get_graph", AsyncMock(return_value=SimpleNamespace(driver=driver))), patch.object(graph, "_record_query"):
            await graph.graph_changes("fixture", 14)
        new, ended = driver.execute_query.call_args_list
        self.assertIn("r.valid_at <= datetime($as_of)", new.args[0])
        self.assertIn("r.invalid_at > datetime($as_of)", new.args[0])
        self.assertIn("r.invalid_at <= datetime($as_of)", ended.args[0])
        self.assertEqual(new.kwargs["as_of"], ended.kwargs["as_of"])
        self.assertEqual(new.kwargs["routing_"], "r")

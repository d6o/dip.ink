"""Offline experiment checks with synthetic vectors, packets, and completions."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
import graph
import paired_retrieval_eval as paired
from evaluate_answers import hash_json

MODEL = "fixture-embedding"
SOURCE = "2026-10-01-120000-fixture-source"
ARTIFACT = "2026-10-02-120000-fixture-eval-artifact"


def documents():
    return [{"name": SOURCE, "body": "CanvasBridge stores fixture records.", "vector": [1., 0.], "embedding_model": MODEL},
            {"name": ARTIFACT, "body": "ARTIFACT_PRIVATE_BODY", "vector": [.9, .1], "embedding_model": MODEL}]


def case(identity="one"):
    question = "What does CanvasBridge store? " + "Provide only recorded evidence. " * 9
    docs = documents()
    wiki = paired.wiki_packet(question, [1., 0.], docs, embedding_model=MODEL)
    hybrid = {"query": question, "facts": [{"fact": docs[0]["body"], "source_slug": SOURCE, "current": True},
                                          {"fact": docs[1]["body"], "source_slug": ARTIFACT, "current": True}],
              "source_excerpt": {"slug": ARTIFACT, "content": docs[1]["body"]},
              "semantic_notes": copy.deepcopy(wiki["semantic_notes"]),
              "entities": [{"name": "artifact aggregate", "summary": "ARTIFACT_PRIVATE_BODY"}],
              "expected_answer": "GOLD_ONLY"}
    return {"id": identity, "question": question, "embedding_model": MODEL, "query_vector": [1., 0.],
            "query_vector_sha256": hash_json([1., 0.]), "packets": {"hybrid": hybrid, "wiki_only": wiki},
            "expected_claims": ["GOLD_ONLY"]}


class PairedEvaluationTests(unittest.IsolatedAsyncioTestCase):
    def test_wiki_vector_arm_has_no_graph_or_embedding_call_and_uses_whole_corpus(self):
        with patch.object(graph, "_get_graph", AsyncMock(side_effect=AssertionError)) as get_graph, \
             patch.object(graph, "_wiki_semantic_hits", AsyncMock(side_effect=AssertionError)) as search:
            packet = paired.wiki_packet("CanvasBridge", [1., 0.], documents(), embedding_model=MODEL)
        get_graph.assert_not_called(); search.assert_not_called()
        self.assertEqual([h["name"] for h in packet["semantic_notes"]], [SOURCE, ARTIFACT])
        self.assertEqual(packet["facts"], [])
        with self.assertRaises(ValueError):
            paired.wiki_packet("CanvasBridge", [1., 0., 0.], documents(), embedding_model=MODEL)
        with self.assertRaises(ValueError):
            paired.wiki_packet("CanvasBridge", [1., 0.], documents(), embedding_model="different")

    async def test_frozen_pairs_share_prompt_settings_exclusions_and_do_not_mutate(self):
        cases = [case()]
        before = copy.deepcopy(cases)
        calls = []
        async def complete(**kwargs):
            calls.append(kwargs)
            return {"model": "fixed-answer-model", "content": json.dumps({
                "answer": "CanvasBridge stores fixture records.", "confidence": "high", "sources": [SOURCE]})}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "paired.jsonl"
            console = io.StringIO()
            with contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
                result = await paired.run_pairs(cases, documents(), embedding_model=MODEL,
                    settings=paired.AnswerSettings("fixed-answer-model"), complete=complete, output=output,
                    exclusions=[ARTIFACT], corpus_version="fixture-snapshot")
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result["expected_arm_receipts"], 2)
            self.assertEqual(len(records), 3)
            self.assertEqual(result["model_calls"], 2)
            self.assertEqual(result["counts"]["hybrid"]["answer"], 1)
            self.assertEqual(console.getvalue(), "")
            for row in records[1:]:
                self.assertEqual(row["question"], cases[0]["question"])
                self.assertIn(ARTIFACT, row["exclusion_report"]["excluded_sources"])
                self.assertNotIn("ARTIFACT_PRIVATE_BODY", json.dumps(row))
                self.assertEqual(row["grounding_action"], "accepted")
                self.assertIn("fixture records", row["raw_answer"])
            self.assertEqual(cases, before)
            self.assertEqual(len(calls), 2)
            for call in calls:
                self.assertEqual({k: v for k, v in call.items() if k != "messages"},
                                 {"model": "fixed-answer-model", "temperature": 0, "max_tokens": 400, "seed": 0})
                self.assertEqual(call["messages"][0]["content"], graph._DISTILL_SYSTEM)
                self.assertIn(cases[0]["question"], call["messages"][1]["content"])
                self.assertNotIn("GOLD_ONLY", json.dumps(call))
                self.assertNotIn("ARTIFACT_PRIVATE_BODY", json.dumps(call))
            with self.assertRaises(FileExistsError):
                await paired.run_pairs(cases, documents(), embedding_model=MODEL,
                    settings=paired.AnswerSettings("fixed-answer-model"), complete=complete, output=output,
                    exclusions=[ARTIFACT], corpus_version="fixture-snapshot")
            second = Path(tmp) / "replay.jsonl"
            await paired.run_pairs(cases, documents(), embedding_model=MODEL,
                settings=paired.AnswerSettings("fixed-answer-model"), complete=complete, output=second,
                exclusions=[ARTIFACT], corpus_version="fixture-snapshot")
            replay = [json.loads(line) for line in second.read_text().splitlines()]
            self.assertEqual([r["packet_sha256"] for r in records[1:]], [r["packet_sha256"] for r in replay[1:]])
            self.assertEqual(calls[:2], calls[2:])

    def test_same_cap_and_existing_temporal_and_subject_checks(self):
        docs = paired.corpus_map(documents(), MODEL)
        docs[SOURCE]["body"] = "CanvasBridge " + "x" * 2487
        for arm in paired.ARMS:
            frozen = case()["packets"][arm]
            frozen["semantic_notes"] = [{"name": SOURCE, "content": "CanvasBridge " + "x" * 10000} for _ in range(20)]
            _, model_packet, _, report = paired.prepare(case()["question"], frozen, docs, {ARTIFACT})
            self.assertLessEqual(report["readable_chars_before_pruning"], 20000)
            count = sum(len(h.get("content", "")) for h in model_packet["semantic_notes"])
            count += sum(len(f.get("fact", "")) for f in model_packet["facts"])
            self.assertLessEqual(count, 20000)
        frozen = case()["packets"]["hybrid"]
        frozen["temporal_candidates"] = [{"name": "2026-12-01-120000-later", "description": "CanvasBridge later record"}]
        docs["2026-12-01-120000-later"] = {"body": ""}
        _, model_packet, abstain, _ = paired.prepare("What is CanvasBridge's current version?", frozen, docs, {ARTIFACT})
        self.assertTrue(abstain)
        self.assertEqual(model_packet["temporal_context"]["eligible_sources"], [])

    async def test_raw_completion_is_not_truncated_by_validation_display_cap(self):
        raw = json.dumps({"answer": "CanvasBridge " + "x" * 3000, "confidence": "high", "sources": [SOURCE]})
        async def complete(**kwargs):
            return {"model": "fixed", "content": raw}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "capture.jsonl"
            await paired.run_pairs([case()], documents(), embedding_model=MODEL,
                settings=paired.AnswerSettings("fixed"), complete=complete, output=output,
                exclusions=[ARTIFACT], corpus_version="fixture")
            rows = [json.loads(line) for line in output.read_text().splitlines()][1:]
            for row in rows:
                self.assertEqual(row["raw_answer"], raw)
                self.assertLessEqual(len(row["result"]["answer"]), 2000)

    async def test_backend_model_drift_is_reported_without_hidden_retry(self):
        models = iter(["fixed", "different"])
        async def complete(**kwargs):
            return {"model": next(models), "content": '{"answer":null,"confidence":"not_found","sources":[]}'}
        with tempfile.TemporaryDirectory() as tmp:
            result = await paired.run_pairs([case()], documents(), embedding_model=MODEL,
                settings=paired.AnswerSettings("fixed"), complete=complete, output=Path(tmp) / "capture.jsonl",
                exclusions=[ARTIFACT], corpus_version="fixture")
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["reported_model_overrides"], 1)
        self.assertEqual(result["reported_model_mismatch_cases"], 1)

    async def test_errors_and_abstentions_remain_in_every_arm_denominator(self):
        calls = 0
        async def complete(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("PRIVATE_ERROR_BODY")
            return {"model": "fixed", "content": '{"answer":null,"confidence":"not_found","sources":[]}'}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "results.jsonl"
            result = await paired.run_pairs([case("a"), case("b")], documents(), embedding_model=MODEL,
                settings=paired.AnswerSettings("fixed"), complete=complete, output=output,
                exclusions=[ARTIFACT], corpus_version="fixture")
            self.assertEqual(result["expected_arm_receipts"], 4)
            self.assertTrue(all(sum(count.values()) == 2 for count in result["counts"].values()))
            self.assertEqual(sum(c["error"] for c in result["counts"].values()), 1)
            self.assertEqual(sum(c["abstention"] for c in result["counts"].values()), 3)
            self.assertNotIn("PRIVATE_ERROR_BODY", output.read_text())

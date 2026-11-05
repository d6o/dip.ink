"""Synthetic SQLite/ranker fixtures. No external provider or database access."""
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
import offline_candidates as offline
import paired_retrieval_eval as paired
from evaluate_answers import hash_json

MODEL = "fixture-embedding"


def docs():
    return [
        {"name": "2026-01-01-000000-a", "title": "Synthetic reference", "date": "2026-01-01", "heading": "Scope",
         "body": "Parent context limits this to a fixture.\n\n## Keyword section\nquartz violet marker.\n\n## Footer\nParent footer must remain.",
         "vector": [1., 0.], "embedding_model": MODEL},
        {"name": "2026-01-02-000000-b", "title": "UniqueMetadataToken", "date": "2026-01-02",
         "body": "Unrelated text.", "vector": [.9, .1], "embedding_model": MODEL},
    ]


class KeywordTests(unittest.TestCase):
    def test_real_bm25_sections_metadata_and_deterministic_order(self):
        corpus = docs()
        with offline.KeywordIndex(corpus) as index, offline.KeywordIndex(list(reversed(corpus))) as second:
            statements = []
            index.connection.set_trace_callback(statements.append)
            hits = index.search("quartz violet")
            self.assertEqual(hits, second.search("quartz violet"))
            self.assertEqual(hits[0]["id"], corpus[0]["name"])
            self.assertEqual(hits[0]["heading"], "Keyword section")
            self.assertEqual(hits[0]["date"], "2026-01-01")
            self.assertLess(hits[0]["bm25_score"], 0)
            self.assertTrue(any("bm25(" in statement and "MATCH" in statement for statement in statements))
            self.assertEqual(index.search("UniqueMetadataToken")[0]["id"], corpus[1]["name"])
            self.assertEqual(index.search("\" OR *; --"), [])
        with offline.KeywordIndex(corpus, index_metadata=False) as index:
            self.assertEqual(index.search("UniqueMetadataToken"), [])
            self.assertEqual(index.search("quartz")[0]["heading"], "Keyword section")

    def test_disk_index_is_private_and_cannot_overwrite_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keyword.sqlite3"
            with offline.KeywordIndex(docs(), path=path) as index:
                self.assertTrue(index.search("quartz"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                offline.KeywordIndex(docs(), path=path)

    def test_section_fanout_does_not_starve_distinct_parent_candidates(self):
        corpus = docs()
        corpus[0]["body"] = "\n".join(f"## Section {i}\nquartz\n" for i in range(100))
        corpus[1]["body"] = "quartz"
        with offline.KeywordIndex(corpus) as index:
            self.assertEqual({h["id"] for h in index.search("quartz")}, {d["name"] for d in corpus})

    def test_top50_rrf60_and_gold_metadata_never_affects_selection(self):
        corpus = [dict(docs()[0], name=f"2026-01-01-000000-source-{i:03d}", vector=[1., i / 100]) for i in range(65)]
        annotated = copy.deepcopy(corpus)
        for doc in annotated:
            doc["expected_answer"] = "GOLD_ONLY"
            doc["gold_source_ids"] = [corpus[-1]["name"]]
        with offline.KeywordIndex(corpus) as index, offline.KeywordIndex(annotated) as other:
            first = offline.select_candidates("quartz", [1., 0.], corpus, index, embedding_model=MODEL)
            second = offline.select_candidates("quartz", [1., 0.], annotated, other, embedding_model=MODEL)
        self.assertEqual(first["candidates"], second["candidates"])
        self.assertEqual(len(first["vector_ids"]), 50)
        self.assertEqual(len(first["bm25_ids"]), 50)
        self.assertEqual(len(first["candidates"]), 50)
        self.assertLessEqual(first["ranker_body_chars"], 20000)
        self.assertAlmostEqual(first["candidates"][0]["rrf_score"], 2 / 61)
        self.assertNotIn("GOLD_ONLY", json.dumps(first["candidates"]))

    def test_excluded_parent_keeps_original_ranks_and_is_not_sent_to_ranker(self):
        corpus = docs()
        with offline.KeywordIndex(corpus) as index:
            selection = offline.select_candidates("quartz", [1., 0.], corpus, index,
                embedding_model=MODEL, exclusions=[corpus[0]["name"]])
        self.assertEqual(selection["excluded_ids"], [corpus[0]["name"]])
        self.assertEqual([c["id"] for c in selection["candidates"]], [corpus[1]["name"]])
        self.assertEqual(selection["candidates"][0]["vector_rank"], 2)
        self.assertAlmostEqual(selection["candidates"][0]["rrf_score"], 1 / 62)

    def test_vector_only_does_not_call_keyword_search_or_graph(self):
        with patch.object(graph, "_get_graph", AsyncMock(side_effect=AssertionError)) as get:
            result = offline.select_candidates("quartz", [1., 0.], docs(), embedding_model=MODEL, mode="vector")
        get.assert_not_called()
        self.assertEqual(result["bm25_ids"], [])
        self.assertEqual(result["keyword_ms"], 0)
        self.assertIsNone(result["keyword_metadata"])

    def test_parent_body_budget_is_twenty_thousand_and_selection_is_capped_at_eight(self):
        corpus = [dict(docs()[0], name=f"2026-01-01-000000-parent-{i}", body="context " + "x" * 2492,
                       vector=[1., i / 10]) for i in range(10)]
        selection = offline.select_candidates("context", [1., 0.], corpus, embedding_model=MODEL, mode="vector")
        ids = [c["id"] for c in selection["candidates"]]
        packet = offline.expand_parents("context", ids[:8], selection, corpus, embedding_model=MODEL)
        self.assertEqual(sum(len(h["content"]) for h in packet["semantic_notes"]), 20000)
        with self.assertRaises(ValueError):
            offline.expand_parents("context", ids[:9], selection, corpus, embedding_model=MODEL)

    def test_parent_expansion_preserves_original_reference_and_context(self):
        corpus = docs()
        before = copy.deepcopy(corpus)
        with offline.KeywordIndex(corpus) as index:
            selection = offline.select_candidates("quartz", [1., 0.], corpus, index, embedding_model=MODEL)
        packet = offline.expand_parents("quartz", [corpus[0]["name"]], selection, corpus, embedding_model=MODEL)
        hit = packet["semantic_notes"][0]
        self.assertEqual(hit["name"], corpus[0]["name"])
        self.assertEqual(hit["content"], corpus[0]["body"])
        self.assertIn("Parent footer", hit["content"])
        self.assertEqual(hit["source_date"], "2026-01-01")
        self.assertEqual(hit["matched_heading"], "Keyword section")
        self.assertTrue(hit["matched_section_id"])
        with self.assertRaises(ValueError):
            offline.validate_ids([hit["matched_section_id"]], selection["candidates"])
        self.assertEqual(corpus, before)
        changed = copy.deepcopy(corpus); changed[0]["body"] = "changed"
        with self.assertRaises(ValueError):
            offline.expand_parents("quartz", [corpus[0]["name"]], selection, changed, embedding_model=MODEL)


class RankerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_duplicate_malformed_and_oversized_ids_are_rejected_without_retry(self):
        corpus = docs()
        selection = offline.select_candidates("quartz", [1., 0.], corpus, embedding_model=MODEL, mode="vector")
        for raw in (["unknown"], [corpus[0]["name"]] * 2, {"ids": [corpus[0]["name"]]}, "not JSON", [1], ["x"] * 9,
                    RuntimeError("PRIVATE_FIXTURE_ERROR")):
            with self.subTest(raw_type=type(raw).__name__), tempfile.TemporaryDirectory() as tmp:
                ranker = AsyncMock(side_effect=raw) if isinstance(raw, Exception) else AsyncMock(return_value=raw)
                console = io.StringIO()
                with contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
                    receipt = await offline.rerank(selection, ranker=ranker, settings={"model": "fixture-ranker"},
                                                    output=Path(tmp) / "rank.json")
                self.assertEqual(receipt["status"], "error")
                self.assertNotIn("PRIVATE_FIXTURE_ERROR", json.dumps(receipt))
                self.assertNotIn("selected_ids", receipt)
                ranker.assert_awaited_once()
                self.assertEqual(console.getvalue(), "")

    async def test_success_private_receipt_and_callback_separation(self):
        corpus = docs()
        selection = offline.select_candidates("quartz", [1., 0.], corpus, embedding_model=MODEL, mode="vector")
        selection["candidates"][0]["gold_source_ids"] = ["GOLD_ONLY"]
        ranker = AsyncMock(return_value=[corpus[0]["name"]])
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "rank.json"
            receipt = await offline.rerank(selection, ranker=ranker, settings={"model": "fixture-ranker"}, output=output)
            self.assertEqual(receipt["status"], "ok")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(receipt["selection_sha256"], hash_json(selection))
        self.assertNotIn("GOLD_ONLY", json.dumps(ranker.call_args.kwargs))
        self.assertNotIn("messages", ranker.call_args.kwargs)

    async def test_reader_models_receive_identical_frozen_packets_with_explicit_budgets(self):
        corpus = docs()
        packet = paired.wiki_packet("What records exist?", [1., 0.], corpus, embedding_model=MODEL)
        case = {"id": "reader-case", "question": "What records exist?", "query_vector": [1., 0.],
                "query_vector_sha256": hash_json([1., 0.]), "embedding_model": MODEL, "packet": packet}
        before = copy.deepcopy(case)
        calls = []
        async def answer(**kwargs):
            calls.append(kwargs)
            if kwargs["model"] == "fixture-control":
                raise RuntimeError("PRIVATE_FIXTURE_ERROR")
            return {"model": kwargs["model"], "content": '{"answer":null,"confidence":"not_found","sources":[]}'}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "readers.jsonl"
            with patch.object(graph, "_get_graph", AsyncMock(side_effect=AssertionError)) as graph_read, \
                 patch.object(paired, "wiki_packet", side_effect=AssertionError) as retrieve:
                result = await paired.run_readers([case], corpus, embedding_model=MODEL,
                    control=paired.AnswerSettings("fixture-control"), candidate=paired.AnswerSettings("fixture-candidate", max_tokens=1200),
                    complete=answer, output=output, exclusions=[], corpus_version="fixture")
            graph_read.assert_not_called()
            retrieve.assert_not_called()
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(records[0]["experiment"], "reader")
            self.assertEqual(records[1]["packet_sha256"], records[2]["packet_sha256"])
            self.assertNotIn("PRIVATE_FIXTURE_ERROR", output.read_text())
            self.assertEqual(result["counts"]["control"]["error"], 1)
            self.assertEqual(result["counts"]["candidate"]["abstention"], 1)
        self.assertEqual(case, before)
        self.assertEqual(calls[0]["messages"], calls[1]["messages"])
        self.assertEqual([call["max_tokens"] for call in calls], [400, 1200])
        self.assertEqual(len(calls), 2)

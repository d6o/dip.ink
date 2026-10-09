from __future__ import annotations

import json
import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")

import graph
import wiki

OLD = "2026-07-23-005912-curator-fallback"
NEW = "2026-09-02-230850-release-deployed"
LATEST = "2026-10-09-171440-frontmatter-depth"
QUESTION = "What are the latest dip.ink release tags and deployed revisions for the memory server and pi-runner?"


def evidence() -> dict:
    return {
        "query": QUESTION,
        "facts": [
            {"fact": "Memory uses v0.1.6.", "source_slug": OLD, "current": True},
            {"fact": "The release publishes pi-runner:v0.1.11.", "source_slug": NEW, "current": True},
        ],
        "entities": [{"summary": "Memory uses v0.1.6."}],
        "communities": [],
        "source_excerpt": {"slug": OLD, "content": "Memory uses v0.1.6."},
        "semantic_notes": [{
            "name": LATEST, "type": "source", "description": "Patch release",
            "content": "Memory uses v0.1.15 at 02216b4. pi-runner:v0.1.15 is published.",
        }],
    }


def current_packet() -> dict:
    packet = evidence()
    packet["temporal_context"] = graph._temporal_context(packet)
    return packet


class TemporalIntentTests(unittest.TestCase):
    def test_current_state_questions(self):
        for question in (
            QUESTION, "What is the current deployment?", "Which version is the server running?",
            "Does memory still use v0.1.6?", "Qual a versão atual?", "Qual é a última versão?",
            "What is the most recent release?", "What runs now and what was used before?",
            "What version does memory use?", "Which image does mykg pin?",
        ):
            with self.subTest(question=question):
                self.assertTrue(graph._current_state_question(question))

    def test_historical_and_ordinary_questions_remain_ordinary(self):
        for question in (
            "What was the latest release on 2026-07-23?",
            "What is the current deployment as of 2026-07-23?",
            "What was the current deployment?", "Which release added metadata backfill?",
            "What version did memory use?", "What is the current version as of 2026-07-01?",
            "What is the latest-safe cache algorithm?",
            "What does the latest Docker tag mean?", "What is the answer?",
        ):
            with self.subTest(question=question):
                self.assertFalse(graph._current_state_question(question))

    def test_invalid_source_dates_are_not_evidence(self):
        self.assertIsNone(graph._source_time("not-a-dated-source"))
        self.assertIsNone(graph._source_time("2026-99-99-999999-invalid"))


class TemporalGroundingTests(unittest.TestCase):
    def test_only_newest_supported_source_is_eligible(self):
        context = current_packet()["temporal_context"]
        self.assertEqual(context["eligible_sources"], [LATEST])
        self.assertEqual(context["as_of"], "2026-10-09T17:14:40Z")
        self.assertFalse(context["live_verified"])

    def test_old_or_mixed_current_claim_is_rejected(self):
        for sources in ([OLD], [NEW], [OLD, NEW], [OLD, LATEST]):
            with self.subTest(sources=sources):
                result, grounded, action = graph._validate_distilled_answer({
                    "answer": "The latest memory deployment is v0.1.6.",
                    "confidence": "high", "sources": sources, "escalate": False,
                }, current_packet())
                self.assertIsNone(result["answer"])
                self.assertEqual(result["confidence"], "not_found")
                self.assertTrue(result["escalate"])
                self.assertFalse(grounded)
                self.assertEqual(action, "rejected")

    def test_recorded_answer_has_date_and_never_claims_live_verification(self):
        result, grounded, action = graph._validate_distilled_answer({
            "answer": "Memory uses v0.1.15 at 02216b4.",
            "confidence": "high", "sources": [LATEST], "escalate": False,
        }, current_packet())
        self.assertTrue(grounded)
        self.assertEqual(action, "downgraded")
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["escalate"])
        self.assertEqual(result["as_of"], "2026-10-09T17:14:40Z")
        self.assertTrue(result["answer"].startswith("Recorded as of 2026-10-09T17:14:40Z:"))
        self.assertTrue(result["answer"].endswith("Live state is not verified."))

    def test_unknown_newer_semantic_hit_blocks_old_current_claims(self):
        packet = evidence()
        packet["semantic_notes"][0].pop("content")
        context = graph._temporal_context(packet)
        self.assertEqual(context["eligible_sources"], [])
        self.assertEqual(context["as_of"], "2026-10-09T17:14:40Z")

    def test_missing_dates_and_superseded_only_facts_are_not_current_evidence(self):
        for facts in (
            [{"source_slug": "undated", "fact": "a fact", "current": True}],
            [{"source_slug": LATEST, "fact": "old fact", "current": False}],
        ):
            with self.subTest(facts=facts):
                context = graph._temporal_context({"facts": facts})
                self.assertEqual(context["eligible_sources"], [])
                self.assertIsNone(context["as_of"])

    def test_old_citation_after_the_source_limit_is_still_rejected(self):
        packet = evidence()
        latest_sources = [LATEST] + [LATEST + f"-{i}" for i in range(4)]
        packet["facts"].extend({
            "source_slug": slug, "current": True, "fact": "A current release fact.",
        } for slug in latest_sources)
        packet["temporal_context"] = graph._temporal_context(packet)
        result, grounded, action = graph._validate_distilled_answer({
            "answer": "Mixed current and old release state.", "confidence": "high",
            "sources": latest_sources + [OLD],
        }, packet)
        self.assertIsNone(result["answer"])
        self.assertFalse(grounded)
        self.assertEqual(action, "rejected")

    def test_distiller_view_excludes_stale_and_undated_fields(self):
        view = graph._current_evidence_packet(current_packet())
        text = json.dumps(view)
        self.assertNotIn("v0.1.6", text)
        self.assertNotIn("v0.1.11", text)
        self.assertEqual(view["facts"], [])
        self.assertEqual(view["entities"], [])
        self.assertEqual(view["communities"], [])
        self.assertIsNone(view["source_excerpt"])
        self.assertEqual([hit["name"] for hit in view["semantic_notes"]], [LATEST])

    def test_current_question_after_a_dated_release_remains_current(self):
        for question in (
            "What is deployed now after the 2026-10-08 release?",
            "What version now after the 2026-10-08 release?",
        ):
            with self.subTest(question=question):
                self.assertTrue(graph._current_state_question(question))

    def test_blank_newest_body_is_not_eligible(self):
        packet = evidence()
        packet["semantic_notes"][0]["content"] = " \n\t "
        context = graph._temporal_context(packet)
        self.assertEqual(context["eligible_sources"], [])
        self.assertEqual(context["as_of"], "2026-10-09T17:14:40Z")

    def test_historical_answer_retains_original_fields_and_confidence(self):
        parsed = {"answer": "Memory used v0.1.6 in July.", "confidence": "high", "sources": [OLD], "escalate": False}
        result, grounded, action = graph._validate_distilled_answer(parsed, evidence())
        self.assertEqual(result, parsed)
        self.assertTrue(grounded)
        self.assertEqual(action, "accepted")

    def test_long_answer_keeps_the_qualification_and_length_bound(self):
        result, _, _ = graph._validate_distilled_answer({
            "answer": "x" * 3000, "confidence": "low", "sources": [LATEST],
        }, current_packet())
        self.assertLessEqual(len(result["answer"]), 2000)
        self.assertTrue(result["answer"].endswith("Live state is not verified."))
        self.assertEqual(result["confidence"], "low")


class TemporalRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_hydrates_only_three_newest_dated_hits_with_bounded_bodies(self):
        hits = [
            {"name": name, "type": "source", "score": 0.8, "description": "Release"}
            for name in (OLD, NEW, LATEST, "2026-08-01-120000-release", "undated-page")
        ]
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=hits) as search, \
             mock.patch.object(wiki.idx, "get", return_value={"body": "b" * 10000}) as get:
            result = await graph._wiki_semantic_hits(QUESTION, 8, hydrate=True)
        search.assert_called_once_with(QUESTION, 8)
        self.assertEqual(get.call_count, 3)
        hydrated = [hit for hit in result if "content" in hit]
        self.assertEqual({hit["name"] for hit in hydrated}, {NEW, LATEST, "2026-08-01-120000-release"})
        self.assertTrue(all(len(hit["content"]) == 2500 for hit in hydrated))

    async def test_blank_bodies_are_not_hydrated_and_long_bodies_are_bounded(self):
        hits = [{"name": LATEST}, {"name": NEW}]
        bodies = {LATEST: "  \n ", NEW: "x" * 2500 + " release v0.1.99"}
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=hits), \
             mock.patch.object(wiki.idx, "get", side_effect=lambda name: {"body": bodies[name]}):
            result = await graph._wiki_semantic_hits(QUESTION, 8, hydrate=True)
        by_name = {hit["name"]: hit for hit in result}
        self.assertNotIn("content", by_name[LATEST])
        self.assertEqual(len(by_name[NEW]["content"]), 2500)
        self.assertNotIn("v0.1.99", by_name[NEW]["content"])

    async def test_ordinary_fusion_does_not_read_bodies(self):
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=[{"name": OLD}]), \
             mock.patch.object(wiki.idx, "get") as get:
            result = await graph._wiki_semantic_hits("historical question")
        get.assert_not_called()
        self.assertNotIn("content", result[0])

    async def test_current_answers_bypass_cache_and_reread_wiki_evidence(self):
        graph._ANSWER_CACHE.clear()
        newer = evidence()
        newer_slug = "2026-10-10-120000-new-release"
        newer["semantic_notes"] = [{"name": newer_slug, "content": "Memory uses v0.1.16."}]
        try:
            with mock.patch.object(graph, "_graph_ingest_watermark", new=mock.AsyncMock(return_value="unchanged")) as watermark, \
                 mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(side_effect=[evidence(), newer])) as assemble, \
                 mock.patch.object(graph, "_distill", new=mock.AsyncMock(side_effect=[
                     {"answer": "Memory uses v0.1.15.", "confidence": "high", "sources": [LATEST]},
                     {"answer": "Memory uses v0.1.16.", "confidence": "high", "sources": [newer_slug]},
                 ])) as distill, mock.patch.object(graph, "_record_query") as record:
                first = await graph._graph_answer_impl(QUESTION)
                second = await graph._graph_answer_impl(QUESTION)
            self.assertNotEqual(first, second)
            self.assertEqual(second["as_of"], "2026-10-10T12:00:00Z")
            watermark.assert_not_awaited()
            self.assertEqual(assemble.await_count, 2)
            self.assertEqual(distill.await_count, 2)
            self.assertTrue(all(call.kwargs["current_state"] for call in assemble.await_args_list))
            packets = [json.loads(call.args[1]) for call in distill.await_args_list]
            self.assertTrue(all("v0.1.6" not in json.dumps(p) for p in packets))
            self.assertEqual(packets[0]["temporal_context"]["eligible_sources"], [LATEST])
            self.assertEqual(packets[1]["temporal_context"]["eligible_sources"], [newer_slug])
            self.assertTrue(all(not call.args[0]["cached"] for call in record.call_args_list))
            self.assertEqual(graph._ANSWER_CACHE, {})
        finally:
            graph._ANSWER_CACHE.clear()

    async def test_no_supported_newest_evidence_abstains_without_model_call(self):
        packet = evidence()
        packet["semantic_notes"][0].pop("content")
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=packet)), \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock()) as distill, \
             mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl(QUESTION)
        distill.assert_not_awaited()
        self.assertIsNone(result["answer"])
        self.assertTrue(result["escalate"])


if __name__ == "__main__":
    unittest.main()

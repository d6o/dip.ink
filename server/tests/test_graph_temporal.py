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
PREVIOUS = "2026-10-09-165822-dipink-v014-image-manifests"
LATEST = "2026-10-09-171440-frontmatter-depth"
RUNNER = "2026-10-09-172000-runner-release"
GAPS = "2026-10-10-060032-memory-gaps-weekly"
QUESTION = "What are the latest dip.ink release tags and deployed revisions for the memory server and pi-runner?"
LATEST_TEXT = "Memory uses v0.1.15 at 02216b4. pi-runner:v0.1.15 is published."


def evidence() -> dict:
    return {
        "query": QUESTION,
        "facts": [
            {"fact": "Memory uses v0.1.6.", "source_slug": OLD, "current": True},
            {"fact": "The release publishes pi-runner:v0.1.11.", "source_slug": NEW, "current": True},
        ],
        "entities": [{"summary": "Memory uses v0.1.6."}],
        "communities": [{"summary": "The curator uses v0.1.7."}],
        "source_excerpt": {"slug": OLD, "content": "Memory uses v0.1.6."},
        "semantic_notes": [
            {"name": LATEST, "type": "source", "description": "Patch release", "content": LATEST_TEXT},
        ],
    }


def current_packet(packet: dict | None = None) -> dict:
    packet = packet or evidence()
    packet["temporal_context"] = graph._temporal_context(packet)
    return packet


def answer(text: str, sources: list[str], confidence: str = "high") -> dict:
    return {"answer": text, "confidence": confidence, "sources": sources, "escalate": False}


class TemporalIntentTests(unittest.TestCase):
    def test_current_state_questions(self):
        for question in (
            QUESTION, "What is the current deployment?", "Which version is the server running?",
            "Qual a versão atual?", "What is the most recent release?",
            "What runs now and what was used before?", "What version does memory use?",
            "Which image does mykg pin?", "What is the memory server version?",
            "What version is in production?", "Which revision is live?",
            "Which service is running on 8080 now?",
            "What is deployed now after the 2026-10-08 release?",
            "What version now after the 2026-10-08 release?",
        ):
            with self.subTest(question=question):
                self.assertTrue(graph._current_state_question(question))

    def test_historical_and_ordinary_questions_remain_ordinary(self):
        for question in (
            "What was the latest release on 2026-07-23?",
            "What is the current deployment as of 2026-07-23?",
            "What is the current version as of 2026-07-01?", "What is the deployment as of July 1?",
            "What was the current deployment?", "Which release added metadata backfill?",
            "What version did memory use?", "What does the latest Docker tag mean?",
            "What is the answer?", "What is the latest-safe cache algorithm?",
            "Do we publish a latest tag?", "Which commit introduced the use of fastembed?",
            "Why does ingest still fail?", "What are the newest notes about Traefik?",
        ):
            with self.subTest(question=question):
                self.assertFalse(graph._current_state_question(question))

    def test_invalid_source_dates_are_not_evidence(self):
        self.assertIsNone(graph._source_time("not-a-dated-source"))
        self.assertIsNone(graph._source_time("2026-99-99-999999-invalid"))


class TemporalGroundingTests(unittest.TestCase):
    def test_recent_supported_sources_inside_the_window_are_eligible(self):
        context = current_packet()["temporal_context"]
        self.assertEqual(context["eligible_sources"], [LATEST])
        self.assertEqual(context["newest_evidence_at"], "2026-10-09T17:14:40Z")
        self.assertFalse(context["live_verified"])

    def test_old_or_mixed_current_claim_is_rejected(self):
        for sources in ([OLD], [NEW], [OLD, NEW], [OLD, LATEST]):
            with self.subTest(sources=sources):
                result, grounded, action = graph._validate_distilled_answer(
                    answer("The latest memory deployment is v0.1.6.", sources), current_packet())
                self.assertIsNone(result["answer"])
                self.assertEqual(result["confidence"], "not_found")
                self.assertTrue(result["escalate"])
                self.assertFalse(grounded)
                self.assertEqual(action, "rejected")

    def test_new_citation_with_old_value_is_rejected(self):
        packet = evidence()
        packet["semantic_notes"][0]["content"] = "Unrelated note about frontmatter depth."
        result, grounded, action = graph._validate_distilled_answer(
            answer("Memory uses v0.1.6", [LATEST]), current_packet(packet))
        self.assertIsNone(result["answer"])
        self.assertFalse(grounded)
        self.assertEqual(action, "rejected")

    def test_commit_and_image_values_must_occur_in_cited_text(self):
        for text in ("Memory uses v0.1.15 at 1234567.", "pi-runner:v0.1.16 is published."):
            with self.subTest(text=text):
                result, _, action = graph._validate_distilled_answer(
                    answer(text, [LATEST]), current_packet())
                self.assertEqual(action, "rejected")
                self.assertIsNone(result["answer"])

    def test_recorded_answer_has_date_and_never_claims_live_verification(self):
        result, grounded, action = graph._validate_distilled_answer(
            answer("Memory uses v0.1.15 at 02216b4", [LATEST]), current_packet())
        self.assertTrue(grounded)
        self.assertEqual(action, "downgraded")
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["escalate"])
        self.assertEqual(result["as_of"], "2026-10-09T17:14:40Z")
        self.assertEqual(
            result["answer"],
            "Recorded as of 2026-10-09T17:14:40Z: Memory uses v0.1.15 at 02216b4. Live state is not verified.",
        )

    def test_subjects_recorded_minutes_apart_can_answer_together(self):
        packet = evidence()
        packet["semantic_notes"] = [
            {"name": LATEST, "content": "Memory uses v0.1.15."},
            {"name": RUNNER, "content": "pi-runner:v0.1.15 is published."},
        ]
        packet = current_packet(packet)
        self.assertEqual(packet["temporal_context"]["eligible_sources"], [LATEST, RUNNER])
        for text, sources, as_of in (
            ("Memory uses v0.1.15; pi-runner:v0.1.15 is published.", [LATEST, RUNNER], "2026-10-09T17:20:00Z"),
            ("Memory uses v0.1.15.", [LATEST], "2026-10-09T17:14:40Z"),
        ):
            with self.subTest(sources=sources):
                result, grounded, _ = graph._validate_distilled_answer(answer(text, sources), packet)
                self.assertTrue(grounded)
                self.assertEqual(result["as_of"], as_of)

    def test_newer_irrelevant_hit_does_not_block_relevant_recent_fact(self):
        packet = evidence()
        packet["facts"].append({"fact": "Memory uses v0.1.15.", "source_slug": LATEST, "current": True})
        packet["semantic_notes"] = [{"name": GAPS, "relevant": False, "content": "Weekly gaps report."}]
        packet = current_packet(packet)
        self.assertEqual(packet["temporal_context"]["eligible_sources"], [LATEST])
        result, grounded, _ = graph._validate_distilled_answer(answer("Memory uses v0.1.15.", [LATEST]), packet)
        self.assertTrue(grounded)
        self.assertIn("v0.1.15", result["answer"])

    def test_sources_outside_the_window_are_not_eligible(self):
        packet = evidence()
        packet["semantic_notes"].append({"name": "2026-10-08-120000-older-release", "content": "Memory uses v0.1.12."})
        context = graph._temporal_context(packet)
        self.assertEqual(context["eligible_sources"], [LATEST])

    def test_unknown_newer_relevant_hit_blocks_old_current_claims(self):
        packet = evidence()
        packet["semantic_notes"][0].pop("content")
        context = graph._temporal_context(packet)
        self.assertEqual(context["eligible_sources"], [])
        self.assertEqual(context["newest_evidence_at"], "2026-10-09T17:14:40Z")

    def test_blank_newest_body_is_not_eligible(self):
        packet = evidence()
        packet["semantic_notes"][0]["content"] = " \n\t "
        self.assertEqual(graph._temporal_context(packet)["eligible_sources"], [])

    def test_missing_dates_and_superseded_only_facts_are_not_current_evidence(self):
        for facts in (
            [{"source_slug": "undated", "fact": "a fact", "current": True}],
            [{"source_slug": LATEST, "fact": "old fact", "current": False}],
        ):
            with self.subTest(facts=facts):
                context = graph._temporal_context({"facts": facts})
                self.assertEqual(context["eligible_sources"], [])
                self.assertIsNone(context["newest_evidence_at"])

    def test_old_citation_after_the_source_limit_is_still_rejected(self):
        packet = evidence()
        latest_sources = [LATEST] + [LATEST + f"-{i}" for i in range(4)]
        packet["facts"].extend({
            "source_slug": slug, "current": True, "fact": "Memory uses v0.1.15.",
        } for slug in latest_sources)
        result, grounded, action = graph._validate_distilled_answer(
            answer("Memory uses v0.1.15.", latest_sources + [OLD]), current_packet(packet))
        self.assertIsNone(result["answer"])
        self.assertFalse(grounded)
        self.assertEqual(action, "rejected")

    def test_distiller_view_excludes_stale_and_undated_fields(self):
        view = graph._current_evidence_packet(current_packet())
        text = json.dumps(view)
        for stale in ("v0.1.6", "v0.1.7", "v0.1.11"):
            self.assertNotIn(stale, text)
        self.assertEqual(view["facts"], [])
        self.assertEqual(view["entities"], [])
        self.assertEqual(view["communities"], [])
        self.assertIsNone(view["source_excerpt"])
        self.assertEqual([hit["name"] for hit in view["semantic_notes"]], [LATEST])

    def test_historical_answer_retains_original_fields_and_confidence(self):
        parsed = answer("Memory used v0.1.6 in July.", [OLD])
        result, grounded, action = graph._validate_distilled_answer(parsed, evidence())
        self.assertEqual(result, parsed)
        self.assertTrue(grounded)
        self.assertEqual(action, "accepted")

    def test_long_answer_keeps_the_qualification_and_length_bound(self):
        result, _, _ = graph._validate_distilled_answer(answer("x" * 3000, [LATEST], "low"), current_packet())
        self.assertLessEqual(len(result["answer"]), 2000)
        self.assertTrue(result["answer"].endswith(". Live state is not verified."))
        self.assertEqual(result["confidence"], "low")


class CurrentBodyExcerptTests(unittest.TestCase):
    def test_late_release_lines_survive_the_bound(self):
        body = (
            "# Note\n\n## Durable claims\n\n- Memory uses v0.1.15 at revision 02216b4.\n\n## Context\n\n"
            + "context text. " * 250
            + "\n- `ghcr.io/d6o/dip.ink/pi-runner:v0.1.15`: `sha256:" + "e9" * 32 + "`.\n"
        )
        self.assertGreater(body.index("pi-runner:v0.1.15"), graph.CURRENT_BODY_CHARS)
        excerpt = graph._current_body_excerpt(body)
        self.assertLessEqual(len(excerpt), graph.CURRENT_BODY_CHARS)
        self.assertIn("Memory uses v0.1.15", excerpt)
        self.assertIn("pi-runner:v0.1.15", excerpt)
        self.assertNotIn("context text. context text.", excerpt)

    def test_short_body_is_unchanged(self):
        self.assertEqual(graph._current_body_excerpt("  short body \n"), "short body")


class TemporalRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_relevant_hits_hydrate_the_three_newest_with_index_bodies(self):
        hits = [
            {"name": name, "type": "source", "score": score, "description": "Release"}
            for name, score in (
                (OLD, 0.67), (NEW, 0.66), (PREVIOUS, 0.63), ("2026-08-01-120000-release", 0.62),
                (LATEST, 0.59), ("undated-page", 0.58), (GAPS, 0.40),
            )
        ]
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=hits) as search, \
             mock.patch.object(wiki.idx, "get", return_value={"body": "b" * 10000}) as get:
            result = await graph._wiki_semantic_hits(QUESTION, 25, hydrate=True)
        search.assert_called_once_with(QUESTION, 25)
        self.assertNotIn(GAPS, [hit["name"] for hit in result])
        self.assertEqual(get.call_count, 3)
        hydrated = {hit["name"] for hit in result if "content" in hit}
        self.assertEqual(hydrated, {LATEST, PREVIOUS, NEW})
        self.assertTrue(all(len(hit["content"]) <= graph.CURRENT_BODY_CHARS for hit in result if "content" in hit))

    async def test_newest_relevant_hit_after_rank_eight_is_hydrated(self):
        hits = [
            {"name": f"2026-09-0{i}-120000-older-release", "score": 0.65 - i / 1000}
            for i in range(1, 9)
        ] + [{"name": LATEST, "score": 0.563}]
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=hits), \
             mock.patch.object(wiki.idx, "get", return_value={"body": LATEST_TEXT}):
            result = await graph._wiki_semantic_hits(QUESTION, 25, hydrate=True)
        by_name = {hit["name"]: hit for hit in result}
        self.assertEqual(by_name[LATEST]["content"], LATEST_TEXT)

    async def test_blank_bodies_are_not_hydrated(self):
        hits = [{"name": LATEST, "score": 0.6}, {"name": NEW, "score": 0.6}]
        bodies = {LATEST: "  \n ", NEW: "Memory uses v0.1.11."}
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=hits), \
             mock.patch.object(wiki.idx, "get", side_effect=lambda name: {"body": bodies[name]}):
            result = await graph._wiki_semantic_hits(QUESTION, 25, hydrate=True)
        by_name = {hit["name"]: hit for hit in result}
        self.assertNotIn("content", by_name[LATEST])
        self.assertEqual(by_name[NEW]["content"], "Memory uses v0.1.11.")

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
                     answer("Memory uses v0.1.15.", [LATEST]),
                     answer("Memory uses v0.1.16.", [newer_slug]),
                 ])) as distill, mock.patch.object(graph, "_record_query") as record:
                first = await graph._graph_answer_impl(QUESTION)
                second = await graph._graph_answer_impl(QUESTION)
            self.assertIn("v0.1.15", first["answer"])
            self.assertEqual(second["as_of"], "2026-10-10T12:00:00Z")
            watermark.assert_not_awaited()
            self.assertEqual(assemble.await_count, 2)
            self.assertTrue(all(call.kwargs["current_state"] for call in assemble.await_args_list))
            packets = [json.loads(call.args[1]) for call in distill.await_args_list]
            self.assertTrue(all("v0.1.6" not in json.dumps(p) for p in packets))
            events = [call.args[0] for call in record.call_args_list]
            self.assertTrue(all(not event["cached"] for event in events))
            self.assertTrue(all(event["temporal_mode"] == "current" for event in events))
            self.assertEqual(graph._ANSWER_CACHE, {})
        finally:
            graph._ANSWER_CACHE.clear()

    async def test_no_supported_recent_evidence_abstains_without_model_call(self):
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

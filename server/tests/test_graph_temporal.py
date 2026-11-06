"""Current policy keeps readable evidence; capture dates are not state authority."""
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


def evidence():
    return {"query": QUESTION,
            "facts": [{"fact": "Memory uses v0.1.6.", "source_slug": OLD, "current": True},
                      {"fact": "The release publishes pi-runner:v0.1.11.", "source_slug": NEW, "current": True}],
            "entities": [{"summary": "Memory uses v0.1.6. The curator uses v0.1.7."}],
            "source_excerpt": {"slug": OLD, "content": "Memory uses v0.1.6."},
            "semantic_notes": [{"name": LATEST, "type": "source", "description": "Patch release", "content": LATEST_TEXT}]}


def current_packet(packet=None):
    packet = packet or evidence()
    packet["temporal_context"] = graph._temporal_context(packet)
    return packet


def answer(text, sources, confidence="high"):
    return {"answer": text, "confidence": confidence, "sources": sources, "escalate": False}


class TemporalIntentTests(unittest.TestCase):
    def test_current_state_questions(self):
        for question in (QUESTION, "What is the current deployment?", "Which version is the server running?",
                         "Qual a versão atual?", "What is the most recent release?", "What runs now and what was used before?",
                         "What version does memory use?", "Which image does mykg pin?", "What is the memory server version?",
                         "What version is in production?", "Which revision is live?", "Which service is running on 8080 now?",
                         "What is deployed now after the 2026-10-08 release?", "What version now after the 2026-10-08 release?",
                         "What image tags does the CI workflow use?", "Which backend does the service use?",
                         "What database does the application use?", "Where is the service configuration stored?"):
            with self.subTest(question=question):
                self.assertTrue(graph._current_state_question(question))

    def test_historical_and_ordinary_questions_remain_ordinary(self):
        for question in ("What was the latest release on 2026-07-23?", "What is the current deployment as of 2026-07-23?",
                         "What is the current version as of 2026-07-01?", "What is the deployment as of July 1?",
                         "What was the current deployment?", "Which release added metadata backfill?",
                         "What version did memory use?", "What does the latest Docker tag mean?", "What is the answer?",
                         "What is the latest-safe cache algorithm?", "Do we publish a latest tag?",
                         "Which commit introduced the use of fastembed?", "Why does ingest still fail?",
                         "What are the newest notes about Traefik?", "Where is the original configuration stored?",
                         "Which models worked in past projects?"):
            with self.subTest(question=question):
                self.assertFalse(graph._current_state_question(question))

    def test_invalid_source_dates_are_not_evidence(self):
        self.assertIsNone(graph._source_time("not-a-dated-source"))
        self.assertIsNone(graph._source_time("2026-99-99-999999-invalid"))


class TemporalGroundingTests(unittest.TestCase):
    def test_all_readable_sources_are_eligible_without_capture_age_expiry(self):
        context = current_packet()["temporal_context"]
        self.assertEqual(context["eligible_sources"], sorted([OLD, NEW, LATEST]))
        self.assertEqual(context["newest_evidence_at"], "2026-10-09T17:14:40Z")
        self.assertIsNone(context["window_seconds"])
        self.assertFalse(context["live_verified"])

    def test_source_age_alone_cannot_reject_a_supported_value(self):
        packet = evidence()
        packet["semantic_notes"][0]["content"] = "A separate component publishes v0.1.15; no Memory rollout occurred."
        result, grounded, action = graph._validate_distilled_answer(answer("Memory uses v0.1.6.", [OLD]), current_packet(packet))
        self.assertTrue(grounded)
        self.assertEqual(action, "downgraded")
        self.assertIn("v0.1.6", result["answer"])

    def test_citation_with_absent_value_still_rejects(self):
        packet = evidence(); packet["semantic_notes"][0]["content"] = "Unrelated note about frontmatter depth."
        result, grounded, action = graph._validate_distilled_answer(answer("Memory uses v0.1.6", [LATEST]), current_packet(packet))
        self.assertIsNone(result["answer"])
        self.assertFalse(grounded)
        self.assertEqual(action, "rejected")

    def test_commit_and_image_values_must_occur_in_cited_text(self):
        for text in ("Memory uses v0.1.15 at 1234567.", "pi-runner:v0.1.16 is published."):
            with self.subTest(text=text):
                result, _, action = graph._validate_distilled_answer(answer(text, [LATEST]), current_packet())
                self.assertEqual(action, "rejected")
                self.assertIsNone(result["answer"])

    def test_numeric_version_alone_cannot_overrule_other_scoped_state(self):
        packet = evidence()
        packet["semantic_notes"][0]["content"] = "A separate component publishes v0.1.15; no Memory rollout occurred."
        packet["semantic_notes"].append({"name": PREVIOUS, "content": "Memory uses v0.1.14 at bf386f8."})
        result, grounded, _ = graph._validate_distilled_answer(answer("Memory v0.1.14 at bf386f8", [PREVIOUS]), current_packet(packet))
        self.assertTrue(grounded)
        self.assertIn("v0.1.14", result["answer"])

    def test_quoted_obsolete_values_remain_reader_context_not_numeric_authority(self):
        packet = evidence()
        packet["semantic_notes"] = [{"name": PREVIOUS, "content": "pi-runner:v0.1.14 is published. Caution: an old answer said memory v0.1.6."}]
        view = graph._current_evidence_packet(current_packet(packet))
        self.assertIn("Caution", json.dumps(view))
        self.assertIn("obsolete, incorrect, or previous values as examples", graph._DISTILL_SYSTEM)
        # This test does not certify that a model follows the instruction.

    def test_value_check_matches_whole_tokens(self):
        for text in ("Memory v0.1.1", "Memory uses v0.1.15 at 02216b.", "Memory at 2216b4c"):
            result, _, action = graph._validate_distilled_answer(answer(text, [LATEST]), current_packet())
            if "02216b." in text:
                self.assertEqual(action, "downgraded")
            else:
                self.assertEqual(action, "rejected")
                self.assertIsNone(result["answer"])

    def test_short_commit_matches_full_commit_not_an_arbitrary_name_prefix(self):
        packet = evidence()
        packet["semantic_notes"][0]["content"] = "Memory uses v0.1.15 at revision 02216b4ce731246b4b6966d5db974fa9b3af6ea1."
        result, grounded, _ = graph._validate_distilled_answer(answer("Memory uses v0.1.15 at 02216b4.", [LATEST]), current_packet(packet), QUESTION)
        self.assertTrue(grounded)
        self.assertIn("02216b4", result["answer"])
        self.assertFalse(graph._value_in_tokens("02216b4", {"02216b4-model-name"}))

    def test_recorded_answer_has_provenance_date_not_live_verification(self):
        result, grounded, action = graph._validate_distilled_answer(answer("Memory uses v0.1.15 at 02216b4", [LATEST]), current_packet())
        self.assertTrue(grounded)
        self.assertEqual(action, "downgraded")
        self.assertEqual(result["as_of"], "2026-10-09T17:14:40Z")
        self.assertEqual(result["confidence"], "medium")
        self.assertTrue(result["escalate"])
        self.assertIn("Live state is not verified", result["answer"])

    def test_sources_recorded_apart_can_answer_together(self):
        packet = evidence()
        packet["semantic_notes"] = [{"name": LATEST, "content": "Memory uses v0.1.15."},
                                    {"name": RUNNER, "content": "pi-runner:v0.1.15 is published."}]
        result, grounded, _ = graph._validate_distilled_answer(answer("Memory uses v0.1.15; pi-runner:v0.1.15 is published.", [LATEST, RUNNER]), current_packet(packet))
        self.assertTrue(grounded)
        self.assertEqual(result["as_of"], "2026-10-09T17:20:00Z")

    def test_irrelevant_and_unreadable_hits_do_not_grant_authority_or_expire_facts(self):
        for hit in ({"name": GAPS, "relevant": False, "content": "Weekly gaps report."},
                    {"name": GAPS}, {"name": GAPS, "content": " \n\t "}):
            packet = evidence(); packet["semantic_notes"] = [hit]
            self.assertEqual(graph._temporal_context(packet)["eligible_sources"], sorted([OLD, NEW]))

    def test_undated_readable_state_is_usable_without_inventing_an_as_of_date(self):
        packet = current_packet({"facts": [{"source_slug": "config-page", "fact": "Service uses a queue.", "current": True}]})
        result, grounded, _ = graph._validate_distilled_answer(answer("Service uses a queue.", ["config-page"]), packet, "Which queue does the service use?")
        self.assertTrue(grounded)
        self.assertNotIn("as_of", result)
        self.assertIn("Recorded evidence:", result["answer"])
        self.assertEqual(graph._temporal_context({"facts": [{"source_slug": LATEST, "fact": "old fact", "current": False}]})["eligible_sources"], [])

    def test_invented_citation_after_the_source_limit_still_rejects(self):
        packet = evidence()
        latest_sources = [LATEST] + [LATEST + f"-{i}" for i in range(4)]
        packet["facts"].extend({"source_slug": slug, "current": True, "fact": "Memory uses v0.1.15."} for slug in latest_sources)
        result, grounded, action = graph._validate_distilled_answer(answer("Memory uses v0.1.15.", latest_sources + ["invented"]), current_packet(packet))
        self.assertIsNone(result["answer"])
        self.assertFalse(grounded)
        self.assertEqual(action, "rejected")

    def test_distiller_keeps_old_readable_context_but_not_unattributed_summaries(self):
        view = graph._current_evidence_packet(current_packet())
        self.assertIn("v0.1.6", json.dumps(view))
        self.assertIn("v0.1.11", json.dumps(view))
        self.assertNotIn("v0.1.7", json.dumps(view))
        self.assertEqual(view["entities"], [])

    def test_historical_answer_keeps_original_fields(self):
        parsed = answer("Memory used v0.1.6 in July.", [OLD])
        result, grounded, action = graph._validate_distilled_answer(parsed, evidence())
        self.assertEqual(result, parsed)
        self.assertTrue(grounded)
        self.assertEqual(action, "accepted")

    def test_long_answer_keeps_qualification_and_length_bound(self):
        result, _, _ = graph._validate_distilled_answer(answer("x" * 3000, [LATEST], "low"), current_packet())
        self.assertLessEqual(len(result["answer"]), 2000)
        self.assertTrue(result["answer"].endswith(". Live state is not verified."))


class CurrentBodyExcerptTests(unittest.TestCase):
    def test_late_release_lines_survive_the_bound(self):
        body = "# Note\n\n## Durable claims\n\n- Memory uses v0.1.15 at revision 02216b4.\n\n## Context\n\n" + "context text. " * 250 + "\n- `ghcr.io/d6o/dip.ink/pi-runner:v0.1.15`: `sha256:" + "e9" * 32 + "`.\n"
        excerpt = graph._current_body_excerpt(body)
        self.assertLessEqual(len(excerpt), graph.CURRENT_BODY_CHARS)
        self.assertIn("Memory uses v0.1.15", excerpt)
        self.assertIn("pi-runner:v0.1.15", excerpt)
        self.assertNotIn("context text. context text.", excerpt)

    def test_short_body_is_unchanged(self):
        self.assertEqual(graph._current_body_excerpt("  short body \n"), "short body")


class TemporalRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_semantic_hydration_keeps_existing_retrieval_order_policy(self):
        hits = [{"name": name, "score": score, "description": "Release"} for name, score in
                ((OLD, .67), (NEW, .66), (PREVIOUS, .63), (LATEST, .59), ("undated-page", .58), (GAPS, .40))]
        with mock.patch.object(graph, "FUSION", True), mock.patch.object(wiki.idx, "search", return_value=hits), \
             mock.patch.object(wiki.idx, "get", return_value={"body": "b" * 10000}) as get:
            out = await graph._wiki_semantic_hits(QUESTION, 25, hydrate=True)
        self.assertEqual(get.call_count, 3)
        self.assertEqual({h["name"] for h in out if "content" in h}, {LATEST, PREVIOUS, NEW})
        self.assertNotIn(GAPS, [h["name"] for h in out])
        self.assertTrue(all(len(h.get("content", "")) <= graph.CURRENT_BODY_CHARS for h in out))

    async def test_newest_relevant_hit_after_rank_eight_is_hydrated(self):
        hits = [{"name": f"2026-09-0{i}-120000-older-release", "score": .65-i/1000} for i in range(1, 9)] + [{"name": LATEST, "score": .563}]
        with mock.patch.object(graph, "FUSION", True), mock.patch.object(wiki.idx, "search", return_value=hits), \
             mock.patch.object(wiki.idx, "get", return_value={"body": LATEST_TEXT}):
            out = await graph._wiki_semantic_hits(QUESTION, 25, hydrate=True)
        self.assertEqual({h["name"]: h for h in out}[LATEST]["content"], LATEST_TEXT)

    async def test_blank_bodies_are_not_hydrated(self):
        with mock.patch.object(graph, "FUSION", True), mock.patch.object(wiki.idx, "search", return_value=[{"name": LATEST, "score": .6}]), \
             mock.patch.object(wiki.idx, "get", return_value={"body": " \n"}):
            out = await graph._wiki_semantic_hits(QUESTION, 25, hydrate=True)
        self.assertNotIn("content", out[0])

    async def test_ordinary_fusion_does_not_read_bodies(self):
        with mock.patch.object(graph, "FUSION", True), mock.patch.object(wiki.idx, "search", return_value=[{"name": OLD}]), mock.patch.object(wiki.idx, "get") as get:
            out = await graph._wiki_semantic_hits("historical question")
        get.assert_not_called()
        self.assertNotIn("content", out[0])

    async def test_current_answers_reread_without_age_pruning(self):
        newer = evidence(); slug = "2026-10-10-120000-new-release"
        newer["semantic_notes"] = [{"name": slug, "content": "Memory uses v0.1.16."}]
        with mock.patch.object(graph, "_assemble_packet", mock.AsyncMock(side_effect=[evidence(), newer])) as assemble, \
             mock.patch.object(graph, "_distill", mock.AsyncMock(side_effect=[answer("Memory uses v0.1.15.", [LATEST]), answer("Memory uses v0.1.16.", [slug])])) as distill, \
             mock.patch.object(graph, "_record_query"):
            first = await graph._graph_answer_impl(QUESTION)
            second = await graph._graph_answer_impl(QUESTION)
        self.assertIn("v0.1.15", first["answer"])
        self.assertEqual(second["as_of"], "2026-10-10T12:00:00Z")
        self.assertEqual(assemble.await_count, 2)
        self.assertTrue(all("v0.1.6" in call.args[1] for call in distill.await_args_list))

    async def test_no_readable_evidence_abstains_without_model_call(self):
        packet = {"facts": [], "semantic_notes": [{"name": LATEST}], "source_excerpt": None}
        with mock.patch.object(graph, "_assemble_packet", mock.AsyncMock(return_value=packet)), \
             mock.patch.object(graph, "_distill", mock.AsyncMock()) as distill, mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl(QUESTION)
        distill.assert_not_awaited()
        self.assertIsNone(result["answer"])

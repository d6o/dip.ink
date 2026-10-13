"""dip.ink#10: TokenBook must not inherit TokenBridge infrastructure facts."""
import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")

import graph
import wiki

QUESTION = "What are the TokenBook self-hosted inference deployment, model, request limits, and protected credential location?"
BRIDGE = "2026-07-02-015607-tokenbridge-pi-prompt-oauth-scrub"
CREDENTIAL = "2026-06-12-143950-corridabrasil-alerta-city-radius"
NOTE = "2026-10-13-024536-tokenbook-self-hosted-inference"
# Synthetic fixture: no real credential location or value.
BODY = ("TokenBook uses its own llama.cpp deployment and Qwen2.5-0.5B-Instruct Q8_0. "
        "TokenBook request limits and credential metadata are in its committed deployment contract.")


def packet():
    return {
        "query": QUESTION,
        "facts": [
            {"source_slug": BRIDGE, "current": True,
             "fact": "TokenBridge uses claude-max at tokenbridge.tokenbridge.svc.cluster.local:64963."},
            {"source_slug": CREDENTIAL, "current": True,
             "fact": "CorridaBrasil uses the corridabrasil-listmonk Secret."},
        ],
        "entities": [{"name": "TokenBridge", "summary": "TokenBridge is a model gateway."}],
        "source_excerpt": {"slug": BRIDGE, "content": "TokenBridge uses claude-max."},
        "semantic_notes": [{"name": NOTE, "description": "TokenBook self-hosted inference", "score": .694}],
    }


def fabricated(confidence="high"):
    return {"answer": "TokenBook uses TokenBridge at port 64963, claude-max, and corridabrasil-listmonk.",
            "sources": [BRIDGE, CREDENTIAL], "confidence": confidence, "escalate": False}


class SubjectGuardTests(unittest.TestCase):
    def test_conservative_product_names(self):
        self.assertEqual(graph._answer_subjects(QUESTION), ["tokenbook"])
        self.assertEqual(graph._answer_subjects("Compare HookFork and TokenBook."), ["hookfork", "tokenbook"])
        self.assertEqual(graph._answer_subjects("What is the CPU model and HTTP port?"), [])
        self.assertEqual(graph._answer_subjects("What is the external-dns target annotation?"), [])

    def test_exact_tokenbook_question_rejects_wrong_subject_at_all_confidences(self):
        for confidence in ("high", "medium", "low"):
            with self.subTest(confidence=confidence):
                result, _, action = graph._validate_distilled_answer(fabricated(confidence), packet(), QUESTION)
                self.assertIsNone(result["answer"])
                self.assertEqual(result["confidence"], "not_found")
                self.assertTrue(result["escalate"])
                self.assertEqual(action, "subject_mismatch")

    def test_title_or_description_cannot_supply_subject_support(self):
        p = packet()
        p["semantic_notes"][0]["content"] = "TokenBridge uses claude-max."
        self.assertTrue(graph._subject_support_missing(
            graph._subject_evidence_packet(p, ["tokenbook"]), ["tokenbook"]))

    def test_exact_word_match_not_similar_names(self):
        for text in ("TokenBridge", "TokenBookish", "MyTokenBook", "TokenBook_private"):
            self.assertFalse(graph._mentions_subject(text, "tokenbook"))
        for text in ("TokenBook's", "tokenbook.voa.sh", "TokenBook-inference"):
            self.assertTrue(graph._mentions_subject(text, "tokenbook"))

    def test_each_fact_is_filtered_even_in_a_shared_source(self):
        p = packet()
        p["facts"].append({"source_slug": BRIDGE, "current": True, "fact": "TokenBook has a separate endpoint."})
        scoped = graph._subject_evidence_packet(p, ["tokenbook"])
        self.assertEqual(len(scoped["facts"]), 1)
        self.assertNotIn("claude-max", str(scoped))
        self.assertEqual(scoped["entities"], [])
        self.assertIsNone(scoped["source_excerpt"])

    def test_correct_note_is_accepted_but_does_not_authorize_an_unrelated_citation(self):
        p = packet()
        p["semantic_notes"][0]["content"] = BODY
        parsed = {"answer": "TokenBook uses its own llama.cpp deployment.",
                  "sources": [NOTE], "confidence": "high", "escalate": False}
        result, _, _ = graph._validate_distilled_answer(parsed, p, "What is the TokenBook inference deployment?")
        self.assertEqual(result["answer"], parsed["answer"])
        parsed["sources"].append(CREDENTIAL)
        result, _, action = graph._validate_distilled_answer(parsed, p, "What is the TokenBook inference deployment?")
        self.assertIsNone(result["answer"])
        self.assertEqual(action, "subject_mismatch")

    def test_multi_subject_question_requires_both(self):
        p = packet()
        p["semantic_notes"][0]["content"] = BODY
        scoped = graph._subject_evidence_packet(p, ["tokenbook", "hookfork"])
        self.assertTrue(graph._subject_support_missing(scoped, ["tokenbook", "hookfork"]))

    def test_timeless_question_preserves_existing_behavior(self):
        parsed = fabricated()
        result, _, _ = graph._validate_distilled_answer(parsed, packet(), "What model does the gateway use?")
        self.assertEqual(result["answer"], parsed["answer"])


class SemanticHydrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_answer_hydrates_bounded_index_bodies(self):
        hits = [{"name": f"TokenBook-page-{i}", "score": .7-i*.01, "description": "TokenBook"} for i in range(8)]
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=hits), \
             mock.patch.object(wiki.idx, "get", return_value={"body": BODY * 1000}) as get:
            out = await graph._wiki_semantic_hits(QUESTION, 8, answer_evidence=True)
        self.assertEqual(get.call_count, 3)
        bodies = [hit["content"] for hit in out if hit.get("content")]
        self.assertEqual(len(bodies), 3)
        self.assertTrue(all(len(body) <= graph.CURRENT_BODY_CHARS for body in bodies))

    async def test_subject_matches_take_priority_over_similar_services(self):
        hits = [{"name": f"TokenBridge-{i}", "score": .70-i*.005} for i in range(5)]
        hits.append({"name": NOTE, "score": .65})
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=hits), \
             mock.patch.object(wiki.idx, "get", return_value={"body": BODY}) as get:
            out = await graph._wiki_semantic_hits(QUESTION, 8, answer_evidence=True)
        self.assertEqual(get.call_args_list[0].args[0], NOTE)
        self.assertTrue(next(h for h in out if h["name"] == NOTE).get("content"))

    async def test_graph_search_keeps_compact_shape(self):
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=[{"name": NOTE, "score": .7}]), \
             mock.patch.object(wiki.idx, "get") as get:
            out = await graph._wiki_semantic_hits(QUESTION)
        get.assert_not_called()
        self.assertNotIn("content", out[0])

    async def test_actual_answer_path_abstains_before_distillation_if_subject_is_absent(self):
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=packet())) as assemble, \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock(return_value=fabricated())) as distill, \
             mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl(QUESTION)
        self.assertEqual(result["confidence"], "not_found")
        self.assertTrue(result["escalate"])
        self.assertTrue(assemble.call_args.kwargs["answer_evidence"])
        distill.assert_not_awaited()

    async def test_distiller_receives_readable_note_not_unrelated_graph_facts(self):
        p = packet()
        p["semantic_notes"][0]["content"] = BODY
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=p)), \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock(return_value={
                 "answer": "TokenBook uses its own llama.cpp deployment.", "confidence": "high", "sources": [NOTE]
             })) as distill, mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl("What is the TokenBook inference deployment?")
        text = distill.call_args.args[1]
        self.assertIn("Qwen2.5", text)
        self.assertNotIn("TokenBridge", text)
        self.assertNotIn("corridabrasil", text)
        self.assertEqual(result["sources"], [NOTE])


class SubjectTemporalVetoTests(unittest.IsolatedAsyncioTestCase):
    OLD = "2026-10-01-120000-tokenbook-deployment"
    NEW = "2026-10-13-120000-tokenbook-update"
    QUERY = "What is the current TokenBook deployment version?"

    def evidence(self, candidate=None):
        return {
            "facts": [{"source_slug": self.OLD, "current": True, "fact": "TokenBook uses v1.0.0."}],
            "entities": [], "source_excerpt": None,
            "semantic_notes": [candidate if candidate is not None else {"name": self.NEW, "relevant": True}],
        }

    def scoped(self, candidate=None):
        return graph._subject_evidence_packet(self.evidence(candidate), ["tokenbook"])

    def test_matching_unhydrated_candidate_only_vetoes_old_evidence(self):
        scoped = self.scoped()
        temporal = graph._temporal_context(scoped)
        self.assertEqual(temporal["newest_evidence_at"], "2026-10-13T12:00:00Z")
        self.assertEqual(temporal["eligible_sources"], [])
        self.assertNotIn(self.NEW, graph._source_texts(scoped))
        self.assertNotIn(self.NEW, graph._readable_source_texts(scoped))
        self.assertNotIn(self.NEW, graph._allowed_provenance(scoped))
        self.assertEqual(scoped, graph._subject_evidence_packet(scoped, ["tokenbook"]))
        scoped["temporal_context"] = temporal
        distilled = graph._current_evidence_packet(scoped)
        self.assertNotIn("temporal_candidates", distilled)
        self.assertEqual(distilled["semantic_notes"], [])
        self.assertEqual(distilled["facts"], [])

    async def test_actual_current_answer_abstains_before_distillation(self):
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=self.evidence())), \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock()) as distill, \
             mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl(self.QUERY)
        self.assertIsNone(result["answer"])
        self.assertEqual(result["confidence"], "not_found")
        self.assertTrue(result["escalate"])
        distill.assert_not_awaited()

    def test_description_match_without_body_remains_a_veto(self):
        scoped = self.scoped({"name": "2026-10-13-120000-runtime-update", "description": "TokenBook changed", "content": "  "})
        self.assertEqual(graph._temporal_context(scoped)["eligible_sources"], [])

    def test_unrelated_newer_candidate_does_not_veto_subject_evidence(self):
        for candidate in (
            {"name": "2026-10-13-120000-tokenbridge-update", "description": "TokenBridge changed"},
            {"name": "2026-10-13-120000-tokenbookish-update"},
            {"name": self.NEW, "relevant": False},
        ):
            with self.subTest(candidate=candidate):
                self.assertEqual(graph._temporal_context(self.scoped(candidate))["eligible_sources"], [self.OLD])

    def test_readable_newer_candidate_supports_current_evidence(self):
        scoped = self.scoped({"name": self.NEW, "content": "TokenBook now uses v1.0.1."})
        self.assertEqual(graph._temporal_context(scoped)["eligible_sources"], [self.NEW])

    async def test_superseded_history_survives_newer_metadata(self):
        p = self.evidence()
        p["facts"][0]["current"] = False
        parsed = {"answer": "TokenBook used v1.0.0 on October 1.", "confidence": "medium",
                  "sources": [self.OLD], "escalate": False}
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=p)), \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock(return_value=parsed)) as distill, \
             mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl("What version did TokenBook use on October 1, 2026?")
        self.assertEqual(result["answer"], parsed["answer"])
        distill.assert_awaited_once()
        self.assertNotIn("temporal_candidates", distill.call_args.args[1])
        self.assertNotIn(self.NEW, distill.call_args.args[1])

    async def test_blank_ordinary_body_does_not_become_readable_support(self):
        with mock.patch.object(graph, "FUSION", True), \
             mock.patch.object(wiki.idx, "search", return_value=[{"name": self.NEW, "score": .7}]), \
             mock.patch.object(wiki.idx, "get", return_value={"body": "   \n"}):
            hits = await graph._wiki_semantic_hits(QUESTION, 8, answer_evidence=True)
        self.assertNotIn("content", hits[0])
        scoped = self.scoped(hits[0])
        self.assertEqual(graph._temporal_context(scoped)["eligible_sources"], [])


class CitationCapAndUnicodeTests(unittest.IsolatedAsyncioTestCase):
    def evidence(self):
        names = [f"2026-10-13-12000{i}-tokenbook-note" for i in range(6)]
        p = {
            "facts": [{"source_slug": name, "current": True, "fact": "TokenBook uses its own inference deployment."}
                      for name in names],
            "entities": [], "source_excerpt": None, "semantic_notes": [],
        }
        return names, p

    def test_unicode_word_neighbors_are_not_exact_subject_matches(self):
        for text in ("éTokenBook", "TokenBooké", "中TokenBook", "TokenBook中", "1TokenBook", "TokenBook_name"):
            with self.subTest(text=text):
                self.assertFalse(graph._mentions_subject(text, "tokenbook"))
        for text in ("TokenBook", "(TokenBook)", "TokenBook’s", "Le service TokenBook fonctionne", "中文。TokenBook。"):
            with self.subTest(text=text):
                self.assertTrue(graph._mentions_subject(text, "tokenbook"))

    async def test_sixth_unrelated_citation_is_rejected_on_normal_answer_path(self):
        names, p = self.evidence()
        p["facts"].append({"source_slug": BRIDGE, "current": True, "fact": "TokenBridge uses claude-max."})
        parsed = {"answer": "TokenBook uses claude-max.", "confidence": "high",
                  "sources": names[:5] + [BRIDGE], "escalate": False}
        with mock.patch.object(graph, "_assemble_packet", new=mock.AsyncMock(return_value=p)), \
             mock.patch.object(graph, "_distill", new=mock.AsyncMock(return_value=parsed)), \
             mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl(QUESTION)
        self.assertIsNone(result["answer"])
        self.assertEqual(result["confidence"], "not_found")
        self.assertTrue(result["escalate"])

    def test_sixth_citation_is_checked_independent_of_prior_packet_filter(self):
        names, p = self.evidence()
        p["facts"].append({"source_slug": BRIDGE, "current": True, "fact": "TokenBridge uses claude-max."})
        for extra in (BRIDGE, "invented-source", CREDENTIAL):
            with self.subTest(extra=extra):
                result, _, action = graph._validate_distilled_answer({
                    "answer": "TokenBook uses claude-max.", "confidence": "high",
                    "sources": names[:5] + [extra], "escalate": False,
                }, p, QUESTION)
                self.assertEqual(action, "subject_mismatch")
                self.assertIsNone(result["answer"])

    def test_all_valid_citations_still_have_five_source_display_cap(self):
        names, p = self.evidence()
        result, _, action = graph._validate_distilled_answer({
            "answer": "TokenBook uses its own inference deployment.", "confidence": "high",
            "sources": names, "escalate": False,
        }, p, "What is the TokenBook inference deployment?")
        self.assertEqual(action, "accepted")
        self.assertEqual(result["sources"], names[:5])

    def test_unicode_attached_word_cannot_supply_citation_support(self):
        names, p = self.evidence()
        p["facts"] = [{"source_slug": names[0], "current": True, "fact": "éTokenBook uses claude-max."}]
        result, _, action = graph._validate_distilled_answer({
            "answer": "TokenBook uses claude-max.", "confidence": "high", "sources": [names[0]],
        }, p, QUESTION)
        self.assertEqual(action, "subject_mismatch")
        self.assertIsNone(result["answer"])

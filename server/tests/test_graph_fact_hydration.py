"""Synthetic source context; no live wiki, provider, or diagnostic-note evidence."""
import copy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
import graph

SUBJECT = "CanvasBridge"
QUESTION = "Where is CanvasBridge's key location recorded, and which image models and request fields worked in past projects?"
MODEL = "2026-10-03-120000-original-model-record"
REQUEST = "2026-10-04-120000-original-request-record"
DIAG = "2026-11-05-120000-diagnostic-example"


class Index:
    def __init__(self, bodies, hits=()):
        self.bodies, self.hits, self.reads = bodies, list(hits), []

    def get(self, name):
        self.reads.append(name)
        body = self.bodies.get(name)
        if isinstance(body, Exception):
            raise body
        return {"body": body} if body is not None else None

    def search(self, query, k):
        return self.hits[:k]


def fact(slug, current=True):
    return {"source_slug": slug, "fact": f"{SUBJECT} records a key location.", "current": current}


def packet(facts, hits=(), excerpt=None):
    return {"facts": facts, "entities": [], "semantic_notes": list(hits), "source_excerpt": excerpt}


class FactHydrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_assembly_sends_original_details_beside_diagnostic_top_episode(self):
        model_body = f"{SUBJECT} key location is /fixtures/vault/API_KEY.\n\n" + ("Background context. " * 220) + (
            f"\n\n{SUBJECT} image models: the successful historical run used the recorded production model, not the diagnostic example."
        )
        request_body = f"{SUBJECT} request format used JSON fields model and prompt in the successful historical run."
        diagnostic = f"Question quoted for diagnosis: {QUESTION}\nThe fictional test name DIAGNOSTIC_ONLY is an example, not operator history."
        idx = Index({MODEL: model_body, REQUEST: request_body, "unrelated": "OtherSystem unrelated notes."},
                    [{"name": "unrelated", "score": .9, "description": "OtherSystem", "type": "source"}])
        edges = [SimpleNamespace(fact=f"{SUBJECT} records a key location.", episodes=[SimpleNamespace(name=slug)], invalid_at=None)
                 for slug in (MODEL, MODEL, REQUEST, MODEL)]
        g = SimpleNamespace(search_=AsyncMock(return_value=SimpleNamespace(
            edges=edges, nodes=[], episodes=[SimpleNamespace(name=DIAG, content=diagnostic)])))
        received = []

        async def distill(question, raw):
            data = json.loads(raw)
            received.append(data)
            texts = graph._readable_source_texts(data)
            # This assertion exercises actual assembly, hydration, and subject pruning.
            self.assertIn("/fixtures/vault/API_KEY", texts[MODEL])
            self.assertIn("recorded production model", texts[MODEL])
            self.assertIn("JSON fields model and prompt", texts[REQUEST])
            self.assertIn("fictional test name", texts[DIAG])
            self.assertNotIn("unrelated", texts)
            self.assertEqual(question, QUESTION)
            return {"answer": "CanvasBridge's historical run used the recorded production model and JSON fields model and prompt.",
                    "confidence": "high", "sources": [MODEL, REQUEST]}

        with patch.dict("sys.modules", {"wiki": SimpleNamespace(idx=idx)}), \
             patch.object(graph, "FUSION", True), patch.object(graph, "_get_graph", AsyncMock(return_value=g)), \
             patch.object(graph, "_distill", side_effect=distill) as model, patch.object(graph, "_record_query") as log:
            result = await graph._graph_answer_impl(QUESTION)
        self.assertEqual(model.await_count, 1)
        self.assertEqual(result["sources"], [MODEL, REQUEST])
        self.assertIsNotNone(result["answer"])
        self.assertEqual(idx.reads.count(MODEL), 1)
        self.assertEqual(idx.reads.count(REQUEST), 1)
        self.assertNotIn("recorded production model", json.dumps(log.call_args_list, default=str))
        self.assertNotIn("DIAGNOSTIC_ONLY", json.dumps(log.call_args_list, default=str))
        self.assertEqual(len(received), 1)

    async def test_new_body_budget_is_deterministic_and_current_facts_go_first(self):
        slugs = [f"2026-10-{i+1:02d}-120000-budget-source" for i in range(6)]
        idx = Index({slug: (f"{SUBJECT} image model request fields. " * 400) for slug in slugs})
        evidence = packet([fact(slugs[0], False), *[fact(slug) for slug in slugs[1:]], fact(slugs[1])])
        with patch.dict("sys.modules", {"wiki": SimpleNamespace(idx=idx)}):
            await graph._hydrate_fact_sources(evidence, QUESTION)
        self.assertEqual(idx.reads, slugs[1:5])
        self.assertEqual([hit["name"] for hit in evidence["semantic_notes"]], slugs[1:5])
        self.assertLessEqual(sum(len(hit["content"]) for hit in evidence["semantic_notes"]), 10000)
        self.assertTrue(all(len(hit["content"]) <= 2500 for hit in evidence["semantic_notes"]))
        body = idx.bodies[slugs[1]]
        self.assertEqual(graph._fact_body_excerpt(body, QUESTION), graph._fact_body_excerpt(body, QUESTION))

    async def test_combined_readable_body_budget_and_subject_filter(self):
        slugs = [f"2026-10-0{i+1}-120000-source" for i in range(4)]
        body = "CanvasBridge relevant context. " + "x" * (2500 - len("CanvasBridge relevant context. "))
        idx = Index({slug: body for slug in slugs})
        evidence = packet([fact(slug) for slug in slugs],
                          [{"name": f"semantic-{i}", "content": body} for i in range(3)],
                          {"slug": DIAG, "content": body})
        with patch.dict("sys.modules", {"wiki": SimpleNamespace(idx=idx)}):
            await graph._hydrate_fact_sources(evidence, QUESTION)
        self.assertEqual(len(idx.reads), 4)
        chars = len(evidence["source_excerpt"]["content"]) + sum(len(h.get("content", "")) for h in evidence["semantic_notes"])
        self.assertEqual(chars, 20000)
        evidence["semantic_notes"][0]["content"] = "OtherSystem unrelated private detail."
        filtered = graph._subject_evidence_packet(evidence, ["canvasbridge"])
        self.assertNotIn("unrelated private detail", json.dumps(filtered))
        self.assertEqual(len(filtered["facts"]), 4)

    async def test_overlap_reuses_bodies_and_deduplicates_source_identity(self):
        idx = Index({})
        evidence = packet([fact(MODEL), fact(MODEL)],
                          [{"name": MODEL, "content": "CanvasBridge model and request context."},
                           {"name": MODEL, "content": "duplicate"}],
                          {"slug": MODEL, "content": "CanvasBridge context."})
        with patch.dict("sys.modules", {"wiki": SimpleNamespace(idx=idx)}):
            await graph._hydrate_fact_sources(evidence, QUESTION)
        self.assertEqual(idx.reads, [])
        self.assertEqual(len(evidence["semantic_notes"]), 1)
        self.assertNotIn("content", evidence["semantic_notes"][0])
        self.assertEqual(evidence["source_excerpt"]["content"], "CanvasBridge model and request context.")
        self.assertEqual(graph._allowed_provenance(evidence)[MODEL], "strong")

    async def test_unavailable_source_preserves_thin_facts_and_logs_only_error_type(self):
        idx = Index({MODEL: RuntimeError("PRIVATE_SOURCE_BODY")})
        evidence = packet([fact(MODEL), fact(REQUEST)])
        before = copy.deepcopy(evidence["facts"])
        with patch.dict("sys.modules", {"wiki": SimpleNamespace(idx=idx)}), self.assertLogs(graph.log, "WARNING") as logs:
            await graph._hydrate_fact_sources(evidence, QUESTION)
        self.assertEqual(evidence["facts"], before)
        self.assertEqual(evidence["semantic_notes"], [])
        self.assertNotIn("PRIVATE_SOURCE_BODY", " ".join(logs.output))

    async def test_superseded_context_stays_historical_and_fresh_source_stays_eligible(self):
        fresh = "2026-11-07-120000-current-record"
        superseded = "2026-12-01-120000-superseded-record"
        idx = Index({MODEL: "CanvasBridge older record.", fresh: "CanvasBridge current model record.",
                     superseded: "CanvasBridge superseded history."})
        evidence = packet([fact(superseded, False), fact(fresh), fact(MODEL)],
                          excerpt={"slug": DIAG, "content": "CanvasBridge prior diagnosis."})
        with patch.dict("sys.modules", {"wiki": SimpleNamespace(idx=idx)}):
            await graph._hydrate_fact_sources(evidence, QUESTION)
        self.assertIn(superseded, graph._readable_source_texts(evidence))
        self.assertNotIn(superseded, graph._source_texts(evidence))
        evidence = graph._subject_evidence_packet(evidence, ["canvasbridge"])
        evidence["temporal_context"] = graph._temporal_context(evidence)
        current = graph._current_evidence_packet(evidence)
        self.assertIn(fresh, current["temporal_context"]["eligible_sources"])
        for excluded in (MODEL, superseded, DIAG):
            self.assertNotIn(excluded, current["temporal_context"]["eligible_sources"])
            self.assertNotIn(excluded, graph._readable_source_texts(current))
        self.assertIn(fresh, graph._allowed_provenance(current))

    async def test_superseded_overlapping_episode_cannot_restore_current_eligibility(self):
        evidence = packet([fact(DIAG, False)], [{"name": DIAG, "content": "CanvasBridge historical context."}],
                          {"slug": DIAG, "content": "CanvasBridge old diagnosis."})
        await graph._hydrate_fact_sources(evidence, QUESTION)
        self.assertFalse(evidence["source_excerpt"]["current"])
        self.assertFalse(evidence["semantic_notes"][0]["current"])
        self.assertEqual(graph._source_texts(evidence), {})
        self.assertEqual(graph._temporal_context(evidence)["eligible_sources"], [])
        self.assertIn(DIAG, graph._readable_source_texts(evidence))

    async def test_newer_unreadable_candidate_still_vetoes_stale_readable_context(self):
        newer = "2026-12-01-120000-canvasbridge-latest"
        idx = Index({MODEL: "CanvasBridge recorded historical context."})
        evidence = packet([fact(MODEL)], [{"name": newer, "description": "CanvasBridge update"}])
        with patch.dict("sys.modules", {"wiki": SimpleNamespace(idx=idx)}):
            await graph._hydrate_fact_sources(evidence, QUESTION)
        evidence = graph._subject_evidence_packet(evidence, ["canvasbridge"])
        self.assertEqual(graph._temporal_context(evidence)["eligible_sources"], [])

    async def test_graph_search_packet_does_not_hydrate_fact_sources(self):
        edge = SimpleNamespace(fact="CanvasBridge key reference.", episodes=[SimpleNamespace(name=MODEL)], invalid_at=None)
        g = SimpleNamespace(search_=AsyncMock(return_value=SimpleNamespace(edges=[edge], nodes=[], episodes=[])))
        idx = Index({MODEL: "PRIVATE_SOURCE_BODY"})
        with patch.dict("sys.modules", {"wiki": SimpleNamespace(idx=idx)}), patch.object(graph, "FUSION", True), \
             patch.object(graph, "_get_graph", AsyncMock(return_value=g)):
            evidence = await graph._assemble_packet(QUESTION, 5)
        self.assertEqual(idx.reads, [])
        config = g.search_.call_args.kwargs["config"]
        original = graph.COMBINED_HYBRID_SEARCH_RRF
        self.assertIsNone(config.community_config)
        self.assertIsNotNone(original.community_config)
        self.assertEqual(config.limit, 5)
        for field in ("edge_config", "node_config", "episode_config"):
            self.assertEqual(getattr(config, field), getattr(original, field))
            self.assertIsNot(getattr(config, field), getattr(original, field))
        self.assertEqual(set(evidence), {"query", "facts", "entities", "source_excerpt", "semantic_notes"})
        self.assertNotIn("PRIVATE_SOURCE_BODY", json.dumps(evidence))

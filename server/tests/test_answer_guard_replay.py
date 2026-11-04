"""Fixed full-question replay, not a reconstruction of deleted private evaluations."""
import os
import unittest

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
import graph

# Inputs and expected drafts are fixed. No model judge or independent redistill.
CASES = (
    ("Did the fixture record an earlier successful change, or does the absence of a later receipt prove it never happened?",
     "The fixture records an earlier successful change."),
    ("What resources can the fixture reader read, according to its recorded scope?",
     "The fixture reader can read pod metadata."),
    ("What did the fixture's local HTTP check establish about LAN reachability?",
     "The fixture establishes only a local response, not LAN reachability."),
    ("What does the fixture's app-only dev release receipt establish about production readiness?",
     "The fixture records dev checks only, not production readiness."),
    ("Which stored copy does the fixture describe as an archive rather than authoritative state?",
     "The fixture describes the cold copy as an archive."),
    ("What does the fixture sandbox contract permit the worker to do?",
     "The fixture sandbox contract permits file reads."),
)


class FixedAnswerReplayTests(unittest.TestCase):
    def test_supported_drafts_keep_older_grounding_and_need_real_provenance(self):
        slug = "2026-10-17-000000-synthetic-fixture"
        for question, answer in CASES:
            with self.subTest(question=question):
                packet = {"facts": [{"fact": answer, "source_slug": slug, "current": True}],
                          "semantic_notes": [], "source_excerpt": {"slug": slug, "content": answer}}
                parsed = {"answer": answer, "confidence": "high", "sources": [slug]}
                result, _, action = graph._validate_distilled_answer(parsed, packet, question)
                self.assertIsNotNone(result["answer"], action)
                self.assertNotIn(action, {"network_vantage_mismatch", "release_scope_mismatch",
                                          "storage_authority_mismatch", "sandbox_contract_mismatch"})
                result, _, action = graph._validate_distilled_answer(dict(parsed, sources=["invented"]), packet, question)
                self.assertIsNone(result["answer"])
                self.assertEqual(action, "rejected")

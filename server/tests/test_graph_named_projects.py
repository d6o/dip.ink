"""dip.ink#8: an answer about a named project needs evidence about that project."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")

import graph  # noqa: E402

QUESTION = ("What did Diego authorize for the scheduled October 11 retirement of batomon.com's "
            "old home k3s storage, and where is the approval record?")
EEE_HANDOFF = "2026-10-07-153336-eee-native-retirement-controller-handoff"
EEE_HARDEN = "2026-10-07-161633-eee-legacy-retirement-controller-hardening"
CHESSRED = "2026-09-06-221543-chessred-runtime-retired-source-retained"
BATOMON = "2026-09-26-193404-batomon-resource-review-sep26-d1-r2-home-stack"


def packet() -> dict:
    return {"facts": [
        {"source_slug": CHESSRED, "current": True,
         "fact": "The k3s repository is the source repository used for k3s configuration and the ChessRed retirement record."},
        {"source_slug": EEE_HARDEN, "current": True,
         "fact": "The plan record for the Devbox legacy retirement controller is /home/diego/Playground/devbox/.scratch/eee-native-retirement.md."},
        {"source_slug": EEE_HANDOFF, "current": True,
         "fact": "Scheduled task dc436c21acc88ea85aaad937b5161c4c concerns Batomon, is scheduled for October 11, 2026, and has continuation authority."},
        {"source_slug": BATOMON, "current": True,
         "fact": "Retiring the home k3s stack is an open decision item for Diego in the Batomon review."},
    ], "entities": [], "source_excerpt": None, "semantic_notes": []}


def validate(answer: str, sources: list[str], question: str = QUESTION):
    return graph._validate_distilled_answer(
        {"answer": answer, "confidence": "high", "sources": sources, "escalate": False}, packet(), question)


class NamedProjectTests(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(graph._named_projects(QUESTION), ["batomon"])
        self.assertEqual(graph._named_projects("How does fc.ia.br bill and what does hookfork.com relay?"), ["fc", "hookfork"])
        self.assertEqual(graph._named_projects("What port does the memory server use?"), [])
        self.assertEqual(graph._named_projects("What is in docs/BILLING.md?"), [])

    def test_exact_ticket_answer_is_rejected(self):
        answer = ("Diego authorized the scheduled October 11, 2026, retirement task dc436c21acc88ea85aaad937b5161c4c "
                  "for Batomon's old home k3s storage with continuation authority. The approval record is in the k3s "
                  "repository, specifically at /home/diego/Playground/devbox/.scratch/eee-native-retirement.md.")
        result, grounded, action = validate(answer, [EEE_HANDOFF, EEE_HARDEN, CHESSRED])
        self.assertEqual(action, "project_mismatch")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["confidence"], "not_found")
        self.assertTrue(result["escalate"])
        self.assertIn("eee-native-retirement.md", result["superseded_note"])
        self.assertIn(EEE_HARDEN, result["superseded_note"])

    def test_identifier_in_no_cited_source_is_rejected(self):
        result, _, action = validate(
            "The approval record is /home/diego/Playground/batomon.com/docs/APPROVAL.md.",
            [BATOMON], "Where is the batomon.com storage retirement approval record?")
        self.assertEqual(action, "project_mismatch")
        self.assertIsNone(result["answer"])

    def test_plain_statement_without_identifiers_is_not_blocked(self):
        result, _, action = validate("The record is in the k3s repository.", [CHESSRED],
                                     "Where is the batomon.com retirement record?")
        self.assertNotEqual(action, "project_mismatch")

    def test_short_key_matches_word_starts_only(self):
        p = packet()
        self.assertEqual(graph._named_projects("what does fc.ia.br use?"), ["fc"])

    def test_answer_supported_by_project_sources_is_accepted(self):
        result, _, action = validate(
            "Task dc436c21acc88ea85aaad937b5161c4c concerns Batomon and is scheduled for October 11.",
            [EEE_HANDOFF, BATOMON])
        self.assertIn(action, ("accepted", "downgraded"))
        self.assertIsNotNone(result["answer"])

    def test_question_without_project_is_unchanged(self):
        result, _, action = validate(
            "The plan record is /home/diego/Playground/devbox/.scratch/eee-native-retirement.md.",
            [EEE_HARDEN], "Where is the devbox legacy retirement controller plan record?")
        self.assertEqual(action, "accepted")


if __name__ == "__main__":
    unittest.main()

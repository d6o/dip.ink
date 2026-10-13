"""dip.ink#2: an answer about a named date needs evidence from that time."""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")

import graph  # noqa: E402

NOW = datetime(2026, 10, 11, 5, 0, tzinfo=timezone.utc)
JULY_A = "2026-07-30-231415-k3s-recovery-final-state-and-open-actions"
JULY_B = "2026-07-30-222704-batomon-wiki-polish-implemented"
OCT = "2026-10-11-031000-k3s-october-restart-wave"
QUESTION = ("What is the latest confirmed recovery state and release-retry hold for the "
            "October 11 k3s incident affecting batomon.com Gitea runners?")


def packet(*slugs: str) -> dict:
    return {
        "facts": [{"fact": "Gitea runners stay at zero", "source_slug": s, "current": True} for s in slugs],
        "entities": [],
        "source_excerpt": {"slug": slugs[0], "content": "runners at zero"} if slugs else None,
        "semantic_notes": [],
    }


def distilled(*slugs: str) -> dict:
    return {"answer": "Gitea runners stay at zero.",
            "confidence": "high", "sources": list(slugs), "escalate": False}


class NamedDateWindowTests(unittest.TestCase):
    def labels(self, question: str) -> list[str]:
        return [label for _, _, label in graph._named_date_windows(question, NOW)]

    def test_forms(self):
        self.assertEqual(self.labels(QUESTION), ["2026-10-11"])
        self.assertEqual(self.labels("what broke on 2026-09-03?"), ["2026-09-03"])
        self.assertEqual(self.labels("the 3rd of Sept 2026 outage"), ["2026-09-03"])
        self.assertEqual(self.labels("Oct. 2, 2025 release"), ["2025-10-02"])
        self.assertEqual(self.labels("what changed in July 2026"), ["2026-07"])
        self.assertEqual(self.labels("deploys in 2026-08"), ["2026-08"])

    def test_no_dates(self):
        self.assertEqual(self.labels("What port does the memory server use?"), [])
        self.assertEqual(self.labels("May I deploy on Friday?"), [])
        self.assertEqual(self.labels("what changed in 2026"), [])

    def test_missing_year_does_not_point_into_the_future(self):
        # On 2026-10-11, "December 20" means 2025-12-20.
        self.assertEqual(self.labels("the December 20 cutover"), ["2025-12-20"])


class DateMismatchGuardTests(unittest.TestCase):
    def validate(self, question: str, *slugs: str):
        real = graph._named_date_windows
        with mock.patch.object(graph, "_named_date_windows", side_effect=lambda q: real(q, NOW)):
            return graph._validate_distilled_answer(distilled(*slugs), packet(*slugs), question)

    def test_july_sources_cannot_answer_an_october_incident(self):
        result, grounded, action = self.validate(QUESTION, JULY_A, JULY_B)
        self.assertEqual(action, "date_mismatch")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["confidence"], "not_found")
        self.assertTrue(result["escalate"])
        self.assertIn("2026-10-11", result["superseded_note"])
        self.assertIn("2026-07-30", result["superseded_note"])

    def test_source_from_the_named_time_is_accepted(self):
        result, _, action = self.validate(QUESTION, JULY_A, OCT)
        self.assertEqual(action, "accepted")
        self.assertEqual(result["confidence"], "high")

    def test_timeless_question_keeps_old_sources(self):
        result, _, action = self.validate("What is the k3s recovery runbook?", JULY_A)
        self.assertEqual(action, "accepted")
        self.assertEqual(result["confidence"], "high")

    def test_historical_month_question_uses_that_month(self):
        result, _, action = self.validate("What was the k3s recovery state in July 2026?", JULY_A)
        self.assertEqual(action, "accepted")


if __name__ == "__main__":
    unittest.main()

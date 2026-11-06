"""Validate synthetic expectations and record API gaps, not temporal API passes."""
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
import graph


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def reference_answer(records, query):
    """Oracle for versioned synthetic rows, not an implementation of Graphiti."""
    valid, known = stamp(query["valid_as_of"]), stamp(query["known_as_of"])
    answer = set()
    for row in records:
        if row["subject"] != query["subject"]:
            continue
        if known < stamp(row["known_at"]) or (row["known_until"] and known >= stamp(row["known_until"])):
            continue
        if row["valid_at"] is None:
            return None
        if stamp(row["valid_at"]) <= valid and (row["invalid_at"] is None or valid < stamp(row["invalid_at"])):
            answer.add(row["fact_id"])
    return sorted(answer)


class TemporalSpecTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / "evaluation_cases" / "temporal-v1.json"
        self.fixture = json.loads(path.read_text())

    def test_reference_expectations_are_consistent_but_all_api_cases_remain_gaps(self):
        self.assertEqual(len(self.fixture["cases"]), 10)
        for query in self.fixture["cases"]:
            with self.subTest(case=query["id"]):
                self.assertEqual(reference_answer(self.fixture["records"], query), query["expected_fact_ids"])
                self.assertEqual(query["api_status"], "gap")
                self.assertTrue(query["gap"])

    def test_request_time_flags_handle_future_bounds_without_claiming_asof_api(self):
        future_start = graph._valid_at_window(SimpleNamespace(valid_at="2099-01-01", invalid_at=None))
        future_end = graph._valid_at_window(SimpleNamespace(valid_at="2026-01-01", invalid_at="2099-01-01"))
        # Request-time interval flags are corrected. They do not implement the
        # fixture's explicit valid-as-of plus known-as-of query contract.
        self.assertFalse(future_start["current"])
        self.assertTrue(future_end["current"])
        gaps = {case["id"]: case["api_status"] for case in self.fixture["cases"]}
        self.assertEqual(gaps["future-start-not-yet-valid"], "gap")
        self.assertEqual(gaps["future-end-still-valid"], "gap")

"""Fixture/input validity only. No reader, reducer, or backend is evaluated here."""
from collections import Counter
import copy
from datetime import datetime
import json
import unittest

from evaluation_cases.present_state_v1 import build, source_inputs, reader_packet


class PresentStateSpecTests(unittest.TestCase):
    def test_compact_generator_is_repeatable_and_trials_remain_unrun(self):
        data = build()
        self.assertEqual(data, build())
        self.assertEqual(len(data["cases"]), 14)
        self.assertEqual(len({case["id"] for case in data["cases"]}), 14)
        self.assertEqual(sum(case["grader"]["outcome"] == "value" for case in data["cases"]), 13)
        for case in data["cases"]:
            with self.subTest(case=case["id"]):
                self.assertTrue(all(value == "unrun" for value in case["backend_trials"].values()))
                self.assertNotRegex(case["question"].lower(), r"\b(current|currently|latest|now|today|yesterday|20\d{2})\b")
                self.assertIn("does", case["question"])
                ids = {source["id"] for source in case["sources"]}
                self.assertEqual(len(ids), len(case["sources"]))
                self.assertTrue(set(case["grader"]["supporting_source_ids"]).issubset(ids))
                for source in case["sources"]:
                    self.assertEqual(set(source), {"id", "captured_at", "text"})
                    datetime.fromisoformat(source["captured_at"].replace("Z", "+00:00"))
                    self.assertTrue(source["text"].strip())

    def test_expectations_and_gold_id_annotations_never_change_inputs(self):
        for case in build()["cases"]:
            original_inputs, original_packet = source_inputs(case), reader_packet(case)
            annotated = copy.deepcopy(case)
            annotated["grader"] = {"value": "GOLD_ONLY", "supporting_source_ids": ["GOLD_ID"], "outcome": "GOLD_ONLY"}
            annotated["scenario"] = "GOLD_ONLY"
            annotated["controls"] = {"gold_source_ids": ["GOLD_ID"]}
            for source in annotated["sources"]:
                source["expected_current"] = "GOLD_ONLY"
            self.assertEqual(source_inputs(annotated), original_inputs)
            self.assertEqual(reader_packet(annotated), original_packet)
            self.assertNotIn("GOLD_ONLY", json.dumps(reader_packet(annotated)))
            self.assertEqual({hit["name"] for hit in original_packet["semantic_notes"]},
                             {source["id"] for source in case["sources"]})
            for hit in original_packet["semantic_notes"]:
                self.assertEqual(set(hit), {"name", "content", "captured_at"})
            self.assertEqual(original_packet["facts"], [])

    def test_frequency_and_order_controls_are_real_not_different_stories(self):
        baseline, shuffled = build()["cases"][:2]
        self.assertEqual(len(baseline["sources"]), 101)
        frequencies = Counter(source["text"] for source in baseline["sources"])
        self.assertEqual(max(frequencies.values()), 100)
        keyed = lambda case: {source["id"]: source for source in case["sources"]}
        self.assertEqual(keyed(baseline), keyed(shuffled))
        self.assertNotEqual([s["id"] for s in baseline["sources"]], [s["id"] for s in shuffled["sources"]])
        self.assertEqual(baseline["grader"], shuffled["grader"])

    def test_capture_time_and_source_count_do_not_form_a_gold_oracle(self):
        cases = {case["id"]: case for case in build()["cases"]}
        # This is a fixture design check, not a prediction/reduction algorithm.
        for identity in ("ps-03", "ps-05", "ps-06", "ps-07", "ps-08", "ps-11", "ps-14"):
            case = cases[identity]
            newest = max(case["sources"], key=lambda source: source["captured_at"])
            self.assertNotIn(newest["id"], case["grader"]["supporting_source_ids"])
        self.assertEqual(len(cases["ps-01"]["sources"]), len(cases["ps-04"]["sources"]))
        self.assertNotEqual(cases["ps-01"]["grader"]["value"], cases["ps-04"]["grader"]["value"])
        self.assertEqual(cases["ps-09"]["sources"][0]["captured_at"][:4], "2020")
        self.assertEqual(cases["ps-09"]["grader"]["outcome"], "value")
        conflict = cases["ps-13"]
        supporters = [source for source in conflict["sources"] if source["id"] in conflict["grader"]["supporting_source_ids"]]
        self.assertEqual(len({source["captured_at"] for source in supporters}), 1)
        self.assertEqual(conflict["grader"]["outcome"], "uncertain")
        self.assertIsNone(conflict["grader"]["value"])

    def test_all_evidence_packet_preserves_every_body_without_budget_clipping(self):
        for case in build()["cases"]:
            packet = reader_packet(case)
            bodies = {hit["name"]: hit["content"] for hit in packet["semantic_notes"]}
            self.assertEqual(bodies, {source["id"]: source["text"] for source in case["sources"]})
            self.assertLessEqual(sum(map(len, bodies.values())), 20_000)
            self.assertTrue(all(len(body) <= 2500 for body in bodies.values()))
        oversized = copy.deepcopy(build()["cases"][0])
        oversized["sources"][0]["text"] = "x" * 20001
        with self.assertRaises(ValueError):
            reader_packet(oversized)

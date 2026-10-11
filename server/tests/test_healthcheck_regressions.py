from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")

from loops import memory_healthcheck as hc  # noqa: E402

WRONG = {
    "dip.ink#1": {"answer": "Memory v0.1.6; pi-runner v0.1.11", "confidence": "high", "sources": []},
    "dip.ink#2": {"answer": "Runners stay at zero; 668ff93 not deployed; run 74 pending", "confidence": "high",
                  "sources": ["2026-07-30-231415-k3s-recovery-final-state-and-open-actions"]},
    "dip.ink#7": {"answer": "Historical or unresolved deliveries remain unaffected by claim revocation.",
                  "confidence": "medium", "sources": []},
    "dip.ink#8": {"answer": "The record is /home/diego/Playground/devbox/.scratch/eee-native-retirement.md.",
                  "confidence": "high", "sources": []},
}
RIGHT = {
    "dip.ink#7": {"answer": "Revocation pauses unresolved affected deliveries for owner review; terminal history stays.",
                  "confidence": "high", "sources": ["2026-10-11-092841-hookfork-claim-revocation-and-inactive-prices"]},
}


class RegressionProbeTests(unittest.TestCase):
    def run_with(self, answers):
        def fake_get(url, timeout=30):
            for ticket, question, _ in hc.REGRESSION_PROBES:
                if hc.urllib.parse.quote(question) in url:
                    return answers.get(ticket, {"answer": None, "confidence": "not_found", "sources": []})
            raise AssertionError(url)
        hc.failures.clear()
        with mock.patch.object(hc, "_get_json", side_effect=fake_get):
            hc.check_regressions("http://memory")
        return list(hc.failures)

    def test_each_known_wrong_answer_fails(self):
        failures = self.run_with(WRONG)
        self.assertEqual(len(failures), 4)
        for ticket in WRONG:
            self.assertTrue(any(ticket in f for f in failures), ticket)

    def test_abstention_and_correct_answers_pass(self):
        self.assertEqual(self.run_with({}), [])
        self.assertEqual(self.run_with(RIGHT), [])

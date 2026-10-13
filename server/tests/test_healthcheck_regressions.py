from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")

from loops import memory_healthcheck as hc  # noqa: E402

WRONG = {
    "dip.ink#15": {"answer": "The approved method for Infisical credential reads is devbox-kube-read.",
                   "confidence": "medium", "sources": []},
    "dip.ink#14": {"answer": "Cleanup k3s#23 is active, with no backups remaining from the old football product.",
                   "confidence": "high", "sources": []},
    "dip.ink#11": {"answer": "TokenBook deploys Worker version 12345678-1234-1234-1234-123456789abc.",
                   "confidence": "high", "sources": []},
    "dip.ink#13": {"answer": "The current k3s project lead address is gpuhours.voa.sh/worker~session:123.",
                   "confidence": "high", "sources": []},
    "dip.ink#10": {"answer": "TokenBook runs on tokenbridge.tokenbridge.svc.cluster.local:64963 with claude-max.",
                   "confidence": "high", "sources": []},
    "dip.ink#1": {"answer": "Memory v0.1.6; pi-runner v0.1.11", "confidence": "high", "sources": []},
    "dip.ink#2": {"answer": "Runners stay at zero; 668ff93 not deployed; run 74 pending", "confidence": "high",
                  "sources": ["2026-07-30-231415-k3s-recovery-final-state-and-open-actions"]},
    "dip.ink#7": {"answer": "Historical or unresolved deliveries remain unaffected by claim revocation.",
                  "confidence": "medium", "sources": []},
    "dip.ink#8": {"answer": "The record is /home/diego/Playground/devbox/.scratch/eee-native-retirement.md.",
                  "confidence": "high", "sources": []},
}
RIGHT = {
    "dip.ink#11": {"answer": "Recorded evidence: Worker version 12345678-1234-1234-1234-123456789abc.",
                   "confidence": "medium", "sources": [], "escalate": True,
                   "observation_dates": {"fixture": "2026-10-13T04:15:36Z"}},
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
        self.assertEqual(len(failures), len(WRONG))
        for ticket in WRONG:
            self.assertTrue(any(ticket in f for f in failures), ticket)

    def test_reverse_metadata_recommendation_and_cli_failure_alert(self):
        for text in (
            "The installed devbox-kube-read with its owned kubeconfig is the approved Infisical method. No live Infisical method is approved.",
            "The current approved method for Infisical credential reads is that the pinned Infisical CLI filters custom headers. No live method is approved yet.",
        ):
            with self.subTest(text=text):
                failures = self.run_with({"dip.ink#15": {"answer": text, "confidence": "medium", "sources": []}})
                self.assertEqual(len(failures), 1)
                self.assertIn("dip.ink#15", failures[0])

    def test_safe_credential_boundaries_do_not_alert(self):
        for text in (
            "No approved live Infisical method exists. devbox-kube-read reads Kubernetes metadata only.",
            "Do not use devbox-kube-read for Infisical credentials. No approved method exists.",
            "The installed devbox-kube-read is not an approved Infisical method.",
            "The approved method is not Infisical CLI filtering. The pinned CLI discards custom headers.",
            "The method for Kubernetes metadata is devbox-kube-read; no live Infisical method is approved.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.run_with({"dip.ink#15": {"answer": text, "confidence": "medium", "sources": []}}), [])

    def test_request_unit_confusion_is_detected(self):
        failures = self.run_with({"dip.ink#10": {
            "answer": "Request limits are defined by the contract: 10,000 input tokens per unit.",
            "confidence": "high", "sources": [],
        }})
        self.assertEqual(len(failures), 1)
        self.assertIn("dip.ink#10", failures[0])

    def test_abstention_and_correct_answers_pass(self):
        self.assertEqual(self.run_with({}), [])
        self.assertEqual(self.run_with(RIGHT), [])

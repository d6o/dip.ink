from __future__ import annotations

import json
import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")

from loops import memory_alerts  # noqa: E402


def healthy_status(*, pending: int = 0, lag_seconds: float = 0.0) -> dict:
    return {
        "components": {
            "wiki": {"ready": True},
            "graph": {"ready": True},
            "git_clone": {"ready": True},
        },
        "ingest": {
            "pending": pending,
            "lag_seconds": lag_seconds,
            # Deliberately old activity is irrelevant when pending=0.
            "newest_episode": {"completed_at": "2020-01-01T00:00:00Z"},
        },
        "communities": {
            "count": 2,
            "age_seconds": 3600.0,
        },
        "queues": {
            "blocked": {"count": 0},
            "review_queue_open": 0,
        },
    }


class MemoryAlertPolicyTests(unittest.TestCase):
    def setUp(self):
        memory_alerts.failures.clear()
        memory_alerts.warnings.clear()

    def test_quiet_period_with_zero_pending_is_healthy(self):
        memory_alerts.evaluate_status(healthy_status(pending=0, lag_seconds=9999999))
        self.assertEqual(memory_alerts.failures, [])

    def test_old_pending_note_fires(self):
        with mock.patch.object(memory_alerts, "MAX_PENDING_AGE_H", 2):
            memory_alerts.evaluate_status(healthy_status(pending=1, lag_seconds=3 * 3600))
        self.assertTrue(any("ingest pending lag" in failure for failure in memory_alerts.failures))

    def test_recent_pending_note_inside_grace_passes(self):
        with mock.patch.object(memory_alerts, "MAX_PENDING_AGE_H", 2):
            memory_alerts.evaluate_status(healthy_status(pending=3, lag_seconds=90 * 60))
        self.assertEqual(memory_alerts.failures, [])

    def test_blocked_and_review_queue_are_warnings_not_failures(self):
        snapshot = healthy_status()
        snapshot["queues"] = {
            "blocked": {"count": 4},
            "review_queue_open": 2,
        }
        memory_alerts.evaluate_status(snapshot)
        self.assertEqual(memory_alerts.failures, [])
        self.assertEqual(len(memory_alerts.warnings), 2)

    def test_community_staleness_still_fires(self):
        snapshot = healthy_status()
        snapshot["communities"] = {
            "count": 2,
            "age_seconds": 9 * 86400,
        }
        with mock.patch.object(memory_alerts, "MAX_COMMUNITY_AGE_D", 8):
            memory_alerts.evaluate_status(snapshot)
        self.assertTrue(any("communities stale" in failure for failure in memory_alerts.failures))

    def test_missing_communities_still_fires(self):
        snapshot = healthy_status()
        snapshot["communities"] = {"count": 0, "age_seconds": None}
        memory_alerts.evaluate_status(snapshot)
        self.assertIn("communities: none in graph", memory_alerts.failures)

    def test_ingest_status_error_fires_instead_of_false_quiet_health(self):
        snapshot = healthy_status()
        snapshot["ingest"]["error"] = "OSError"
        memory_alerts.evaluate_status(snapshot)
        self.assertTrue(any("ingest status unavailable" in failure for failure in memory_alerts.failures))

    def test_component_down_still_fires(self):
        snapshot = healthy_status()
        snapshot["components"]["graph"] = {"ready": False, "error": "down"}
        memory_alerts.evaluate_status(snapshot)
        self.assertTrue(any("component graph not ready" in failure for failure in memory_alerts.failures))

    def test_server_down_still_fires(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            memory_alerts.check_status("http://memory")
        self.assertTrue(any("unreachable" in failure for failure in memory_alerts.failures))

    def test_status_endpoint_payload_is_evaluated(self):
        payload = json.dumps(healthy_status()).encode()

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return payload

        with mock.patch("urllib.request.urlopen", return_value=Response()):
            memory_alerts.check_status("http://memory")
        self.assertEqual(memory_alerts.failures, [])


if __name__ == "__main__":
    unittest.main()

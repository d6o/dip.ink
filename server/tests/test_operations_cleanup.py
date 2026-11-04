"""Synthetic operational faults; no real delivery, credentials, or graph writes."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
from loops import notify, memory_healthcheck as health, contradiction_janitor as janitor
from loops import zero_edge_report


class NotificationScopeTests(unittest.TestCase):
    def test_database_group_and_instance_are_explicit_on_both_state_paths(self):
        driver = Mock()
        driver.execute_query.return_value = ([{"fp": "fixture", "ts": 10}], None, None)
        with patch.dict(os.environ, {"NEO4J_DATABASE": "fixture-db", "GROUP_ID": "fixture-group", "NOTIFY_INSTANCE": "fixture-instance"}), \
             patch("neo4j.GraphDatabase.driver", return_value=driver):
            store = notify.Neo4jStateStore()
            self.assertEqual(store.get("job"), ("fixture", 10))
            store.put("job", "fixture", 20)
            store.close()
        for call in driver.execute_query.call_args_list:
            self.assertEqual(call.kwargs["database_"], "fixture-db")
            self.assertEqual(call.kwargs["group_id"], "fixture-group")
            self.assertEqual(call.kwargs["instance"], "fixture-instance")
            self.assertIn("group_id: $group_id", call.args[0])
        driver.close.assert_called_once()

    def test_owned_connection_closes_after_read_failure_and_alert_still_delivers(self):
        store = Mock()
        store.get.side_effect = RuntimeError("private fixture diagnostic")
        with patch.object(notify, "telegram_configured", return_value=True), \
             patch.object(notify, "Neo4jStateStore", return_value=store), \
             patch.object(notify, "send_telegram", return_value=True) as send:
            self.assertEqual(notify.notify("job", "title", [notify.Alert("fault", "fixture")],
                                           now=0, throttle_without_state=False), "fire")
        send.assert_called_once()
        store.close.assert_called_once()
        store.put.assert_not_called()

    def test_reminder_configuration_and_default_boundary(self):
        for value, expected in [("12", 12), ("4", 4), ("nan", 12), ("0", 12), ("bad", 12)]:
            with patch.dict(os.environ, {"NOTIFY_REPEAT_HOURS": value}):
                self.assertEqual(notify.repeat_hours(), expected)
        self.assertIsNone(notify.decide("same", 0, "same", 43199, 12))
        self.assertEqual(notify.decide("same", 0, "same", 43200, 12), "repeat")
        self.assertEqual(notify.decide("same", 0, "", 1, 12), "resolved")


class HealthScopeTests(unittest.TestCase):
    def test_matching_and_different_groups_and_missing_metadata(self):
        for group, database, valid in [("fixture", "custom", True), ("main", "custom", False),
                                       ("fixture", "neo4j", False), (None, None, False)]:
            health.failures.clear()
            with patch.dict(os.environ, {"GROUP_ID": "fixture", "NEO4J_DATABASE": "custom"}), \
                 patch.object(health, "_get_json", return_value={"components": {"graph": {"group_id": group, "database": database}}}):
                self.assertEqual(health.check_scope(), valid)
                self.assertEqual(bool(health.failures), not valid)
        health.failures.clear()


class JanitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_failure_is_not_an_empty_finding(self):
        g = SimpleNamespace(llm_client=SimpleNamespace(generate_response=AsyncMock(side_effect=RuntimeError("PRIVATE_FIXTURE"))))
        with self.assertRaisesRegex(RuntimeError, "Janitor analysis failed") as error:
            await janitor.judge_contradictions(g, "fixture", [{"fact": "fixture", "valid_at": None}])
        self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))

    async def test_schema_and_message_contract(self):
        g = SimpleNamespace(llm_client=SimpleNamespace(generate_response=AsyncMock(return_value={"pairs": [[0, 1]]})))
        self.assertEqual(await janitor.judge_contradictions(g, "fixture", [{"fact": "fixture", "valid_at": None}]), [(0, 1)])
        messages = g.llm_client.generate_response.call_args.args[0]
        self.assertEqual(messages[0].role, "user")
        g.llm_client.generate_response.return_value = {"pairs": "invalid"}
        with self.assertRaises(RuntimeError):
            await janitor.judge_contradictions(g, "fixture", [{"fact": "fixture", "valid_at": None}])

    async def test_writes_need_separate_explicit_approval(self):
        with patch.object(janitor, "DRY_RUN", False), patch.dict(os.environ, {"JANITOR_ALLOW_WRITES": "0"}), \
             patch.object(janitor, "build_graphiti") as build:
            with self.assertRaises(RuntimeError):
                await janitor.main()
            build.assert_not_called()


class ZeroEdgeTests(unittest.TestCase):
    def test_metadata_does_not_claim_a_fault_and_no_content_is_requested(self):
        driver = Mock()
        driver.execute_query.return_value = ([{"uuid": "u", "slug": "fixture", "complete": True,
                                              "edge_metadata_present": True}], None, None)
        report = zero_edge_report.inspect(driver, group="fixture", database="fixture-db")
        self.assertEqual(report["episodes"][0]["classification"], "completed_recorded_zero_not_proven_fault")
        call = driver.execute_query.call_args
        self.assertEqual(call.kwargs["routing_"], "r")
        self.assertEqual(call.kwargs["database_"], "fixture-db")
        self.assertNotIn("e.content", call.args[0])
        self.assertNotIn("r.fact", call.args[0])

    def test_missing_metadata_and_verified_provenance_are_distinct(self):
        driver = Mock()
        driver.execute_query.side_effect = [([{"uuid": "u", "slug": "fixture", "complete": None,
                                               "edge_metadata_present": False}], None, None),
                                            ([{"linked_edges": 2}], None, None)]
        report = zero_edge_report.inspect(driver, group="fixture", database="neo4j", verify_links=True)
        self.assertEqual(report["episodes"][0]["classification"], "provenance_edges_present")
        self.assertEqual(len(driver.execute_query.call_args_list), 2)

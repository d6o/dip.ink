"""Operational metadata checks against the disposable Neo4j test service."""
import json
import os
import unittest
from uuid import uuid4
from unittest.mock import patch

from loops import notify, zero_edge_report


@unittest.skipUnless(os.environ.get("NEO4J_INTEGRATION") == "1", "requires disposable Neo4j integration service")
class OperationsIntegrationTests(unittest.TestCase):
    def test_notification_state_is_partitioned_and_zero_edge_report_is_metadata_only(self):
        identity = "ops-fixture-" + uuid4().hex
        stores = []
        try:
            for suffix in ("a", "b"):
                with patch.dict(os.environ, {"GROUP_ID": identity + suffix, "NOTIFY_INSTANCE": identity}):
                    stores.append(notify.Neo4jStateStore())
            a, b = stores
            self.assertEqual(a.get(identity), ("", 0.))
            a.put(identity, "fault", 10)
            self.assertEqual(a.get(identity), ("fault", 10.))
            self.assertEqual(b.get(identity), ("", 0.))
            b.put(identity, "other-fault", 20)
            self.assertEqual(a.get(identity), ("fault", 10.))
            a._driver.execute_query(
                "CREATE (:Episodic {uuid: $uuid, name: $slug, group_id: $group, entity_edges: [], "
                "dipink_ingest_complete: true, content: 'PRIVATE_FIXTURE_BODY'})",
                uuid=identity, slug=identity, group=identity, database_=a._database,
            )
            report = zero_edge_report.inspect(a._driver, group=identity, database=a._database, verify_links=True)
            self.assertEqual(report["returned"], 1)
            self.assertEqual(report["episodes"][0]["linked_edges"], 0)
            self.assertEqual(report["episodes"][0]["classification"], "no_linked_edges_not_proven_fault")
            self.assertNotIn("PRIVATE_FIXTURE_BODY", json.dumps(report))
        finally:
            if stores:
                stores[0]._driver.execute_query("MATCH (s:DipinkAlertState {name: $name}) DELETE s", name=identity, database_=stores[0]._database)
                stores[0]._driver.execute_query("MATCH (e:Episodic {uuid: $uuid}) DETACH DELETE e", uuid=identity, database_=stores[0]._database)
            for store in stores:
                store.close()

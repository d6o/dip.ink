"""Interval-query mechanics on disposable Neo4j; not a source-ingestion trial."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
import graph
from ingest import DipInkNeo4jDriver


@unittest.skipUnless(os.environ.get("NEO4J_INTEGRATION") == "1", "requires isolated Neo4j fixture")
class ValidityQueryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_changes_does_not_report_future_activation_or_future_supersession(self):
        group = "interval-fixture-" + uuid4().hex
        driver = DipInkNeo4jDriver(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                                   os.environ["NEO4J_PASSWORD"], database=os.environ.get("NEO4J_DATABASE", "neo4j"))
        now = datetime.now(timezone.utc)
        rows = [
            {"fact": "active", "start": now - timedelta(days=1), "end": None},
            {"fact": "future-start", "start": now + timedelta(days=1), "end": None},
            {"fact": "future-end", "start": now - timedelta(days=1), "end": now + timedelta(days=1)},
            {"fact": "ended", "start": now - timedelta(days=2), "end": now - timedelta(hours=1)},
            {"fact": "unknown-start", "start": None, "end": None},
            {"fact": "invalid-interval", "start": now - timedelta(hours=1), "end": now - timedelta(days=1)},
        ]
        try:
            await driver.execute_query(
                "CREATE (a:Entity {group_id: $group, name: 'interval fixture'}) "
                "CREATE (b:Entity {group_id: $group, name: 'interval fixture'}) "
                "WITH a, b UNWIND $rows AS row CREATE (a)-[:RELATES_TO {group_id: $group, "
                "fact: row.fact, valid_at: row.start, invalid_at: row.end}]->(b)", group=group, rows=rows,
            )
            with patch.object(graph, "DEFAULT_GROUP_ID", group), \
                 patch.object(graph, "_get_graph", AsyncMock(return_value=SimpleNamespace(driver=driver))), \
                 patch.object(graph, "_record_query"):
                result = await graph.graph_changes("interval fixture", 14)
            states = {row["fact"]: row["current"] for row in result["new_facts"]}
            self.assertEqual(states, {"active": True, "future-end": True, "ended": False})
            self.assertEqual([row["fact"] for row in result["superseded"]], ["ended"])
        finally:
            await driver.execute_query("MATCH (n:Entity {group_id: $group}) DETACH DELETE n", group=group)
            await driver.close()

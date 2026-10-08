"""Vector quality checks on an explicitly isolated Neo4j 5.26 fixture database.

This suite creates/drops global managed indexes. Never use a production database.
Both NEO4J_INTEGRATION=1 and NEO4J_VECTOR_TEST_ISOLATED=1 are required.
"""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from graphiti_core.driver.neo4j.operations.search_ops import Neo4jSearchOperations
from graphiti_core.search.search_filters import SearchFilters, DateFilter, ComparisonOperator

from vector_indexes import Executor, connection, manage
from vector_eval import MeasuredExecutor, evaluate
from vector_search import NODE_INDEX, EDGE_INDEX, IndexedReadSearch, index_metadata


def vector(x, y, z=0):
    return [x, y, z] + [0.] * 1021


@unittest.skipUnless(
    os.environ.get("NEO4J_INTEGRATION") == "1" and os.environ.get("NEO4J_VECTOR_TEST_ISOLATED") == "1",
    "requires an isolated Neo4j 5.26 database and both integration flags",
)
class VectorNeo4jIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def assert_records_equal(self, left, right):
        self.assertEqual([item.model_dump() for item in left], [item.model_dump() for item in right])

    async def test_index_lifecycle_filters_provenance_and_quality(self):
        driver = connection()
        executor = MeasuredExecutor(driver, os.environ.get("NEO4J_DATABASE", "neo4j"), 60)
        prefix = "vector-test-" + uuid4().hex
        groups = [prefix + "-a", prefix + "-b"]
        acquired = False
        try:
            for spec in (NODE_INDEX, EDGE_INDEX):
                self.assertIsNone(await index_metadata(executor, spec), "Use a fresh isolated database without managed vector indexes")
            acquired = True
            for group, near in zip(groups, (False, True)):
                await executor.execute_query(
                    "CREATE (:Episodic {uuid: $episode, name: 'fixture-note-slug', group_id: $group}) "
                    "CREATE (:Entity:Person {uuid: $sink, name: 'private sink', summary: 'private', "
                    "group_id: $group, created_at: datetime()})", episode=group + "-episode", sink=group + "-sink", group=group,
                )
                rows = [{"uuid": group + f"-{i:02d}", "vector": vector(1 if near else .8, .01 * i if near else .6, .02 * i),
                         "superseded": i % 2 == 1} for i in range(12)]
                await executor.execute_query(
                    "UNWIND $rows AS row MATCH (sink:Entity {uuid: $sink}) "
                    "CREATE (n:Entity:Person {uuid: row.uuid, name: 'private name', summary: 'private summary', "
                    "group_id: $group, created_at: datetime(), name_embedding: row.vector}) "
                    "CREATE (n)-[:RELATES_TO {uuid: row.uuid + '-edge', group_id: $group, "
                    "name: 'KNOWS', fact: 'private fact', fact_embedding: row.vector, "
                    "episodes: [$episode], created_at: datetime(), valid_at: datetime('2020-01-01T00:00:00Z'), "
                    "invalid_at: CASE WHEN row.superseded THEN datetime('2021-01-01T00:00:00Z') ELSE null END, "
                    "expired_at: CASE WHEN row.superseded THEN datetime('2021-01-02T00:00:00Z') ELSE null END}]->(sink)",
                    rows=rows, group=group, sink=group + "-sink", episode=group + "-episode",
                )
            exact = Neo4jSearchOperations()
            indexed = IndexedReadSearch()
            query_vector = vector(1., 0.)
            filters = SearchFilters()
            args = (executor, query_vector, None, None, filters, [groups[0]], 3, .6)
            expected = await exact.edge_similarity_search(*args)
            # Missing indexes preserve results through exact search.
            self.assert_records_equal(expected, await indexed.edge_similarity_search(*args))
            states = await manage(executor, create=True, wait=90)
            self.assertEqual([s["dimensions"] for s in states], [1024, 1024])
            self.assertEqual(states, await manage(executor, create=True, dimensions=1024, wait=90))
            self.assertEqual(states, await manage(executor, dimensions=1024))
            for kind in ("edge", "node"):
                results = [row async for row in evaluate(executor, [query_vector], kind=kind, groups=[groups[0]], limit=3)]
                self.assertEqual(results[0]["recall_at_k"], 1.)
                self.assertTrue(results[0]["same_order"])
                self.assertTrue(results[0]["metadata_equal"])
                self.assertEqual(results[0]["ann_calls"], 1)
                self.assertFalse(results[0]["fallback"])
                self.assertNotIn("private", str(results))
            # Exercise the read-only evaluation CLI against fixture data only.
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).parents[1] / "vector_eval.py"),
                "--kind", "edge", "--group", groups[0], "--samples", "1", "--limit", "3",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 120)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertNotIn(b"private", stdout)
            self.assertEqual(json.loads(stdout)["recall_at_k"], 1.)
            edges = await indexed.edge_similarity_search(*args)
            self.assertTrue(all(e.group_id == groups[0] for e in edges))
            self.assertTrue(any(e.invalid_at is not None for e in edges))
            self.assertTrue(any(e.invalid_at is None for e in edges))
            self.assertTrue(all(e.episodes == [groups[0] + "-episode"] for e in edges))
            for edge in edges:
                self.assertEqual(edge.target_node_uuid, groups[0] + "-sink")
                self.assertIsNotNone(edge.valid_at)
            # Both groups are returned only when both are requested.
            combined = await indexed.edge_similarity_search(executor, query_vector, None, None, filters, groups, 20, .6)
            self.assertEqual({e.group_id for e in combined}, set(groups))
            self.assertEqual(await indexed.edge_similarity_search(executor, query_vector, None, None, filters, [], 3), [])
            # Score filtering uses exact cosine and the upstream strict > predicate.
            threshold_args = (executor, query_vector, None, None, filters, [groups[0]], 3, .95)
            self.assert_records_equal(await indexed.edge_similarity_search(*threshold_args), await exact.edge_similarity_search(*threshold_args))
            current = SearchFilters(invalid_at=[[DateFilter(comparison_operator=ComparisonOperator.is_null)]])
            current_args = (executor, query_vector, None, None, current, [groups[0]], 3, .6)
            self.assert_records_equal(await indexed.edge_similarity_search(*current_args), await exact.edge_similarity_search(*current_args))
            filtered = SearchFilters(edge_uuids=[groups[0] + "-01-edge"], edge_types=["KNOWS"], node_labels=["Person"])
            filtered_args = (executor, query_vector, None, None, filtered, [groups[0]], 3, .6)
            self.assert_records_equal(await indexed.edge_similarity_search(*filtered_args), await exact.edge_similarity_search(*filtered_args))
            source = groups[0] + "-02"
            for group_filter in ([groups[0]], None):
                constrained = await indexed.edge_similarity_search(executor, query_vector, source, groups[0] + "-sink", filters, group_filter)
                self.assertEqual([e.uuid for e in constrained], [source + "-edge"])
                self.assertEqual(await indexed.edge_similarity_search(executor, query_vector, source, groups[1] + "-sink", filters, group_filter), [])
            node_filters = SearchFilters(node_labels=["Person"])
            node_args = (executor, query_vector, node_filters, [groups[0]], 3, .6)
            self.assert_records_equal(await indexed.node_similarity_search(*node_args), await exact.node_similarity_search(*node_args))
            # A selective group excluded from the global top-2 must fall back.
            narrow = IndexedReadSearch(max_candidates=2)
            self.assert_records_equal(await narrow.edge_similarity_search(*args[:6], 1, .6), expected[:1])
            # Simulate POPULATING after validation; actual fallback queries hit Neo4j.
            original = index_metadata
            async def offline(executor, spec):
                row = await original(executor, spec)
                row["state"] = "POPULATING"
                return row
            with patch("vector_search.index_metadata", side_effect=offline):
                self.assert_records_equal(await indexed.edge_similarity_search(*args), expected)
            # Reject an incompatible managed index; never silently replace it.
            await executor.execute_query(f"DROP INDEX {EDGE_INDEX.name}")
            await executor.execute_query(
                f"CREATE VECTOR INDEX {EDGE_INDEX.name} FOR ()-[e:RELATES_TO]-() ON (e.fact_embedding) "
                "OPTIONS {indexConfig: {`vector.dimensions`: 3, `vector.similarity_function`: 'cosine'}}"
            )
            with self.assertRaises(ValueError):
                await manage(executor, create=True, dimensions=1024)
            self.assert_records_equal(await indexed.edge_similarity_search(*args), expected)
        finally:
            if acquired:
                for spec in (NODE_INDEX, EDGE_INDEX):
                    await executor.execute_query(f"DROP INDEX {spec.name} IF EXISTS")
                await executor.execute_query("MATCH (n) WHERE n.group_id IN $groups DETACH DELETE n", groups=groups)
            await driver.close()

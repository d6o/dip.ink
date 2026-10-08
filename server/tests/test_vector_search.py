"""Read-only vector adapter and explicit index-management contracts."""
import inspect
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from graphiti_core.driver.neo4j.operations.search_ops import Neo4jSearchOperations
from graphiti_core.search.search_filters import SearchFilters
from graphiti_core.search import search_utils

from vector_indexes import discover_dimensions, manage
from vector_search import (
    EDGE_INDEX, NODE_INDEX, IndexedReadSearch, IndexedReadSearchInterface, index_problem, install_read_search,
)


def metadata(spec, dimensions=1024, state="ONLINE"):
    return {"name": spec.name, "type": "VECTOR", "entityType": spec.entity_type,
            "labelsOrTypes": [spec.label], "properties": [spec.property], "state": state,
            "options": {"indexConfig": {"vector.dimensions": dimensions,
                                         "vector.similarity_function": "cosine"}}}


def result(rows):
    return rows, None, None


class IndexValidationTests(unittest.TestCase):
    def test_checks_schema_cosine_dimension_and_state(self):
        self.assertEqual(index_problem(None, NODE_INDEX), "missing")
        for spec in (NODE_INDEX, EDGE_INDEX):
            self.assertIsNone(index_problem(metadata(spec), spec, 1024))
            row = metadata(spec)
            row["options"]["indexConfig"]["vector.similarity_function"] = "COSINE"
            self.assertIsNone(index_problem(row, spec, 1024))
            self.assertEqual(index_problem(metadata(spec), spec, 1536), "dimensions")
            self.assertEqual(index_problem(metadata(spec, state="POPULATING"), spec), "offline")
            for field, value in [("type", "RANGE"), ("entityType", "other"),
                                 ("labelsOrTypes", ["Other"]), ("properties", ["other"])]:
                row = metadata(spec); row[field] = value
                self.assertEqual(index_problem(row, spec), "schema")
            row = metadata(spec)
            row["options"]["indexConfig"]["vector.similarity_function"] = "euclidean"
            self.assertEqual(index_problem(row, spec), "similarity")

    def test_install_is_explicit_and_rollback_keeps_legacy(self):
        driver = SimpleNamespace(_search_ops=Neo4jSearchOperations(), search_interface=None)
        with patch.dict(os.environ, {"GRAPH_VECTOR_SEARCH": "0"}):
            install_read_search(driver)
        self.assertIs(type(driver._search_ops), Neo4jSearchOperations)
        self.assertIsNone(driver.search_interface)
        with patch.dict(os.environ, {"GRAPH_VECTOR_SEARCH": "1", "GRAPH_VECTOR_DIMENSIONS": "1024"}):
            install_read_search(driver)
        self.assertIsInstance(driver.search_interface, IndexedReadSearchInterface)
        self.assertIs(driver._search_ops, driver.search_interface.operations)
        self.assertEqual(driver._search_ops.dimensions, 1024)
        self.assertIs(IndexedReadSearch.edge_fulltext_search, Neo4jSearchOperations.edge_fulltext_search)
        self.assertIs(IndexedReadSearch.node_fulltext_search, Neo4jSearchOperations.node_fulltext_search)


class VectorSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_search_utils_routes_through_per_instance_interface(self):
        driver = SimpleNamespace(_search_ops=Neo4jSearchOperations(), search_interface=None)
        with patch.dict(os.environ, {"GRAPH_VECTOR_SEARCH": "1"}):
            install_read_search(driver)
        filters, groups, vector = SearchFilters(), ["fixture"], [1., 0.]
        cases = {
            "node_similarity_search": (vector, filters, groups, 3, .6),
            "edge_similarity_search": (vector, "source", "target", filters, groups, 3, .6),
            "node_fulltext_search": ("fixture", filters, groups, 3),
            "edge_fulltext_search": ("fixture", filters, groups, 3),
            "episode_fulltext_search": ("fixture", filters, groups, 3),
        }
        for name, args in cases.items():
            with self.subTest(method=name):
                method = getattr(driver.search_interface, name)
                inspect.signature(method).bind(driver, *args)
                with patch.object(driver._search_ops, name, AsyncMock(return_value=[])) as called:
                    self.assertEqual(await getattr(search_utils, name)(driver, *args), [])
                    called.assert_awaited_once_with(driver, *args)

    async def test_optional_interface_methods_keep_legacy_fallback_contracts(self):
        from graphiti_core.driver.driver import GraphProvider
        filters, groups = SearchFilters(), ["fixture"]
        cases = {
            "node_bfs_search": (["origin"], filters, 2, groups, 3),
            "edge_bfs_search": (["origin"], 2, filters, groups, 3),
            "community_fulltext_search": ("fixture", groups, 3),
            "community_similarity_search": ([1., 0.], groups, 3, .6),
            "get_embeddings_for_communities": ([],),
            "node_distance_reranker": (["other"], "center", 0),
            "episode_mentions_reranker": ([["other"]], 0),
        }
        for name, args in cases.items():
            with self.subTest(method=name):
                driver = SimpleNamespace(provider=GraphProvider.NEO4J, search_interface=None,
                                         fulltext_syntax="", execute_query=AsyncMock(return_value=result([])))
                upstream = getattr(search_utils, name)
                expected = await upstream(driver, *args)
                legacy_calls = driver.execute_query.call_args_list[:]
                driver.execute_query.reset_mock()
                with patch.dict(os.environ, {"GRAPH_VECTOR_SEARCH": "1"}):
                    install_read_search(driver)
                with self.assertRaises(NotImplementedError):
                    await getattr(driver.search_interface, name)(driver, *args)
                self.assertEqual(await upstream(driver, *args), expected)
                self.assertEqual(driver.execute_query.call_args_list, legacy_calls)

    async def test_ann_queries_and_upstream_projections(self):
        for spec in (NODE_INDEX, EDGE_INDEX):
            executor = SimpleNamespace(execute_query=AsyncMock(side_effect=[result([metadata(spec, 2)]), result([{}])]))
            adapter = IndexedReadSearch(overfetch=30, max_candidates=120)
            parser = "entity_edge_from_record" if spec == EDGE_INDEX else "entity_node_from_record"
            with patch("vector_search." + parser, return_value="parsed"):
                actual = await adapter._similarity(executor, spec, [1., 0.], SearchFilters(), ["private-group"], 1, .7)
            self.assertEqual(actual, ["parsed"])
            query, = executor.execute_query.call_args.args
            params = executor.execute_query.call_args.kwargs
            self.assertIn("db.index.vector.query", query)
            self.assertIn("vector.similarity.cosine", query)
            self.assertIn("score > $min_score", query)
            self.assertEqual(params["group_ids"], ["private-group"])
            self.assertLessEqual(params["candidates"], 120)
            self.assertEqual(params["routing_"], "r")
            if spec == EDGE_INDEX:
                for field in ["episodes", "invalid_at", "valid_at", "expired_at", "source_node_uuid", "target_node_uuid"]:
                    self.assertIn(field, query)
                self.assertNotIn("invalid_at IS NULL", query)

    async def test_missing_offline_wrong_dimension_and_metadata_error_fall_back(self):
        for row in (None, metadata(NODE_INDEX, 2, "POPULATING"), metadata(NODE_INDEX, 3)):
            executor = SimpleNamespace(execute_query=AsyncMock(side_effect=[result([row] if row else []), result([])]))
            await IndexedReadSearch().node_similarity_search(executor, [1., 0.], SearchFilters())
            self.assertTrue(executor.execute_query.call_args.args[0].startswith("MATCH"))
        executor = SimpleNamespace(execute_query=AsyncMock(side_effect=[RuntimeError("private error"), result([])]))
        await IndexedReadSearch().node_similarity_search(executor, [1., 0.], SearchFilters())
        self.assertEqual(executor.execute_query.await_count, 2)

    async def test_ann_error_and_underfill_fall_back_once(self):
        for ann in (result([]), RuntimeError("offline after metadata check")):
            executor = SimpleNamespace(execute_query=AsyncMock(side_effect=[result([metadata(NODE_INDEX, 2)]), ann, result([])]))
            await IndexedReadSearch().node_similarity_search(executor, [1., 0.], SearchFilters(), ["small-group"])
            self.assertEqual(executor.execute_query.await_count, 3)
            self.assertTrue(executor.execute_query.call_args.args[0].startswith("MATCH"))

    async def test_explicit_filters_and_endpoints_use_exact_without_ann(self):
        executor = SimpleNamespace(execute_query=AsyncMock(return_value=result([])))
        await IndexedReadSearch().edge_similarity_search(
            executor, [1., 0.], "source", "target", SearchFilters(edge_types=["KNOWS"]), None,
        )
        self.assertEqual(executor.execute_query.await_count, 1)
        query = executor.execute_query.call_args.args[0]
        for clause in ["e.name in $edge_types", "n.uuid = $source_uuid", "m.uuid = $target_uuid"]:
            self.assertIn(clause, query)
        self.assertNotIn("db.index", query)

    async def test_empty_group_is_empty_without_io(self):
        executor = SimpleNamespace(execute_query=AsyncMock())
        self.assertEqual(await IndexedReadSearch().node_similarity_search(executor, [1., 0.], SearchFilters(), []), [])
        executor.execute_query.assert_not_called()

    async def test_discovery_rejects_empty_and_mixed_dimensions(self):
        for rows in ([], [{"dimensions": 2}, {"dimensions": 3}]):
            executor = SimpleNamespace(execute_query=AsyncMock(return_value=result(rows)))
            with self.assertRaises(ValueError):
                await discover_dimensions(executor, NODE_INDEX)
        executor = SimpleNamespace(execute_query=AsyncMock(return_value=result([{"dimensions": 1024}])))
        self.assertEqual(await discover_dimensions(executor, NODE_INDEX), 1024)

    async def test_incompatible_schema_prevents_all_ddl(self):
        executor = SimpleNamespace(execute_query=AsyncMock(return_value=result([metadata(NODE_INDEX, 2)])))
        with self.assertRaises(ValueError):
            await manage(executor, create=True, dimensions=1024)
        self.assertTrue(all(call.args[0].startswith("SHOW") for call in executor.execute_query.call_args_list))

    async def test_offline_index_check_is_read_only(self):
        executor = SimpleNamespace(execute_query=AsyncMock(side_effect=[
            result([metadata(NODE_INDEX, 2)]), result([metadata(EDGE_INDEX, 2)]),
            result([metadata(NODE_INDEX, 2, "POPULATING")]), result([metadata(EDGE_INDEX, 2)]),
        ]))
        with self.assertRaises(ValueError):
            await manage(executor, dimensions=2)
        self.assertTrue(all(call.args[0].startswith("SHOW") for call in executor.execute_query.call_args_list))

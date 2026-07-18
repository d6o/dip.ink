from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")

import ingest  # noqa: E402


class _FakeAsyncDriver:
    async def close(self) -> None:
        return None


class DriverConstructionTests(unittest.TestCase):
    def test_configured_driver_uses_bounded_pool_without_scheduling_schema_task(self):
        calls: list[dict] = []

        def make_driver(**kwargs):
            calls.append(kwargs)
            return _FakeAsyncDriver()

        async def run() -> None:
            loop = asyncio.get_running_loop()
            seen_contexts: list[dict] = []
            previous = loop.get_exception_handler()
            loop.set_exception_handler(lambda _loop, context: seen_contexts.append(context))
            try:
                with mock.patch("neo4j.AsyncGraphDatabase.driver", side_effect=make_driver), \
                     mock.patch.object(
                         ingest.Neo4jDriver,
                         "build_indices_and_constraints",
                         new=mock.AsyncMock(side_effect=AssertionError("constructor scheduled schema setup")),
                     ):
                    driver = ingest.DipInkNeo4jDriver(
                        "bolt://example:7687",
                        "neo4j",
                        "password",
                        max_connection_pool_size=40,
                        connection_acquisition_timeout=30,
                    )
                    await asyncio.sleep(0)
                    await driver.close()
            finally:
                loop.set_exception_handler(previous)
            self.assertEqual(seen_contexts, [])

        asyncio.run(run())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_connection_pool_size"], 40)
        self.assertEqual(calls[0]["connection_acquisition_timeout"], 30)

    def test_graphiti_receives_explicit_driver_at_construction(self):
        captured: dict = {}

        def fake_graphiti(**kwargs):
            captured.update(kwargs)
            return object()

        with mock.patch.object(ingest, "DipInkNeo4jDriver", return_value="configured-driver") as driver_cls, \
             mock.patch.object(ingest, "Graphiti", side_effect=fake_graphiti), \
             mock.patch("graphiti_core.llm_client.OpenAIClient", return_value="llm"):
            result = ingest.build_graphiti()

        self.assertIsNotNone(result)
        self.assertEqual(captured["graph_driver"], "configured-driver")
        self.assertNotIn("uri", captured)
        self.assertNotIn("user", captured)
        self.assertNotIn("password", captured)
        driver_cls.assert_called_once()

    def test_group_builder_does_not_clone_or_treat_group_as_database(self):
        sentinel = object()
        with mock.patch.object(ingest, "build_graphiti", return_value=sentinel) as build:
            self.assertIs(ingest.build_graphiti_on_group("tenant-a"), sentinel)
        build.assert_called_once_with()


class _RowsDriver:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    async def execute_query(self, query: str, **params):
        self.calls.append((query, params))
        return self.rows, None, None


class GroupScopeTests(unittest.TestCase):
    def test_ingest_state_queries_are_group_scoped(self):
        async def run() -> None:
            driver = _RowsDriver([{"done": []}])
            await ingest._get_done_slugs(driver, "group-a")
            query, params = driver.calls[-1]
            self.assertIn("group_id: $group_id", query)
            self.assertEqual(params["group_id"], "group-a")

            driver.rows = [{"partials": []}]
            await ingest._get_partial_slugs(driver, "group-a")
            query, params = driver.calls[-1]
            self.assertIn("group_id: $group_id", query)
            self.assertEqual(params["group_id"], "group-a")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

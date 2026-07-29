from __future__ import annotations

import asyncio
import os
import unittest

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_EMBED_PROVIDER", "openai")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
os.environ.setdefault("WIKI_ROOT", "/tmp/wiki-mcp-test-community-root")

import ingest  # noqa: E402

from graphiti_core.utils.maintenance import community_operations as co  # noqa: E402


class PoolExhausted(RuntimeError):
    """Stands in for neo4j.exceptions.ConnectionAcquisitionTimeoutError."""


class FakePooledDriver:
    """Driver with a hard connection-pool ceiling.

    Mirrors the production failure: each in-flight query holds one connection,
    and acquiring beyond the ceiling raises instead of queueing.
    """

    def __init__(self, entity_uuids: list[str], pool_size: int) -> None:
        self._entity_uuids = entity_uuids
        self._pool_size = pool_size
        self.in_flight = 0
        self.peak_in_flight = 0

    async def execute_query(self, query: str, **params):
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self.in_flight > self._pool_size:
                raise PoolExhausted("failed to obtain a connection from the pool within 30s")
            await asyncio.sleep(0)  # force interleaving so fan-out is observable
            if "RELATES_TO" in query:
                return [{"edges": []}], None, None
            return [{"uuids": list(self._entity_uuids)}], None, None
        finally:
            self.in_flight -= 1


class CommunityClusterHydrationTest(unittest.TestCase):
    """Cluster hydration must stay within the Neo4j connection pool.

    Every edgeless entity becomes its own singleton cluster, so a graph with
    thousands of sparse entities fans out to thousands of hydration queries. An
    unbounded gather starves the pool; build_communities() has already called
    remove_communities() by then, so the graph is left with ZERO communities.
    """

    def setUp(self) -> None:
        ingest.patch_community_clustering()
        self._orig_get_by_uuids = co.EntityNode.get_by_uuids
        self._orig_hydrate_conc = ingest.HYDRATE_CONC

        async def fake_get_by_uuids(driver, uuids):
            await driver.execute_query("MATCH (n:Entity) RETURN n", uuids=uuids)
            return list(uuids)

        co.EntityNode.get_by_uuids = staticmethod(fake_get_by_uuids)

    def tearDown(self) -> None:
        co.EntityNode.get_by_uuids = self._orig_get_by_uuids
        ingest.HYDRATE_CONC = self._orig_hydrate_conc

    def test_hydration_stays_within_pool_and_returns_all_clusters(self):
        pool_size = 40
        singleton_count = 437  # the live graph's edgeless-entity count
        ingest.HYDRATE_CONC = max(1, pool_size // 2)
        driver = FakePooledDriver(
            [f"uuid-{i}" for i in range(singleton_count)], pool_size=pool_size
        )

        clusters = asyncio.run(co.get_community_clusters(driver, ["mykg"]))

        self.assertEqual(len(clusters), singleton_count)
        self.assertLessEqual(
            driver.peak_in_flight,
            pool_size,
            f"hydration fan-out {driver.peak_in_flight} exceeded pool {pool_size}",
        )

    def test_unbounded_fanout_would_exhaust_the_pool(self):
        """Guards the regression: without the cap this starves the pool."""
        pool_size = 40
        driver = FakePooledDriver(
            [f"uuid-{i}" for i in range(437)], pool_size=pool_size
        )

        async def unbounded():
            uuids = [f"uuid-{i}" for i in range(437)]
            return await asyncio.gather(
                *[co.EntityNode.get_by_uuids(driver, [u]) for u in uuids]
            )

        with self.assertRaises(PoolExhausted):
            asyncio.run(unbounded())

    def test_hydration_concurrency_defaults_below_pool_size(self):
        self.assertLess(ingest.HYDRATE_CONC, ingest.NEO4J_MAX_POOL)
        self.assertGreaterEqual(ingest.HYDRATE_CONC, 1)


class DegenerateFallbackTest(unittest.TestCase):
    """Phase 1 must not carry a timeout when the fallback is identical to it.

    The live CronJob sets MAX_CLUSTER == BOUNDED_MAX_CLUSTER and
    COMMUNITY_CONC == BOUNDED_CONC. Timing out then restarts the same work with
    no timeout, so the job spends BUILD_TIMEOUT and still needs a full build,
    and activeDeadlineSeconds kills it before communities are ever persisted.
    """

    @staticmethod
    def _is_degenerate(start_max, bounded_max, start_conc, bounded_conc):
        return start_max == bounded_max and start_conc == bounded_conc

    def test_identical_phases_are_degenerate(self):
        self.assertTrue(self._is_degenerate(200, 200, 4, 4))

    def test_distinct_phases_keep_the_timeout(self):
        self.assertFalse(self._is_degenerate(None, 200, 4, 4))  # unbounded -> bounded
        self.assertFalse(self._is_degenerate(500, 200, 4, 4))   # looser cap
        self.assertFalse(self._is_degenerate(200, 200, 8, 4))   # higher concurrency


if __name__ == "__main__":
    unittest.main()

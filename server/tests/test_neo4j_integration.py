"""Real Neo4j lifecycle regression test.

Run explicitly against neo4j:5.26.2, for example:

    docker run -d --rm --name dipink-neo4j-test \
      -e NEO4J_AUTH=neo4j/test-password -p 17687:7687 neo4j:5.26.2
    NEO4J_INTEGRATION=1 NEO4J_URI=bolt://127.0.0.1:17687 \
      NEO4J_PASSWORD=test-password python -m unittest \
      tests.test_neo4j_integration

The normal image-build unit suite skips this test.
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import io
import logging
import os
import tempfile
import unittest
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_EMBED_PROVIDER", "openai")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
os.environ.setdefault("GROUP_ID", "lane-a-integration")
os.environ.setdefault("NOTES_ROOT", "/tmp/dipink-integration-notes")
os.environ.setdefault("INBOX_ROOTS", "")

import graph  # noqa: E402
import ingest  # noqa: E402
from loops import memory_alerts, memory_healthcheck  # noqa: E402


@unittest.skipUnless(
    os.environ.get("NEO4J_INTEGRATION", "").lower() in {"1", "true", "yes"},
    "set NEO4J_INTEGRATION=1 and point NEO4J_URI at neo4j:5.26.2",
)
class Neo4jLifecycleIntegrationTests(unittest.TestCase):
    def test_clients_jobs_and_status_close_without_background_task_failures(self):
        async def run() -> None:
            loop = asyncio.get_running_loop()
            unhandled: list[dict] = []
            previous_handler = loop.get_exception_handler()
            loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
            log_stream = io.StringIO()
            handler = logging.StreamHandler(log_stream)
            logging.getLogger().addHandler(handler)
            group_id = ingest.DEFAULT_GROUP_ID

            try:
                setup = ingest.build_graphiti_on_group(group_id)
                await setup.driver.health_check()
                await setup.build_indices_and_constraints()
                await setup.driver.execute_query(
                    "MATCH (n) WHERE n.group_id = $group_id DETACH DELETE n",
                    group_id=group_id,
                )
                now = datetime.now(timezone.utc)
                slug = now.strftime("%Y-%m-%d-%H%M%S") + "-lifecycle-fixture"
                await setup.driver.execute_query(
                    "CREATE (e:Episodic {uuid: randomUUID(), name: $slug, group_id: $group_id, "
                    "content: 'fixture', source: 'text', source_description: 'test', "
                    "entity_edges: [], created_at: datetime(), valid_at: datetime()}) "
                    "CREATE (n:Entity {uuid: randomUUID(), name: 'fixture', group_id: $group_id, "
                    "summary: 'fixture', created_at: datetime()}) "
                    "CREATE (e)-[:MENTIONS {uuid: randomUUID(), group_id: $group_id, "
                    "created_at: datetime()}]->(n) "
                    "CREATE (:Community {uuid: randomUUID(), name: 'fixture community', "
                    "group_id: $group_id, summary: 'fixture', created_at: datetime()})",
                    slug=slug,
                    group_id=group_id,
                )
                await setup.close()

                # Server warm/use/close uses the same explicit-driver path.
                await graph.close()
                await graph.warm()
                self.assertIsNotNone(graph._g)
                await graph._g.driver.health_check()
                await graph.close()
                self.assertIsNone(graph._g)

                # Both short-lived loop jobs exercise real clients and close them.
                memory_alerts.failures.clear()
                await memory_alerts.check_graph()
                self.assertEqual(memory_alerts.failures, [])

                memory_healthcheck.failures.clear()
                memory_healthcheck.NOW = now
                await memory_healthcheck.check_ingestion(
                    [(slug, now - timedelta(hours=3))], [], []
                )
                self.assertEqual(memory_healthcheck.failures, [])

                # One real ingest status call, including discovery and scoped reads.
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    folder = root / slug
                    folder.mkdir()
                    (folder / f"{slug}.md").write_text("fixture", encoding="utf-8")
                    old_root, old_inbox = ingest.NOTES_ROOT, ingest.INBOX_ROOTS
                    ingest.NOTES_ROOT, ingest.INBOX_ROOTS = root, []
                    try:
                        with contextlib.redirect_stdout(io.StringIO()):
                            await ingest.status()
                    finally:
                        ingest.NOTES_ROOT, ingest.INBOX_ROOTS = old_root, old_inbox

                cleanup = ingest.build_graphiti_on_group(group_id)
                await cleanup.driver.execute_query(
                    "MATCH (n) WHERE n.group_id = $group_id DETACH DELETE n",
                    group_id=group_id,
                )
                await cleanup.close()

                await asyncio.sleep(0.1)
                gc.collect()
                await asyncio.sleep(0)
            finally:
                logging.getLogger().removeHandler(handler)
                loop.set_exception_handler(previous_handler)

            messages = "\n".join(
                str(context.get("message", "")) + " " + repr(context.get("exception"))
                for context in unhandled
            )
            logs = log_stream.getvalue()
            forbidden = (
                "Task exception was never retrieved",
                "IncompleteCommit",
                "defunct connection",
                "unclosed driver",
            )
            for marker in forbidden:
                self.assertNotIn(marker.lower(), (messages + "\n" + logs).lower())
            self.assertEqual(unhandled, [])

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            asyncio.run(run())
        warning_text = "\n".join(str(w.message) for w in caught)
        self.assertNotIn("unclosed", warning_text.lower())
        self.assertNotIn("leaked", warning_text.lower())


if __name__ == "__main__":
    unittest.main()

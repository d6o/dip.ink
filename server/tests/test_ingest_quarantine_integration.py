"""Durable retry state on the disposable Neo4j CI service only."""
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
import ingest
import ingest_failures


@unittest.skipUnless(os.environ.get("NEO4J_INTEGRATION") == "1", "requires isolated Neo4j integration service")
class QuarantineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_count_read_only_status_and_retry_reset(self):
        group = "quarantine-fixture-" + uuid4().hex
        g = ingest.build_graphiti_on_group(group)
        try:
            await ingest_failures.ensure_index(g.driver)
            await ingest_failures.ensure_index(g.driver)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "fixture.md"
                path.write_text("synthetic note")
                note = (datetime.now(timezone.utc), "fixture", path)
                digest = ingest.note_content_hash(path)
                for expected in range(1, 4):
                    self.assertEqual(await ingest_failures.record(g.driver, group, "fixture", digest, "json_invalid"), expected)
                assessment = await ingest.assess_ingest(g.driver, [note], group_id=group, upgrade_legacy=False)
                self.assertEqual(assessment.as_dict()["quarantined"], 1)
                self.assertEqual(assessment.as_dict()["done"], 0)
                self.assertEqual(assessment.as_dict()["pending"], 1)
                self.assertEqual(await ingest_failures.states(g.driver, group + "-other"), {})
                # Read-only status does not reset a durable failure budget.
                self.assertEqual((await ingest_failures.states(g.driver, group))["fixture"]["failure_count"], 3)
                self.assertEqual(await ingest_failures.retry(g.driver, group, "fixture"), 1)
                self.assertEqual((await ingest.assess_ingest(g.driver, [note], group_id=group)).retryable, {"fixture"})
                await ingest_failures.record(g.driver, group, "fixture", "changed-hash", "schema")
                self.assertEqual((await ingest_failures.states(g.driver, group))["fixture"]["failure_count"], 1)
                await ingest_failures.clear(g.driver, group, "fixture")
                self.assertEqual(await ingest_failures.states(g.driver, group), {})
        finally:
            await ingest_failures.clear(g.driver, group, "fixture")
            await g.close()

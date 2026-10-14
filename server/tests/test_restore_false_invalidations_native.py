"""Opt-in native lock and rollback checks with isolated temporary fixtures.

No production edge UUID is used. The caller must select this test explicitly.
The fixture nodes have a separate group ID and are removed after the checks.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import time
import unittest
from unittest import mock
from uuid import uuid4

from loops import restore_false_invalidations as repair
from loops.notify import Neo4jStateStore


@unittest.skipUnless(os.environ.get("RUN_PROJECTION_REPAIR_NATIVE") == "1", "explicit native fixture test required")
class NativeRestorationTests(unittest.TestCase):
    def test_native_lock_fresh_state_and_atomic_rollback(self):
        store = Neo4jStateStore()
        driver = store._driver
        prefix = "dipink-repair-fixture-" + str(uuid4())
        ids = [prefix + "-0", prefix + "-1"]
        fixture_fact = "Synthetic native repair fixture."
        expected = {i: (fixture_fact, expired) for i, (_, expired) in zip(ids, repair.FACTS.values())}
        try:
            with driver.session(default_access_mode="WRITE") as s:
                s.run("""
UNWIND $items AS item
CREATE (a:Entity:DipinkRepairFixture {uuid:item.uuid+'-a', group_id:$group})
CREATE (b:Entity:DipinkRepairFixture {uuid:item.uuid+'-b', group_id:$group})
CREATE (a)-[r:RELATES_TO {
 uuid:item.uuid, group_id:$group, fact:$fact, episodes:[$ep],
 valid_at:datetime('2026-10-13T10:51:14Z'),
 invalid_at:datetime('2026-10-14T00:28:59Z[UTC]'), expired_at:datetime(item.expired)
}]->(b)
""", items=[{"uuid": i, "expired": e} for i, (_, e) in expected.items()],
                      group=prefix, fact=fixture_fact, ep=prefix).consume()

            def snapshot():
                with driver.session(default_access_mode="READ") as s:
                    return sorted([dict(r) for r in s.run(repair.READ, ids=ids)], key=lambda r: r["uuid"])

            original = snapshot()
            with mock.patch.dict(repair.FACTS, expected, clear=True), mock.patch.object(repair, "SOURCE_EPISODE", prefix):
                # A writer holds the relationship lock. The reader must wait
                # and observe the committed property after its lock returns.
                with driver.session(default_access_mode="WRITE") as writer:
                    tx = writer.begin_transaction()
                    tx.run("MATCH ()-[r:RELATES_TO {uuid:$id}]->() SET r.concurrent=true", id=ids[0]).consume()
                    def locked_read():
                        with driver.session(default_access_mode="WRITE") as reader:
                            return reader.execute_write(lambda t: dict(t.run(repair.LOCK, uuid=ids[0]).single()))
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(locked_read)
                        try:
                            blocked = False
                            until = time.monotonic() + 10
                            while time.monotonic() < until:
                                with driver.session(default_access_mode="READ") as observer:
                                    blocked = any("Blocked" in str(r["status"]) for r in observer.run(
                                        "SHOW TRANSACTIONS YIELD currentQuery, status "
                                        "WHERE currentQuery = $lock_query RETURN status", lock_query=repair.LOCK))
                                if blocked:
                                    break
                                time.sleep(.05)
                            self.assertTrue(blocked, "Native lock wait was not observed")
                            self.assertFalse(future.done())
                            tx.commit()
                            locked = future.result(timeout=15)
                        finally:
                            try:
                                tx.rollback()
                            except Exception:
                                pass
                        self.assertTrue(locked["properties"]["concurrent"])
                        self.assertNotEqual(locked, next(r for r in original if r["uuid"] == ids[0]))

                with driver.session(default_access_mode="WRITE") as s:
                    with self.assertRaises(RuntimeError):
                        s.execute_write(lambda t: repair.apply(t, original, datetime.now(timezone.utc)))
                self.assertTrue(all(r["properties"]["invalid_at"] is not None for r in snapshot()))
                with driver.session(default_access_mode="WRITE") as s:
                    s.run("MATCH ()-[r:RELATES_TO {uuid:$id}]->() REMOVE r.concurrent", id=ids[0]).consume()
                original = snapshot()

                with driver.session(default_access_mode="WRITE") as s:
                    tx = s.begin_transaction()
                    try:
                        corrected = repair.apply(tx, original, datetime.now(timezone.utc))
                        self.assertEqual(set(corrected), set(ids))
                        changed = list(tx.run(repair.READ, ids=ids))
                        self.assertTrue(all(r["properties"].get("invalid_at") is None for r in changed))
                    finally:
                        tx.rollback()
                self.assertEqual(snapshot(), original)

                # Force the second final predicate to miss after the first
                # write. Native transaction rollback must retain both edges.
                class SecondMiss:
                    def __init__(self, tx):
                        self.tx = tx
                        self.writes = 0
                    def run(self, query, **params):
                        if query == repair.APPLY:
                            self.writes += 1
                            if self.writes == 2:
                                params["source_uuid"] = "not-the-snapshot-endpoint"
                        return self.tx.run(query, **params)
                with driver.session(default_access_mode="WRITE") as s:
                    with self.assertRaises(RuntimeError):
                        s.execute_write(lambda t: repair.apply(SecondMiss(t), original, datetime.now(timezone.utc)))
                self.assertEqual(snapshot(), original)
                with driver.session(default_access_mode="READ") as s:
                    count = s.run("MATCH (c:DipinkProjectionCorrection) WHERE c.edge_uuid IN $ids RETURN count(c) AS n", ids=ids).single()["n"]
                self.assertEqual(count, 0)
        finally:
            with driver.session(default_access_mode="WRITE") as s:
                s.run("MATCH (n:DipinkRepairFixture {group_id:$group}) DETACH DELETE n", group=prefix).consume()
                s.run("MATCH (c:DipinkProjectionCorrection) WHERE c.edge_uuid IN $ids DELETE c", ids=ids).consume()
            store.close()

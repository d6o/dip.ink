#!/usr/bin/env python3
"""Restore two source-supported constraints after a cross-service invalidation.

This is a bounded data correction, not a generic graph repair command.
It does not touch archives, wiki pages, inputs, models, jobs, or native guards.
The default mode is read-only. Apply requires an exact prior-state match.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SOURCE_EPISODE = "25530abb-5848-4c1a-9114-decb89258911"
SOURCE_NAMES = [
    "2026-10-13-105217-retired-football-two-claim-bindings",
    "2026-10-14-003457-retained-toc-work-reserve-failure",
]
FACTS = {
    "26db6e96-3e75-41c0-8a19-4ef6f5b247b0": (
        "K3s#23 still retains dormant Supabase shared data, and offline Supabase selective processing remains unimplemented; metadata does not establish safe mounting, attachment or process clearance, file identity, archive contents, exclusive old ownership, or preservation proof.",
        "2026-10-14T00:46:02.731226+00:00",
    ),
    "4c623a79-bd22-45e5-b1c6-eeb953ead85e": (
        "K3s#23 still retains the full pre-delete Listmonk archives; exact archive access remains unimplemented, zero copies have been removed, and zero producers changed.",
        "2026-10-14T00:46:02.731229+00:00",
    ),
}
# The server signature preserves native value types and datetime zones. Bolt
# can round-trip UTC named zones as offsets, so parameter-map equality fails.
SIGNATURE = "apoc.convert.toJson([k IN apoc.coll.sort(keys(r)) | {key:k, type:valueType(r[k]), value:r[k], zone:CASE WHEN valueType(r[k]) STARTS WITH 'ZONED DATETIME' THEN (r[k]).timezone ELSE null END}])"
READ = f"""
MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
WHERE r.uuid IN $ids
RETURN r.uuid AS uuid, a.uuid AS source_uuid, b.uuid AS target_uuid,
       properties(r) AS properties, {SIGNATURE} AS signature
"""
LOCK = f"""
MATCH (a:Entity)-[r:RELATES_TO {{uuid:$uuid}}]->(b:Entity)
WITH a, b, r
CALL apoc.lock.rels([r])
CALL apoc.lock.nodes([a, b])
RETURN r.uuid AS uuid, a.uuid AS source_uuid, b.uuid AS target_uuid,
       properties(r) AS properties, {SIGNATURE} AS signature
"""
APPLY = f"""
MATCH (a:Entity)-[r:RELATES_TO {{uuid:$uuid}}]->(b:Entity)
WHERE a.uuid = $source_uuid AND b.uuid = $target_uuid
  AND {SIGNATURE} = $signature
CREATE (c:DipinkProjectionCorrection {{
    key:$key, edge_uuid:$uuid, source_uuid:a.uuid, target_uuid:b.uuid,
    corrected_at:$at, reason:$reason, source_names:$source_names,
    prior_state_json:$prior_state_json
}})
SET r.invalid_at = null, r.expired_at = null
RETURN r.uuid AS uuid
"""
REASON = (
    "The later Umami diagnostic preserves zero removals and separate blocked "
    "Listmonk/offline Supabase access. It does not contradict these constraints."
)


def iso(value):
    if value is None:
        return None
    if hasattr(value, "to_native"):
        value = value.to_native()
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def validate(rows):
    if len(rows) != len(FACTS) or {r["uuid"] for r in rows} != set(FACTS):
        raise RuntimeError("Expected both exact constraint edges")
    for row in rows:
        p = row["properties"]
        fact, expired = FACTS[row["uuid"]]
        if not (
            p.get("fact") == fact
            and p.get("episodes") == [SOURCE_EPISODE]
            and iso(p.get("valid_at")) == "2026-10-13T10:51:14+00:00"
            and iso(p.get("invalid_at")) == "2026-10-14T00:28:59+00:00"
            and iso(p.get("expired_at")) == expired
        ):
            raise RuntimeError("Constraint edge state changed; no correction selected")


def backup(rows):
    folder = Path(tempfile.mkdtemp(prefix="dipink-false-invalidation-"))
    path = folder / "prior-state.json"
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump({"sources": SOURCE_NAMES, "reason": REASON, "rows": rows}, stream, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    folder_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(folder_fd)
    finally:
        os.close(folder_fd)
    return path


def apply(tx, rows, at):
    validate(rows)
    # Lock endpoints and relationships before a complete state comparison.
    # Keep all locks until the transaction commits or rolls back.
    ordered = sorted(rows, key=lambda row: row["uuid"])
    for row in ordered:
        locked = tx.run(LOCK, uuid=row["uuid"]).single()
        if locked is None or dict(locked) != row:
            raise RuntimeError("Complete locked edge state changed; no correction selected")
    corrected = []
    for row in ordered:
        p = row["properties"]
        result = tx.run(
            APPLY, uuid=row["uuid"], source_uuid=row["source_uuid"],
            target_uuid=row["target_uuid"], signature=row["signature"],
            key="dipink4-restore-" + row["uuid"], at=at, reason=REASON,
            source_names=SOURCE_NAMES,
            prior_state_json=json.dumps(row, default=str),
        ).single()
        if result is None:
            # The transaction rolls back both changes on a concurrent update.
            raise RuntimeError("Compare-and-set failed; correction transaction must roll back")
        corrected.append(result["uuid"])
    return corrected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from loops.notify import Neo4jStateStore
    store = Neo4jStateStore()
    try:
        with store._driver.session(default_access_mode="READ") as session:
            rows = session.execute_read(lambda tx: [dict(r) for r in tx.run(READ, ids=list(FACTS))])
        validate(rows)
        path = backup(rows)
        print(json.dumps({"matched": len(rows), "backup": str(path), "apply": args.apply}))
        if not args.apply:
            return
        at = datetime.now(timezone.utc)
        with store._driver.session(default_access_mode="WRITE") as session:
            corrected = session.execute_write(lambda tx: apply(tx, rows, at))
        print(json.dumps({"corrected_edges": corrected, "audit_label": "DipinkProjectionCorrection"}))
    finally:
        store.close()


if __name__ == "__main__":
    main()

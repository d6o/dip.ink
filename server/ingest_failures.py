"""Durable, group-scoped ingest failure state. Never stores source/model bodies.

Quarantine pauses unchanged input; it does not mark an episode complete or move
source files. Changed content is eligible automatically. Explicit retry resets
its counter but retains the incomplete-attempt marker until successful ingest.
"""
from __future__ import annotations

import hashlib
import os


def failure_limit() -> int:
    value = int(os.environ.get("INGEST_MAX_FAILURES", "3"))
    if not 1 <= value <= 20:
        raise ValueError("INGEST_MAX_FAILURES must be between 1 and 20")
    return value


async def ensure_index(driver):
    await driver.execute_query(
        "CREATE CONSTRAINT dipink_ingest_failure_key IF NOT EXISTS "
        "FOR (f:DipinkIngestFailure) REQUIRE f.key IS UNIQUE"
    )


async def states(driver, group_id):
    rows, _, _ = await driver.execute_query(
        "MATCH (f:DipinkIngestFailure {group_id: $group_id}) "
        "RETURN f.slug AS slug, f.content_hash AS content_hash, f.failure_count AS failure_count, "
        "f.error_kind AS error_kind, toString(f.failed_at) AS failed_at",
        group_id=group_id, routing_="r",
    )
    return {row["slug"]: dict(row) for row in rows}


async def record(driver, group_id, slug, content_hash, error_kind):
    key = hashlib.sha256((group_id + "\0" + slug).encode()).hexdigest()
    rows, _, _ = await driver.execute_query(
        "MERGE (f:DipinkIngestFailure {key: $key}) "
        "SET f.failure_count = CASE WHEN f.content_hash = $content_hash "
        "THEN coalesce(f.failure_count, 0) + 1 ELSE 1 END, "
        "f.group_id = $group_id, f.slug = $slug, f.content_hash = $content_hash, "
        "f.error_kind = $error_kind, f.failed_at = datetime() "
        "RETURN f.failure_count AS failure_count",
        key=key, group_id=group_id, slug=slug, content_hash=content_hash, error_kind=error_kind,
    )
    return int(rows[0]["failure_count"])


async def clear(driver, group_id, slug):
    await driver.execute_query(
        "MATCH (f:DipinkIngestFailure {group_id: $group_id, slug: $slug}) DELETE f",
        group_id=group_id, slug=slug,
    )


async def retry(driver, group_id, slug):
    rows, _, _ = await driver.execute_query(
        "MATCH (f:DipinkIngestFailure {group_id: $group_id, slug: $slug}) "
        "SET f.failure_count = 0, f.retry_requested_at = datetime() RETURN count(f) AS reset",
        group_id=group_id, slug=slug,
    )
    return int(rows[0]["reset"])

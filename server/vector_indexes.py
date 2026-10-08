"""Explicit Neo4j 5.26 vector index administration. No request-time DDL.

python vector_indexes.py create [--dimensions 1024] [--wait 120]
python vector_indexes.py check [--dimensions 1024]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time

from neo4j import AsyncGraphDatabase

from vector_search import INDEXES, index_metadata, index_problem


class IndexConfigurationError(ValueError):
    """Bounded index diagnostic that is safe for command output."""


class Executor:
    """Small executor for maintenance/evaluation without LLM clients."""
    def __init__(self, driver, database):
        self.driver, self.database = driver, database

    async def execute_query(self, query, **params):
        return await self.driver.execute_query(query, database_=self.database, **params)


def connection():
    return AsyncGraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
        max_connection_pool_size=2,
    )


async def discover_dimensions(executor, spec):
    pattern = "(x:Entity)" if spec.entity_type == "NODE" else "()-[x:RELATES_TO]->()"
    rows, _, _ = await executor.execute_query(
        f"MATCH {pattern} WHERE x.{spec.property} IS NOT NULL "
        f"RETURN DISTINCT size(x.{spec.property}) AS dimensions LIMIT 2", routing_="r",
    )
    sizes = [row["dimensions"] for row in rows]
    if len(sizes) != 1 or not isinstance(sizes[0], int) or not 1 <= sizes[0] <= 4096:
        raise IndexConfigurationError(f"{spec.name}: empty or mixed embedding dimensions; supply or repair explicitly")
    return sizes[0]


async def manage(executor, *, create=False, dimensions=None, wait=0):
    if dimensions is not None and not 1 <= dimensions <= 4096:
        raise IndexConfigurationError("Vector dimensions must be between 1 and 4096")
    if not 0 <= wait <= 3600:
        raise IndexConfigurationError("Wait must be between 0 and 3600 seconds")
    # Validate both definitions before any write. An incompatible index is never replaced.
    plan = []
    for spec in INDEXES:
        row = await index_metadata(executor, spec)
        size = dimensions if dimensions is not None else await discover_dimensions(executor, spec)
        problem = index_problem(row, spec, size, require_online=False)
        if problem and not (create and problem == "missing"):
            raise IndexConfigurationError(f"{spec.name}: {problem}")
        plan.append((spec, size))
    if create:
        for spec, size in plan:
            pattern = "(x:Entity)" if spec.entity_type == "NODE" else "()-[x:RELATES_TO]-()"
            await executor.execute_query(
                f"CREATE VECTOR INDEX {spec.name} IF NOT EXISTS FOR {pattern} ON (x.{spec.property}) "
                "OPTIONS {indexConfig: {`vector.dimensions`: $dimensions, "
                "`vector.similarity_function`: 'cosine'}}", dimensions=size,
            )
    deadline = time.monotonic() + wait
    while True:
        states = []
        for spec, size in plan:
            row = await index_metadata(executor, spec)
            problem = index_problem(row, spec, size)
            if problem not in (None, "offline"):
                raise IndexConfigurationError(f"{spec.name}: {problem}")
            if row.get("state") == "FAILED":
                raise IndexConfigurationError(f"{spec.name}: failed")
            states.append({"name": spec.name, "dimensions": size, "state": row["state"]})
        if all(state["state"] == "ONLINE" for state in states):
            return states
        if time.monotonic() >= deadline:
            raise IndexConfigurationError("Vector indexes are not ONLINE; run check after population")
        await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))


async def main(args):
    async with connection() as driver:
        executor = Executor(driver, os.environ.get("NEO4J_DATABASE", "neo4j"))
        print(json.dumps(await manage(executor, create=args.command == "create",
                                      dimensions=args.dimensions, wait=args.wait)))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "check"])
    parser.add_argument("--dimensions", type=int, default=os.environ.get("GRAPH_VECTOR_DIMENSIONS") or None)
    parser.add_argument("--wait", type=float, default=0)
    args = parser.parse_args()
    try:
        asyncio.run(main(args))
    except IndexConfigurationError as error:
        print(json.dumps({"ok": False, "problem": str(error)}))
        raise SystemExit(1)
    except Exception as error:
        # Connection/driver errors can contain private URLs. Print only the type.
        print(json.dumps({"ok": False, "error_type": type(error).__name__}))
        raise SystemExit(1)

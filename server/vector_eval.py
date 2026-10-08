"""Read-only, sequential exact-versus-indexed retrieval evaluation.

Queries use identical vectors from a JSON file or a bounded sample of stored
embeddings. Output contains only UUIDs, counts, timings, and quality metrics.
No embedding model, LLM, private note text, or write procedure is used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import time
from pathlib import Path

from neo4j import Query
from graphiti_core.driver.neo4j.operations.search_ops import Neo4jSearchOperations
from graphiti_core.search.search_filters import SearchFilters

from vector_indexes import Executor, connection
from vector_search import EDGE_INDEX, NODE_INDEX, IndexedReadSearch


class MeasuredExecutor(Executor):
    def __init__(self, driver, database, timeout):
        super().__init__(driver, database)
        self.timeout = timeout
        self.ann_calls = 0
        self.exact_calls = 0

    async def execute_query(self, query, **params):
        if "CALL db.index.vector.query" in query:
            self.ann_calls += 1
        elif "vector.similarity.cosine" in query:
            self.exact_calls += 1
        return await super().execute_query(Query(query, timeout=self.timeout), **params)


async def sample_vectors(executor, kind, groups, samples):
    spec = EDGE_INDEX if kind == "edge" else NODE_INDEX
    pattern = "()-[x:RELATES_TO]->()" if kind == "edge" else "(x:Entity)"
    where = f"x.{spec.property} IS NOT NULL"
    if groups is not None:
        where += " AND x.group_id IN $groups"
    rows, _, _ = await executor.execute_query(
        f"MATCH {pattern} WHERE {where} WITH x ORDER BY x.uuid LIMIT $samples "
        f"RETURN x.{spec.property} AS vector", groups=groups, samples=samples, routing_="r",
    )
    return [list(row["vector"]) for row in rows]


def quality(exact, indexed):
    expected = [item.uuid for item in exact]
    actual = [item.uuid for item in indexed]
    overlap = set(expected) & set(actual)
    return {"exact_ids": expected, "indexed_ids": actual,
            "recall_at_k": len(overlap) / len(expected) if expected else float(not actual),
            "same_order": expected == actual,
            "metadata_equal": all(
                next(x for x in exact if x.uuid == uuid).model_dump()
                == next(x for x in indexed if x.uuid == uuid).model_dump() for uuid in overlap)}


async def evaluate(executor, vectors, *, kind, groups=None, limit=10, min_score=0.6,
                   overfetch=20, max_candidates=2000, timeout=600):
    legacy = Neo4jSearchOperations()
    indexed = IndexedReadSearch(overfetch=overfetch, max_candidates=max_candidates)
    for i, vector in enumerate(vectors):
        if (not 1 <= len(vector) <= 4096 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector)
                or not any(vector)):
            raise ValueError("Invalid query vector")
        results, times = [], []
        ann_before, exact_before = executor.ann_calls, executor.exact_calls
        for adapter in (legacy, indexed):
            started = time.monotonic()
            if kind == "edge":
                call = adapter.edge_similarity_search(executor, vector, None, None, SearchFilters(), groups, limit, min_score)
            else:
                call = adapter.node_similarity_search(executor, vector, SearchFilters(), groups, limit, min_score)
            results.append(await asyncio.wait_for(call, timeout=timeout))
            times.append(round((time.monotonic() - started) * 1000, 2))
        yield {"sample": i, "kind": kind, "dimensions": len(vector),
               "exact_ms": times[0], "indexed_ms": times[1],
               "ann_calls": executor.ann_calls - ann_before,
               "fallback": executor.exact_calls - exact_before > 1,
               **quality(*results)}


async def main(args):
    if not 1 <= args.samples <= 20 or not 1 <= args.limit <= 100:
        raise ValueError("Sample or result limit out of bounds")
    if not 1 <= args.timeout <= 900 or not 0 <= args.min_recall <= 1:
        raise ValueError("Invalid evaluation bounds")
    async with connection() as driver:
        executor = MeasuredExecutor(driver, os.environ.get("NEO4J_DATABASE", "neo4j"), args.timeout)
        vectors = (json.loads(Path(args.vectors).read_text())[:args.samples] if args.vectors
                   else await sample_vectors(executor, args.kind, args.group, args.samples))
        if not vectors:
            raise ValueError("No query vectors")
        passed = True
        async for row in evaluate(executor, vectors, kind=args.kind, groups=args.group,
                                  limit=args.limit, min_score=args.min_score, timeout=args.timeout,
                                  overfetch=args.overfetch, max_candidates=args.max_candidates):
            print(json.dumps(row), flush=True)
            passed &= (row["recall_at_k"] >= args.min_recall and row["metadata_equal"]
                       and row["ann_calls"] > 0 and not row["fallback"])
        return 0 if passed else 2


if __name__ == "__main__":
    # Driver diagnostics can include endpoints or query parameters. JSON is the
    # sole CLI output contract; adapter diagnostics remain enabled in the server.
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["node", "edge"], required=True)
    parser.add_argument("--group", action="append", help="Repeat for multiple group IDs")
    parser.add_argument("--vectors", help="JSON array of query vectors; never printed")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-score", type=float, default=0.6)
    parser.add_argument("--min-recall", type=float, default=0.9)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--overfetch", type=int, default=20)
    parser.add_argument("--max-candidates", type=int, default=2000)
    args = parser.parse_args()
    try:
        result = asyncio.run(main(args))
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__}), flush=True)
        result = 1
    raise SystemExit(result)

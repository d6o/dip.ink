"""Read-only recall and latency evaluation: exact Graphiti ingest searches vs ANN adapter.

Run inside the memory pod. Queries run sequentially on one connection.
Output: JSONL rows with UUIDs, scores, and timings only (no fact text, no vectors).

  python3 recall_eval.py sample --edges 200 --nodes 200 --days 7 --sample /tmp/vre/sample.json
  python3 recall_eval.py run --sample /tmp/vre/sample.json --out /tmp/vre/results.jsonl
  python3 recall_eval.py verify --sample /tmp/vre/sample.json   # replica == adapter check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")

from neo4j import AsyncGraphDatabase  # noqa: E402
from graphiti_core.driver.driver import GraphProvider  # noqa: E402
from graphiti_core.graph_queries import get_vector_cosine_func_query, get_relationships_query  # noqa: E402
from graphiti_core.models.edges.edge_db_queries import get_entity_edge_return_query  # noqa: E402
from graphiti_core.models.nodes.node_db_queries import get_entity_node_return_query  # noqa: E402
from graphiti_core.helpers import lucene_sanitize  # noqa: E402
from graphiti_core.search.search_utils import MAX_QUERY_LENGTH  # noqa: E402

P = GraphProvider.NEO4J
GROUP = os.environ.get("GROUP_ID", "mykg")
MIN_SCORE = 0.6           # DEFAULT_MIN_SCORE and NODE_DEDUP_COSINE_MIN_SCORE
EDGE_LIMIT = 20           # 2 * DEFAULT_SEARCH_LIMIT, cosine and BM25 branch of EDGE_HYBRID_SEARCH_RRF
NODE_LIMIT = 15           # NODE_DEDUP_CANDIDATE_LIMIT
FINAL_LIMIT = 10          # SearchConfig.limit
OVERFETCH = [int(x) for x in os.environ.get("VRE_OVERFETCH", "20,40,80,100").split(",")]
MAX_CANDIDATES = int(os.environ.get("VRE_MAX_CANDIDATES", "2000"))

EDGE_PROJ = get_entity_edge_return_query(P)
NODE_PROJ = get_entity_node_return_query(P)


def tail(alias, prop, where):
    cos = get_vector_cosine_func_query(f"{alias}.{prop}", "$search_vector", P)
    scope = "e, n, m" if alias == "e" else "n"
    proj = EDGE_PROJ if alias == "e" else NODE_PROJ
    return (where + f" WITH DISTINCT {scope}, {cos} AS score "
            "WHERE score > $min_score RETURN " + proj + ", score AS _score ORDER BY score DESC LIMIT $limit")


EDGE_WHERE = " WHERE e.group_id IN $group_ids"
NODE_WHERE = " WHERE n.group_id IN $group_ids"
EXACT_EDGE = "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)" + tail("e", "fact_embedding", EDGE_WHERE)
EXACT_NODE = "MATCH (n:Entity)" + tail("n", "name_embedding", NODE_WHERE)
ANN_EDGE = ("CALL db.index.vector.queryRelationships($index_name, $candidates, $search_vector) "
            "YIELD relationship AS e WITH e, startNode(e) AS n, endNode(e) AS m "
            "WHERE n:Entity AND m:Entity WITH e, n, m" + tail("e", "fact_embedding", EDGE_WHERE))
ANN_NODE = ("CALL db.index.vector.queryNodes($index_name, $candidates, $search_vector) "
            "YIELD node AS n WITH n" + tail("n", "name_embedding", NODE_WHERE))
REL_EDGE = ("MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)"
            + tail("e", "fact_embedding", " WHERE e.uuid in $edge_uuids AND e.group_id IN $group_ids"))
FT_EDGE = (get_relationships_query("edge_name_and_fact", limit=0, provider=P)
           + " YIELD relationship AS rel, score MATCH (n:Entity)-[e:RELATES_TO {uuid: rel.uuid}]->(m:Entity) "
           "{where} WITH e, score, n, m RETURN " + EDGE_PROJ + " ORDER BY score DESC LIMIT $limit")
# EntityEdge.get_between_nodes in 0.29.2 (Neo4j entity_edge_ops): directed source->target.
BETWEEN = ("MATCH (n:Entity {uuid: $s})-[e:RELATES_TO]->(m:Entity {uuid: $t}) RETURN e.uuid AS uuid")


def ft_query(text):
    q = lucene_sanitize(text)
    if len(q.split(" ")) + len([GROUP]) >= MAX_QUERY_LENGTH:
        return ""
    return f'group_id:"{GROUP}" AND (' + q + ")"


def candidates(limit, overfetch):
    return min(MAX_CANDIDATES, max(100, limit * overfetch))


async def q(d, cypher, **params):
    t = time.monotonic()
    rows, _, _ = await d.execute_query(cypher, routing_="r", **params)
    return rows, time.monotonic() - t


def strip(rows, self_uuid, k):
    out = [(r["uuid"], float(r["_score"]) if "_score" in r.keys() else None) for r in rows]
    return [x for x in out if x[0] != self_uuid][:k]


async def do_sample(d, a):
    edges, _ = await q(d, (
        "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) WHERE e.created_at > datetime() - duration({days: $days}) "
        "AND e.group_id = $g AND e.fact_embedding IS NOT NULL AND e.fact IS NOT NULL "
        "WITH e, n, m, rand() AS r ORDER BY r LIMIT $k "
        "RETURN e.uuid AS uuid, n.uuid AS s, m.uuid AS t, e.fact AS fact, e.fact_embedding AS v, "
        "toString(e.created_at) AS created_at"), days=a.days, g=GROUP, k=a.edges)
    nodes, _ = await q(d, (
        "MATCH (n:Entity) WHERE n.created_at > datetime() - duration({days: $days}) AND n.group_id = $g "
        "AND n.name_embedding IS NOT NULL WITH n, rand() AS r ORDER BY r LIMIT $k "
        "RETURN n.uuid AS uuid, n.name AS name, n.name_embedding AS v, toString(n.created_at) AS created_at"),
        days=a.days, g=GROUP, k=a.nodes)
    data = {"edges": [dict(r) for r in edges], "nodes": [dict(r) for r in nodes]}
    os.makedirs(os.path.dirname(a.sample), exist_ok=True)
    with open(a.sample, "w") as f:
        json.dump(data, f)
    print("sampled", len(data["edges"]), "edges", len(data["nodes"]), "nodes",
          "dims", {len(x["v"]) for x in data["edges"] + data["nodes"]})


async def eval_node(d, x):
    v, me = x["v"], x["uuid"]
    rows, t = await q(d, EXACT_NODE, search_vector=v, group_ids=[GROUP], min_score=MIN_SCORE,
                      limit=NODE_LIMIT + 1)
    out = {"kind": "node", "uuid": me, "exact": strip(rows, me, NODE_LIMIT), "exact_s": t, "ann": {}}
    for of in OVERFETCH:
        c = candidates(NODE_LIMIT, of)
        rows, t = await q(d, ANN_NODE, index_name="dipink_entity_name_vector", candidates=c + 1,
                          search_vector=v, group_ids=[GROUP], min_score=MIN_SCORE, limit=NODE_LIMIT + 1)
        res = strip(rows, me, NODE_LIMIT)
        out["ann"][of] = {"res": res, "s": t, "candidates": c, "underfilled": len(res) < NODE_LIMIT}
    return out


async def eval_edge(d, x):
    v, me = x["v"], x["uuid"]
    rows, t = await q(d, EXACT_EDGE, search_vector=v, group_ids=[GROUP], min_score=MIN_SCORE,
                      limit=EDGE_LIMIT + 1)
    out = {"kind": "edge", "uuid": me, "exact": strip(rows, me, EDGE_LIMIT), "exact_s": t, "ann": {}}
    for of in OVERFETCH:
        c = candidates(EDGE_LIMIT, of)
        rows, t = await q(d, ANN_EDGE, index_name="dipink_fact_vector", candidates=c + 1,
                          search_vector=v, group_ids=[GROUP], min_score=MIN_SCORE, limit=EDGE_LIMIT + 1)
        res = strip(rows, me, EDGE_LIMIT)
        out["ann"][of] = {"res": res, "s": t, "candidates": c, "underfilled": len(res) < EDGE_LIMIT}
    # BM25 half of EDGE_HYBRID_SEARCH_RRF, invalidation shape: SearchFilters(), group filter.
    fq = ft_query(x["fact"])
    if fq:
        rows, t = await q(d, FT_EDGE.replace("{where}", "WHERE e.group_id IN $group_ids"), query=fq,
                          group_ids=[GROUP], limit=EDGE_LIMIT + 1)
        out["bm25"] = [u for u, _ in strip(rows, me, EDGE_LIMIT)]
    else:
        t = 0.0
        out["bm25"] = []
    out["bm25_s"] = t
    # Related-edges shape: SearchFilters(edge_uuids=get_between_nodes(source, target)), exact path.
    rows, t_between = await q(d, BETWEEN, s=x["s"], t=x["t"])
    between = [r["uuid"] for r in rows if r["uuid"] != me]
    rows, t_rel = await q(d, REL_EDGE, search_vector=v, edge_uuids=between, group_ids=[GROUP],
                          min_score=MIN_SCORE, limit=EDGE_LIMIT)
    if fq:
        _, t_relft = await q(d, FT_EDGE.replace("{where}", "WHERE e.uuid in $edge_uuids AND e.group_id IN $group_ids"),
                             query=fq, edge_uuids=between, group_ids=[GROUP], limit=EDGE_LIMIT)
    else:
        t_relft = 0.0
    out["related"] = {"n_between": len(between), "between_s": t_between, "cosine_s": t_rel,
                      "bm25_s": t_relft, "n_res": len(rows)}
    return out


async def do_run(d, a):
    data = json.load(open(a.sample))
    done = set()
    if os.path.exists(a.out):
        for line in open(a.out):
            r = json.loads(line)
            done.add((r["kind"], r["uuid"]))
    order = [("node", x) for x in data["nodes"]] + [("edge", x) for x in data["edges"]]
    if a.interleave:
        nodes = [("node", x) for x in data["nodes"]]
        edges = [("edge", x) for x in data["edges"]]
        order = [y for pair in zip(nodes, edges) for y in pair]
    started = time.monotonic()
    with open(a.out, "a") as f:
        for i, (kind, x) in enumerate(order):
            if (kind, x["uuid"]) in done:
                continue
            try:
                r = await (eval_node(d, x) if kind == "node" else eval_edge(d, x))
            except Exception as e:  # keep going; record only the error class
                r = {"kind": kind, "uuid": x["uuid"], "error": type(e).__name__}
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(f"{i + 1}/{len(order)} {kind} {time.monotonic() - started:.0f}s "
                  f"exact={r.get('exact_s', -1):.1f}s", flush=True)
            if a.pause:
                await asyncio.sleep(a.pause)


async def do_verify(d, a):
    """Check that the replica query returns the same rows as the real adapter."""
    from vector_search import IndexedReadSearch
    from graphiti_core.search.search_filters import SearchFilters
    data = json.load(open(a.sample))
    ad = IndexedReadSearch(overfetch=20, max_candidates=MAX_CANDIDATES, dimensions=1024)

    class Ex:
        async def execute_query(self, cypher, **kw):
            return await d.execute_query(cypher, **kw)
    ex = Ex()
    for x in data["nodes"][:3]:
        got = await ad.node_similarity_search(ex, x["v"], SearchFilters(), [GROUP], NODE_LIMIT, MIN_SCORE)
        rows, _ = await q(d, ANN_NODE, index_name="dipink_entity_name_vector", candidates=candidates(NODE_LIMIT, 20),
                          search_vector=x["v"], group_ids=[GROUP], min_score=MIN_SCORE, limit=NODE_LIMIT)
        print("node", [n.uuid for n in got] == [r["uuid"] for r in rows], len(got))
    for x in data["edges"][:3]:
        got = await ad.edge_similarity_search(ex, x["v"], None, None, SearchFilters(), [GROUP], EDGE_LIMIT,
                                              MIN_SCORE)
        rows, _ = await q(d, ANN_EDGE, index_name="dipink_fact_vector", candidates=candidates(EDGE_LIMIT, 20),
                          search_vector=x["v"], group_ids=[GROUP], min_score=MIN_SCORE, limit=EDGE_LIMIT)
        a_ids, r_ids = [e.uuid for e in got], [r["uuid"] for r in rows]
        print("edge order", a_ids == r_ids, "set", set(a_ids) == set(r_ids), len(got),
              "tied_scores", len(rows) - len({round(r["_score"], 9) for r in rows}))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sample", "run", "verify"])
    ap.add_argument("--edges", type=int, default=200)
    ap.add_argument("--nodes", type=int, default=200)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--sample", default="/tmp/vre/sample.json")
    ap.add_argument("--out", default="/tmp/vre/results.jsonl")
    ap.add_argument("--pause", type=float, default=0.0)
    ap.add_argument("--interleave", action="store_true")
    a = ap.parse_args()
    d = AsyncGraphDatabase.driver(os.environ["NEO4J_URI"],
                                  auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
                                  max_connection_pool_size=1)
    try:
        await {"sample": do_sample, "run": do_run, "verify": do_verify}[a.cmd](d, a)
    finally:
        await d.close()

asyncio.run(main())

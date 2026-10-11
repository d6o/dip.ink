"""Build realistic dedupe_edges.resolve_edge inputs from the live graph (read-only).

Run inside the memory pod. The script reproduces the candidate retrieval of
graphiti-core 0.29.2 resolve_extracted_edges for an edge that is already in
the graph, as of the time when its source episode started:

* related edges ("EXISTING FACTS"): EntityEdge.get_between_nodes(source, target),
  then the EDGE_HYBRID_SEARCH_RRF search with SearchFilters(edge_uuids=...)
  (BM25 + cosine >= 0.6, 2*limit each, RRF, limit 10);
* invalidation candidates: the same hybrid search over the whole group,
  minus the related edges;
* "as of" = only edges with created_at < episode.created_at; the edge itself
  and other edges of the same episode are excluded.

The ingest vector adapter (server/vector_search.py, overfetch 100) is used
for the cosine branch, like the ingest CronJob. Each branch fetches a wide
list, then the script removes the edges that did not exist yet and cuts
each branch to 2*limit before RRF. The query vector is the stored
fact_embedding of the edge (Graphiti embeds the same fact text).

Sample groups:
  R  random edges created in the last N days (single episode)
  C  edges that Graphiti used to invalidate an older edge X:
     X.invalid_at = Y.valid_at, Y created < 2 h before X.expired_at,
     X and Y share an entity; X must be in the rebuilt candidate lists
  D  edges with an older near-duplicate between the same two entities
     (another episode, cosine >= 0.85)

  python3 resolve_edge_build.py --days 3 --r 80 --c 50 --d 50 --out /tmp/ret/cases.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")
os.environ.setdefault("INGEST_VECTOR_SEARCH", "1")

from graphiti_core.driver.neo4j_driver import Neo4jDriver  # noqa: E402
from graphiti_core.search.search_filters import SearchFilters  # noqa: E402
from graphiti_core.search.search_utils import (  # noqa: E402
    edge_fulltext_search,
    edge_similarity_search,
    rrf,
)
from graphiti_core.utils.maintenance.dedup_helpers import _normalize_string_exact  # noqa: E402
from vector_search import install_ingest_search  # noqa: E402

GROUP = os.environ.get("GROUP_ID", "mykg")
LIMIT = 10          # SearchConfig.limit (DEFAULT_SEARCH_LIMIT)
BRANCH = 2 * LIMIT  # edge_search fetches 2*limit per method
WIDE = 120          # wide fetch before the "as of" filter
MIN_SCORE = 0.6     # EdgeSearchConfig.sim_min_score


def _dt(v):
    if v is None:
        return None
    if hasattr(v, "to_native"):
        v = v.to_native()
    return v


def _iso(v):
    v = _dt(v)
    return v.isoformat() if v is not None else None


async def q(driver, cypher, **kw):
    rows, _, _ = await driver.execute_query(cypher, routing_="r", **kw)
    return [dict(r) for r in rows]


def edge_info(e, idx=None):
    out = {
        "uuid": e.uuid,
        "fact": e.fact,
        "created_at": _iso(e.created_at),
        "valid_at": _iso(e.valid_at),
        "invalid_at": _iso(e.invalid_at),
        "expired_at": _iso(e.expired_at),
        "source": e.source_node_uuid,
        "target": e.target_node_uuid,
    }
    if idx is not None:
        out["idx"] = idx
    return out


async def build_case(driver, row):
    """row: uuid, fact, src, tgt, emb, ep_uuid, ep_created, ep_name."""
    cutoff = _dt(row["ep_created"])
    me = row["uuid"]

    def alive(e):
        c = _dt(e.created_at)
        return e.uuid != me and c is not None and c < cutoff

    # related edges: same direction, same endpoints, as of the cutoff
    between = await q(
        driver,
        "MATCH (n:Entity {uuid:$s})-[e:RELATES_TO]->(m:Entity {uuid:$t}) "
        "WHERE e.uuid <> $me AND e.created_at < $cut RETURN e.uuid AS uuid",
        s=row["src"], t=row["tgt"], me=me, cut=row["ep_created"],
    )
    between_uuids = [r["uuid"] for r in between]
    related = []
    if between_uuids:
        f = SearchFilters(edge_uuids=between_uuids)
        ft, sim = await asyncio.gather(
            edge_fulltext_search(driver, row["fact"], f, [GROUP], BRANCH),
            edge_similarity_search(driver, row["emb"], None, None, f, [GROUP], BRANCH, MIN_SCORE),
        )
        by = {e.uuid: e for e in ft + sim}
        order, _ = rrf([[e.uuid for e in ft], [e.uuid for e in sim]])
        related = [by[u] for u in order[:LIMIT]]

    f = SearchFilters()
    ft, sim = await asyncio.gather(
        edge_fulltext_search(driver, row["fact"], f, [GROUP], WIDE),
        edge_similarity_search(driver, row["emb"], None, None, f, [GROUP], WIDE, MIN_SCORE),
    )
    ft = [e for e in ft if alive(e)][:BRANCH]
    sim = [e for e in sim if alive(e)][:BRANCH]
    by = {e.uuid: e for e in ft + sim}
    order, _ = rrf([[e.uuid for e in ft], [e.uuid for e in sim]])
    rel_uuids = {e.uuid for e in related}
    inval = [by[u] for u in order[:LIMIT] if u not in rel_uuids]

    norm = _normalize_string_exact(row["fact"])
    fast_path = any(
        e.source_node_uuid == row["src"] and e.target_node_uuid == row["tgt"]
        and _normalize_string_exact(e.fact) == norm
        for e in related
    )
    return {
        "uuid": me,
        "new_fact": row["fact"],
        "source": row["src"],
        "target": row["tgt"],
        "valid_at": _iso(row.get("valid_at")),
        "episode": row["ep_name"],
        "episode_created_at": _iso(row["ep_created"]),
        "related": [edge_info(e, i) for i, e in enumerate(related)],
        "candidates": [edge_info(e, len(related) + i) for i, e in enumerate(inval)],
        "fast_path": fast_path,
        "llm_called": bool(related or inval) and not fast_path,
    }


BASE = (
    "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) "
    "WHERE e.group_id = $g AND e.created_at > datetime() - duration({days:$days}) "
    "AND size(e.episodes) = 1 "
)
RET = (
    " MATCH (ep:Episodic {uuid: e.episodes[0]}) "
    "RETURN e.uuid AS uuid, e.fact AS fact, n.uuid AS src, m.uuid AS tgt, "
    "e.fact_embedding AS emb, e.valid_at AS valid_at, ep.uuid AS ep_uuid, "
    "ep.created_at AS ep_created, ep.name AS ep_name"
)


async def pick_random(driver, days, n, seed):
    rows = await q(driver, BASE + "RETURN e.uuid AS uuid", g=GROUP, days=days)
    uuids = sorted(r["uuid"] for r in rows)
    random.Random(seed).shuffle(uuids)
    return uuids[: n * 2]


async def pick_contradictions(driver, days):
    rows = await q(
        driver,
        "MATCH (a:Entity)-[x:RELATES_TO]->(b:Entity) "
        "WHERE x.group_id = $g AND x.expired_at > datetime() - duration({days:$days}) "
        "AND x.invalid_at IS NOT NULL AND x.expired_at > x.created_at + duration('PT5M') "
        "MATCH (c:Entity)-[y:RELATES_TO]->(d:Entity) "
        "WHERE y.group_id = $g AND y.valid_at = x.invalid_at AND y.uuid <> x.uuid "
        "AND y.created_at <= x.expired_at AND y.created_at >= x.expired_at - duration('PT2H') "
        "AND y.created_at > x.created_at AND size(y.episodes) = 1 "
        "AND (a IN [c, d] OR b IN [c, d]) "
        "WITH x, y, vector.similarity.cosine(x.fact_embedding, y.fact_embedding) AS cos "
        "ORDER BY cos DESC "
        "WITH x, collect({y: y.uuid, cos: cos})[0] AS best "
        "RETURN x.uuid AS x, best.y AS y, best.cos AS cos",
        g=GROUP, days=days,
    )
    return rows


async def pick_near_duplicates(driver, days):
    rows = await q(
        driver,
        "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) "
        "WHERE e.group_id = $g AND e.created_at > datetime() - duration({days:$days}) "
        "AND size(e.episodes) = 1 "
        "MATCH (n)-[a:RELATES_TO]->(m) "
        "WHERE a.uuid <> e.uuid AND a.created_at < e.created_at "
        "AND NOT e.episodes[0] IN a.episodes "
        "WITH e, a, vector.similarity.cosine(a.fact_embedding, e.fact_embedding) AS cos "
        "WHERE cos >= 0.85 "
        "WITH e, max(cos) AS cos RETURN e.uuid AS uuid, cos",
        g=GROUP, days=days,
    )
    return rows


async def load_rows(driver, uuids):
    rows = await q(
        driver,
        "UNWIND $u AS id MATCH (n:Entity)-[e:RELATES_TO {uuid: id}]->(m:Entity)" + RET,
        u=list(uuids),
    )
    return {r["uuid"]: r for r in rows}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--r", type=int, default=80)
    ap.add_argument("--c", type=int, default=50)
    ap.add_argument("--d", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20261012)
    ap.add_argument("--out", default="/tmp/ret/cases.json")
    a = ap.parse_args()

    driver = Neo4jDriver(
        os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"],
        database=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )
    install_ingest_search(driver)
    rnd = random.Random(a.seed)
    cases, seen = [], set()

    async def take(group, uuids, want, extra=None, check=None):
        rows = await load_rows(driver, uuids)
        got = 0
        for u in uuids:
            if got >= want:
                break
            if u in seen or u not in rows:
                continue
            case = await build_case(driver, rows[u])
            if not case["llm_called"]:
                print(f"[{group}] skip {u[:8]} (no LLM call: fast path or no candidates)", flush=True)
                continue
            if check and not check(case):
                continue
            case["group"] = group
            case["id"] = f"{group}{got:03d}"
            if extra:
                case["hist"] = extra.get(u, {})
            seen.add(u)
            cases.append(case)
            got += 1
            print(f"[{group}] {case['id']} rel={len(case['related'])} cand={len(case['candidates'])}", flush=True)
        return got

    # C first: the rarest group
    links = await pick_contradictions(driver, a.days)
    print(f"[C] links found: {len(links)}", flush=True)
    rnd.shuffle(links)
    by_y = {}
    for l in links:
        by_y.setdefault(l["y"], []).append(l["x"])
    ys = list(by_y)
    hist_c = {y: {"historic_contradicted": xs} for y, xs in by_y.items()}

    def has_x(case):
        ids = {e["uuid"] for e in case["related"] + case["candidates"]}
        hits = [x for x in case_hist(case) if x in ids]
        return bool(hits)

    def case_hist(case):
        return by_y.get(case["uuid"], [])

    await take("C", ys, a.c, hist_c, has_x)

    dups = await pick_near_duplicates(driver, a.days)
    print(f"[D] near-duplicate edges found: {len(dups)}", flush=True)
    rnd.shuffle(dups)
    hist_d = {r["uuid"]: {"max_same_pair_cos": r["cos"]} for r in dups}
    await take("D", [r["uuid"] for r in dups], a.d, hist_d)

    await take("R", await pick_random(driver, a.days, a.r, a.seed), a.r)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump({"built_at": datetime.now(timezone.utc).isoformat(), "days": a.days, "cases": cases}, fh)
    print(f"[done] {len(cases)} cases -> {a.out}", flush=True)
    await driver.close()


if __name__ == "__main__":
    asyncio.run(main())

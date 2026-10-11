"""Read-only: are the newest committed edges and nodes visible to the vector indexes?

For the N most recent RELATES_TO edges and Entity nodes, query the ANN index with the
stored vector and check that the item itself comes back (adapter candidate budget).
"""
import asyncio
import os
import time

from neo4j import AsyncGraphDatabase

N = int(os.environ.get("VRE_FRESH_N", "40"))


async def main():
    d = AsyncGraphDatabase.driver(os.environ["NEO4J_URI"],
                                  auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
                                  max_connection_pool_size=1)
    edges, _, _ = await d.execute_query(
        "MATCH ()-[e:RELATES_TO]->() WHERE e.created_at > datetime() - duration('PT6H') "
        "AND e.fact_embedding IS NOT NULL RETURN e.uuid AS uuid, e.fact_embedding AS v, "
        "duration.inSeconds(e.created_at, datetime()).seconds AS age ORDER BY e.created_at DESC LIMIT $n",
        n=N, routing_="r")
    nodes, _, _ = await d.execute_query(
        "MATCH (n:Entity) WHERE n.created_at > datetime() - duration('PT6H') AND n.name_embedding IS NOT NULL "
        "RETURN n.uuid AS uuid, n.name_embedding AS v, "
        "duration.inSeconds(n.created_at, datetime()).seconds AS age ORDER BY n.created_at DESC LIMIT $n",
        n=N, routing_="r")
    for kind, rows, idx, proc, cand in (
            ("edge", edges, "dipink_fact_vector", "queryRelationships", 400),
            ("node", nodes, "dipink_entity_name_vector", "queryNodes", 300)):
        found = 0
        ages = []
        for r in rows:
            yield_ = "relationship AS x" if proc == "queryRelationships" else "node AS x"
            res, _, _ = await d.execute_query(
                f"CALL db.index.vector.{proc}($idx, $k, $v) YIELD {yield_}, score "
                "RETURN x.uuid AS uuid", idx=idx, k=cand, v=r["v"], routing_="r")
            ids = [x["uuid"] for x in res]
            found += r["uuid"] in ids
            ages.append(r["age"])
        print(f"{kind}: newest {len(rows)} items, age {min(ages, default=0)}-{max(ages, default=0)} s, "
              f"self found by ANN: {found}/{len(rows)}")
    await d.close()

asyncio.run(main())

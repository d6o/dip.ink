"""Read-only: EXPLAIN plans and vector index options. No query executes data scans."""
import asyncio
import os

from neo4j import AsyncGraphDatabase

REL = ("MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) WHERE e.uuid in $edge_uuids AND e.group_id IN $group_ids "
       "WITH DISTINCT e, n, m, vector.similarity.cosine(e.fact_embedding, $search_vector) AS score "
       "WHERE score > $min_score RETURN e.uuid AS uuid, score ORDER BY score DESC LIMIT $limit")
FT = ('CALL db.index.fulltext.queryRelationships("edge_name_and_fact", $query, {limit: $limit}) '
      "YIELD relationship AS rel, score MATCH (n:Entity)-[e:RELATES_TO {uuid: rel.uuid}]->(m:Entity) "
      "WHERE e.uuid in $edge_uuids AND e.group_id IN $group_ids WITH e, score, n, m "
      "RETURN e.uuid AS uuid ORDER BY score DESC LIMIT $limit")


def ops(plan, depth=0, out=None):
    out = [] if out is None else out
    args = plan.get("args", {})
    out.append("  " * depth + plan["operatorType"] + "  " + str(args.get("Details", ""))[:160])
    for c in plan.get("children", []):
        ops(c, depth + 1, out)
    return out


async def main():
    d = AsyncGraphDatabase.driver(os.environ["NEO4J_URI"],
                                  auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
    rows, _, _ = await d.execute_query(
        "SHOW INDEXES YIELD name, options WHERE name STARTS WITH 'dipink_' AND name ENDS WITH 'vector' "
        "RETURN name, options", routing_="r")
    for r in rows:
        print(r["name"], r["options"])
    vec = [0.01] * 1024
    for name, q, p in [
        ("related_cosine", REL, dict(edge_uuids=["x", "y"], group_ids=["mykg"], search_vector=vec,
                                     min_score=0.6, limit=20)),
        ("related_cosine_empty", REL, dict(edge_uuids=[], group_ids=["mykg"], search_vector=vec,
                                           min_score=0.6, limit=20)),
        ("related_fulltext", FT, dict(edge_uuids=["x"], group_ids=["mykg"], query='group_id:"mykg" AND (x)',
                                      limit=20)),
    ]:
        async with d.session() as s:
            res = await s.run("EXPLAIN " + q, parameters=p)
            summ = await res.consume()
            print("==", name)
            print("\n".join(ops(summ.plan)))
    await d.close()

asyncio.run(main())

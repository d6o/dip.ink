"""Read-only exploration of the graph shape for the recall evaluation."""
import asyncio
import os

from neo4j import AsyncGraphDatabase

Q = {
    "edge_groups": "MATCH ()-[e:RELATES_TO]->() RETURN e.group_id AS g, count(*) AS c ORDER BY c DESC",
    "node_groups": "MATCH (n:Entity) RETURN n.group_id AS g, count(*) AS c ORDER BY c DESC",
    "edges_7d": "MATCH ()-[e:RELATES_TO]->() WHERE e.created_at > datetime() - duration('P7D') "
                "RETURN count(*) AS c, sum(CASE WHEN e.fact_embedding IS NULL THEN 1 ELSE 0 END) AS no_emb",
    "nodes_7d": "MATCH (n:Entity) WHERE n.created_at > datetime() - duration('P7D') "
                "RETURN count(*) AS c, sum(CASE WHEN n.name_embedding IS NULL THEN 1 ELSE 0 END) AS no_emb",
    "indexes": "SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, state "
               "RETURN name, type, entityType, labelsOrTypes, properties, state",
    "version": "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition",
}


async def main():
    d = AsyncGraphDatabase.driver(os.environ["NEO4J_URI"],
                                  auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
    for name, q in Q.items():
        rows, _, _ = await d.execute_query(q, routing_="r")
        print("==", name)
        for r in rows:
            print("  ", dict(r))
    await d.close()

asyncio.run(main())

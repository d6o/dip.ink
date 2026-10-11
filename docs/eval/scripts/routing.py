"""Read-only: run the real IndexedReadSearch adapter on ingest-shaped calls; log its routing and timing."""
import asyncio, json, logging, os, sys, time
sys.path.insert(0, "/app")
from neo4j import AsyncGraphDatabase
from graphiti_core.search.search_filters import SearchFilters, edge_search_filter_query_constructor, node_search_filter_query_constructor
from graphiti_core.driver.driver import GraphProvider
from vector_search import IndexedReadSearch

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
for n in ("neo4j", "httpx"):
    logging.getLogger(n).setLevel(logging.WARNING)
print("node SearchFilters() clauses:", node_search_filter_query_constructor(SearchFilters(), GraphProvider.NEO4J)[0])
print("edge SearchFilters() clauses:", edge_search_filter_query_constructor(SearchFilters(), GraphProvider.NEO4J)[0])
print("edge SearchFilters(edge_uuids=[]) clauses:", edge_search_filter_query_constructor(SearchFilters(edge_uuids=[]), GraphProvider.NEO4J)[0])

async def main():
    d = AsyncGraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]), max_connection_pool_size=1)
    class Ex:
        async def execute_query(self, cypher, **kw):
            return await d.execute_query(cypher, **kw)
    ex = Ex()
    data = json.load(open("/tmp/vre/sample.json"))
    ad = IndexedReadSearch(overfetch=int(os.environ.get("OF", "20")), max_candidates=2000, dimensions=1024)
    t_meta = []
    for x in data["nodes"][:10]:
        t = time.monotonic(); await ad.node_similarity_search(ex, x["v"], SearchFilters(), ["mykg"], 15, 0.6); t_meta.append(time.monotonic() - t)
    print("adapter node call (metadata + ANN) s:", sorted(round(v, 3) for v in t_meta))
    t_meta = []
    for x in data["edges"][:10]:
        t = time.monotonic(); await ad.edge_similarity_search(ex, x["v"], None, None, SearchFilters(), ["mykg"], 20, 0.6); t_meta.append(time.monotonic() - t)
    print("adapter edge invalidation call (metadata + ANN) s:", sorted(round(v, 3) for v in t_meta))
    t_meta = []
    for x in data["edges"][:10]:
        t = time.monotonic(); await ad.edge_similarity_search(ex, x["v"], None, None, SearchFilters(edge_uuids=[]), ["mykg"], 20, 0.6); t_meta.append(time.monotonic() - t)
    print("adapter edge related call (edge_uuids=[]) s:", sorted(round(v, 3) for v in t_meta))
    t = time.monotonic()
    for _ in range(10):
        await d.execute_query("SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, options, state WHERE name = $index_name RETURN *", index_name="dipink_fact_vector", routing_="r")
    print("metadata query mean s:", round((time.monotonic() - t) / 10, 4))
    await d.close()
asyncio.run(main())

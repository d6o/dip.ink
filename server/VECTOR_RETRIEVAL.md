# Neo4j vector retrieval for graph reads

This optional adapter supports Neo4j 5.26.2 Community. It uses `db.index.vector.queryNodes` and `db.index.vector.queryRelationships`, not the newer `SEARCH` syntax.

## Scope and safety

- `GRAPH_VECTOR_SEARCH=0` is the default and rollback setting. Restart the memory server after a change.
- `graph._get_graph` installs the adapter only for graph reads. `ingest.build_graphiti` retains upstream exact matching and temporal invalidation.
- Graphiti 0.29.2 routes `search_` through `driver.search_interface`, not `driver.search_ops`. A per-instance bridge connects that interface to indexed read operations.
- Optional interface methods retain legacy fallback. In particular, legacy rerankers return UUID/score tuples, whereas operations rerankers return nodes.
- Fulltext, BFS, RRF, provenance parsing, and response packets remain unchanged.
- No request creates indexes. Missing, offline, incompatible, or failed indexes cause exact fallback for that branch.
- The adapter checks index metadata before each ANN query. Metadata permission failures also cause exact fallback.
- ANN returns bounded candidates. The adapter applies the group restriction and reranks with the existing `vector.similarity.cosine` function.
- The score threshold remains strictly `score > min_score`. Scores use the Neo4j 0–1 range.
- No implicit current-only filter applies. Superseded facts retain their UUIDs, episode references, and temporal fields.
- Explicit label, date, edge-type, edge-UUID, or source/target constraints use exact search. They cannot starve behind global ANN candidates.
- Group-only queries use ANN, but underfilled results fall back to exact search. This protects small groups from empty or truncated candidate sets.
- Source/target restrictions apply even without group IDs. Upstream 0.29.2 applies those restrictions only when group IDs are present; rollback retains upstream behavior.

ANN cannot guarantee the exact top-k even when it fills the result limit. Large competing groups can reduce recall. Compare representative group queries before enablement. Increase overfetch, or retain exact mode, if recall is insufficient.

An exact fallback can retain the old latency. Logs report a bounded reason and retrieval mode, without query text, embeddings, or exception messages.

## Explicit index administration

Use the existing `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, and optional `NEO4J_DATABASE` environment variables. Do not put credentials on the command line.

Index construction consumes additional disk, heap, and filesystem cache. Check resource headroom before creation. These commands never drop or replace an incompatible index.

```sh
cd server
# Create both indexes once. Existing compatible definitions are safe to reuse.
python vector_indexes.py create --dimensions 1024 --wait 120
# Read-only schema, cosine, dimension, and ONLINE checks.
python vector_indexes.py check --dimensions 1024
```

Without `--dimensions` or `GRAPH_VECTOR_DIMENSIONS`, the command discovers each property's dimension separately. Discovery scans distinct stored vector sizes. It rejects empty or mixed dimensions. Supply the known dimension for an empty database; repair mixed data separately.

Managed indexes:

| Name | Schema | Similarity |
|---|---|---|
| `dipink_entity_name_vector` | `(:Entity).name_embedding` | cosine |
| `dipink_fact_vector` | `[:RELATES_TO].fact_embedding` | cosine |

`create` uses `IF NOT EXISTS`. Both definitions are checked before any DDL. Readiness checks also validate the final definitions after creation. `--wait` bounds the readiness poll; the default is no wait. A timeout leaves valid indexes to finish population. Run `check` again before enablement.

## Read-only quality and latency comparison

Use read-only database credentials where available. No production evaluation runs automatically.

```sh
cd server
python vector_eval.py --kind edge --group main --samples 3 --limit 10
python vector_eval.py --kind node --group main --samples 3 --limit 10
# Use representative precomputed query vectors from the same embedding model.
python vector_eval.py --kind edge --group main --vectors /tmp/query-vectors.json --samples 5
```

The JSON file contains an array of vectors. It contains no required query text. Without a file, the evaluator samples stored vectors by UUID. Self-vector samples are only a smoke check; representative query vectors are required for rollout quality assessment.

The evaluator compares operation-level retrieval, not the complete `Graphiti.search_` path. Direct comparisons alone do not prove runtime routing. Repeat representative end-to-end queries before rollout.

The evaluator runs sequentially: concurrency is one, and the connection pool is two. It passes the same vector, groups, score threshold, and limit to exact and indexed search. `--timeout` limits each call and its database queries (default 600 seconds). Samples are limited to 20; result limits are capped at 100.

Output is JSONL with UUIDs, timings, dimensions, overlap recall@k, order equality, metadata equality, ANN call counts, and fallback status. It prints no facts, titles, vectors, note contents, or secrets. Exit 2 means the recall threshold failed, metadata differed, or a fallback prevented a true ANN comparison. The default recall threshold is 0.9. Exit 1 means evaluation failed.

Use `--overfetch` and `--max-candidates` to compare candidate budgets. Runtime defaults are 20 times the result limit, with a floor of 100 and a cap of 2000 candidates. Hard configuration bounds are 100 for overfetch and 10,000 for the candidate cap.

After independent quality review, set `GRAPH_VECTOR_SEARCH=1`. Optionally set `GRAPH_VECTOR_DIMENSIONS` as an additional assertion. A query/index dimension mismatch uses exact fallback, never an incorrectly configured index.

## Tests

```sh
cd server
python -m unittest discover -s tests -p 'test_vector_search.py' -v
# ONLY against a temporary isolated Neo4j 5.26.2 database:
NEO4J_INTEGRATION=1 NEO4J_VECTOR_TEST_ISOLATED=1 \
  python -m unittest discover -s tests -p 'test_vector_neo4j_integration.py' -v
```

The real suite also calls `Graphiti.search_` through `graph._get_graph` with a fixture embedder at limits 3 and 8. It records both vector procedures and rejects unconstrained entity/edge cosine scans in enabled mode. Disabled mode uses exact scans. It compares fulltext/RRF results and metadata, explicit-filter fallback, and BFS results between modes.

The real suite creates fixture groups and global managed indexes. It refuses pre-existing managed indexes, and removes only its fixture data and indexes. CI supplies a disposable database. The offline-state test simulates `POPULATING` metadata over real Neo4j exact queries; it does not depend on a population race.

Reference: [Neo4j vector index documentation](https://neo4j.com/docs/cypher-manual/current/indexes/semantic-indexes/vector-indexes/). This implementation uses only the procedures and DDL available in 5.26.2.

# Ingest-shaped vector search: ANN adapter recall and latency against exact search

Date: 2026-10-11. Evaluator: agent `vector-recall-eval` (read-only).
System: memory image v0.1.24, graphiti-core 0.29.2, Neo4j 5.26.2 Community.
Graph: group `mykg` only. 181,579 RELATES_TO edges and 53,697 Entity nodes. Embeddings have 1024 dimensions.

## Summary

The ANN adapter (`server/vector_search.py`) finds the same candidates as the exact scan for nearly all ingest-shaped queries.
It is 13 to 70 times faster per query. With overfetch 100 and a cap of 2000 candidates, it returned the exact candidate set for:

- 200/200 node dedupe queries (k = 15).
- 199/200 invalidation-candidate cosine queries (k = 20). The one miss is at rank 17. Its score is 0.0034 above the returned item.
- 200/200 invalidation-candidate hybrid results (final top 10 after RRF).

No query lost a candidate with a Neo4j score of 0.9 or more, at any overfetch value.
The related-edges search (`edge_uuids` filter) is already cheap on the exact path (p50 8 ms). The adapter keeps it exact.

Recommendation: enable the adapter for ingest with `overfetch=100` and `max_candidates=2000`, after one code change and a guard (see "Recommendation").
Do not use the current read setting (`GRAPH_VECTOR_OVERFETCH=20`) for ingest. At overfetch 20, 3 node queries and 5 edge queries lost a candidate, and 2 hybrid results changed.

## 1. Search inventory of `add_episode` (graphiti-core 0.29.2)

Source: `/usr/local/lib/python3.12/site-packages/graphiti_core` in the memory pod.

| # | Search | Code | Call shape | Limit | min_score | Adapter route |
|---|---|---|---|---|---|---|
| 1 | Node dedupe candidates | `utils/maintenance/node_operations.py` `_semantic_candidate_search` | `node_similarity_search(vector, SearchFilters(), [group_id], 15, 0.6)`, one call for each extracted node. Cosine only: no BM25 half, no rerank. | `NODE_DEDUP_CANDIDATE_LIMIT = 15` | `NODE_DEDUP_COSINE_MIN_SCORE = 0.6` | ANN (no filter clauses) |
| 2 | Edges between the same endpoints | `edge_operations.py` `resolve_extracted_edges` | `EntityEdge.get_between_nodes(source, target)`: an exact MATCH on two UUIDs. | none | none | not a vector search |
| 3a | Related edges, BM25 half | `edge_operations.py` ~line 393 | `search(fact, [group_id], EDGE_HYBRID_SEARCH_RRF, SearchFilters(edge_uuids=<#2 result>))` → `edge_fulltext_search(..., 2*limit)` | 20 | none | upstream fulltext (unchanged) |
| 3b | Related edges, cosine half | same | `edge_similarity_search(vector, None, None, SearchFilters(edge_uuids=...), [group_id], 20, 0.6)` | 20 | 0.6 | **exact** (`reason=constrained`) |
| 4a | Invalidation candidates, BM25 half | `edge_operations.py` ~line 407 | `search(fact, [group_id], EDGE_HYBRID_SEARCH_RRF, SearchFilters())` → `edge_fulltext_search(..., 20)` | 20 | none | upstream fulltext (unchanged) |
| 4b | Invalidation candidates, cosine half | same | `edge_similarity_search(vector, None, None, SearchFilters(), [group_id], 20, 0.6)` | 20 | 0.6 | **ANN** |

Notes:

- `EDGE_HYBRID_SEARCH_RRF` uses `[bm25, cosine_similarity]` with the RRF reranker. `SearchConfig.limit` is 10, so each branch fetches `2 * limit = 20`. RRF uses `rank_const=1` and `reranker_min_score=0`. The final result is the top 10.
- `min_score` is the Neo4j `vector.similarity.cosine` score. That score is in the range 0 to 1: `(1 + cos) / 2`. Thus 0.6 is a raw cosine of 0.2, and 0.9 is a raw cosine of 0.8.
- The adapter routing in the table is measured, not inferred. `docs/eval/scripts/routing.py` called the real `IndexedReadSearch` in the pod. Logs: `kind=NODE mode=indexed candidates=300`, `kind=RELATIONSHIP mode=indexed candidates=400`, and `kind=RELATIONSHIP mode=exact reason=constrained` for `SearchFilters(edge_uuids=[])`. `SearchFilters()` gives no filter clauses for nodes or edges.
- `ingest_metrics.query_kind` puts searches 3b and 4b in the same kind `cosine_scan_edges`. The ingest report (`--days 0.2`) shows 1212 `cosine_scan_edges` queries and 1212 `fulltext` queries: two cosine queries for each extracted edge. Search 3b uses a range-index seek (see section 4), so nearly all of the `cosine_scan_edges` time comes from search 4b.
- Edge dedupe (a duplicate fact) uses search 3, which stays exact. ANN changes only node dedupe (search 1) and the invalidation candidates (search 4).

## 2. Dataset

- 200 RELATES_TO edges and 200 Entity nodes. Random sample (`ORDER BY rand()`) from items with `created_at` in the last 7 days, group `mykg`. The 7-day pool had 15,894 edges and 4,616 nodes. All had embeddings.
- Query vector: the stored `fact_embedding` or `name_embedding` of the item. The item itself is removed from all result lists. Each query requests `limit + 1` rows, then removes the item, then keeps `limit` rows.
- BM25 query: the stored `fact` text, through `lucene_sanitize` and the Graphiti group prefix `group_id:"mykg" AND (...)`.
- The sample file with texts and vectors stayed in the pod (`/tmp/vre/sample.json`). The repository has only UUIDs, scores, and timings: `docs/eval/data/2026-10-11-results.jsonl.gz`.

Limit of the method: the query vectors are vectors that are in the index. A new extracted fact or name is not in the index. The HNSW search can be a little less accurate for such vectors. Section 7 gives a guard for this risk.

## 3. Method

All queries ran in the memory pod (`memory-67bdf45c44-9dgtj`), one at a time, on one connection, with a 1 s pause after each item. The ingest CronJob ran at the same time. The run took 92 minutes for 400 items.

For each item:

1. Exact: the Graphiti 0.29.2 Cypher text (`MATCH ... WHERE group_id IN $group_ids WITH DISTINCT ..., vector.similarity.cosine(...) AS score WHERE score > $min_score ... ORDER BY score DESC LIMIT $limit`). It is built with the same Graphiti helper functions.
2. ANN: the adapter query text (`db.index.vector.queryNodes` or `queryRelationships`, group filter, exact cosine rerank, same `min_score` and limit). Candidates = `min(2000, max(100, limit * overfetch))`, plus 1 for the removed item. Overfetch values: 20 (current read setting), 40 (2x), 80 (4x), and 100 (adapter maximum).
3. Edges only: the BM25 query of search 4a, and the full search 2 → 3a → 3b chain for the item's endpoints.

`recall_eval.py verify` compared the replica query with the real adapter call on 6 items. The UUID sets were equal in 6/6. One edge had a different order because of two equal scores.

The latency of the adapter includes one `SHOW INDEXES` metadata query before each ANN query. That query took 7 ms mean. The real adapter calls took 72–145 ms (nodes 76–139 ms) at overfetch 20.

Scripts: `docs/eval/scripts/` (`recall_eval.py`, `analyze.py`, `details.py`, `routing.py`, `freshness.py`, `plans.py`, `explore.py`).

## 4. Results

### 4.1 Node dedupe (search 1): cosine only, k = 15, n = 200

| Overfetch | Candidates | Recall@15 mean | Min | Queries with recall 1.0 | Top-1 found | Recall of items with score ≥ 0.9 | Underfilled (exact fallback) | ANN p50 | ANN p90 |
|---|---|---|---|---|---|---|---|---|---|
| 20 | 300 | 0.9990 | 0.93 | 98.5% (197) | 100% | 631/631 | 0 | 0.072 s | 0.125 s |
| 40 | 600 | 0.9997 | 0.93 | 99.5% (199) | 100% | 631/631 | 0 | 0.100 s | 0.145 s |
| 80 | 1200 | 1.0000 | 1.00 | 100% (200) | 100% | 631/631 | 0 | 0.136 s | 0.178 s |
| 100 | 1500 | 1.0000 | 1.00 | 100% (200) | 100% | 631/631 | 0 | 0.157 s | 0.192 s |

Exact scan, sequential: p50 1.84 s, p90 1.90 s, mean 5.32 s, max 69.6 s.
Sum for 200 queries: exact 1064 s, ANN (overfetch 20) 27 s.

All misses (the ANN result had a lower-scored item instead):

| Item | Overfetch | Missed rank, score | Replacement rank, score |
|---|---|---|---|
| e2e14a14 | 20 | 3, 0.8114 | 15, 0.7273 |
| 7b98e905 | 20 | 4, 0.7257 | 15, 0.6991 |
| bbdaa71e | 20, 40 | 14, 0.7952 | 15, 0.7882 |

Node dedupe has no BM25 half. Thus the effective recall for node dedupe is the cosine recall.

### 4.2 Invalidation candidates (search 4b): cosine branch, k = 20, n = 200

| Overfetch | Candidates | Recall@20 mean | Min | Queries with recall 1.0 | Same, ties counted as found | Top-1 found | Recall of items with score ≥ 0.9 | Underfilled | ANN p50 | ANN p90 |
|---|---|---|---|---|---|---|---|---|---|---|
| 20 | 400 | 0.9985 | 0.90 | 97.5% (195) | 97.5% | 100% | 447/447 | 0 | 0.088 s | 0.141 s |
| 40 | 800 | 0.9990 | 0.95 | 98.0% (196) | 98.5% | 100% | 447/447 | 0 | 0.126 s | 0.165 s |
| 80 | 1600 | 0.9995 | 0.95 | 99.0% (198) | 99.0% | 100% | 447/447 | 0 | 0.166 s | 0.208 s |
| 100 | 2000 | 0.9998 | 0.95 | 99.5% (199) | 99.5% | 100% | 447/447 | 0 | 0.190 s | 0.230 s |

Exact scan, sequential: p50 6.27 s, p90 53.4 s, mean 19.1 s, max 209.7 s.
Sum for 200 queries: exact 3827 s, ANN (overfetch 20) 29.5 s.

All misses:

| Item | Overfetch values with the miss | Missed rank, score | Replacement rank, score |
|---|---|---|---|
| 1d9df5c4 | 20 | 4, 0.7950 | 20, 0.7823 |
| fb67e9a2 | 20 | 8, 0.7652 and 17, 0.7487 | 19, 0.7453 and 20, 0.7450 |
| fb67e9a2 | 40, 80, 100 | 17, 0.7487 | 20, 0.7453 |
| 58c99f2a | 20 | 9, 0.7595 | 20, 0.7367 |
| 33391e49 | 20, 40 | 16, 0.7617 | 20, 0.7591 |
| bc5c0c29 | 20, 40, 80 | 19, 0.7291 | 20, 0.7271 |
| b5243572 | 40 | 20, 0.7423 | 20, 0.7423 (equal score; not a real miss) |

The lowest score in any exact edge result was 0.721. The median score at rank 20 was 0.809. Thus `min_score = 0.6` never limited the result size, and no query was underfilled.

### 4.3 Effective hybrid recall (search 4, final top 10 after RRF)

The analysis applies the Graphiti RRF (`rank_const=1`, list order BM25 then cosine) to the measured BM25 list and to the exact or ANN cosine list. It compares the final top 10.

| Overfetch | Cosine misses (all ranks) | Misses that BM25 top 20 also returns | Misses in the exact final top 10 | Final top-10 recall mean | Queries with final recall 1.0 | Min |
|---|---|---|---|---|---|---|
| 20 | 6 | 1 | 1 | 0.9985 | 98.5% (197) | 0.90 |
| 40 | 4 | 1 | 0 | 0.9995 | 99.5% (199) | 0.90 |
| 80 | 2 | 1 | 0 | 0.9995 | 99.5% (199) | 0.90 |
| 100 | 1 | 0 | 0 | 1.0000 | 100% (200) | 1.00 |

Details:

- At overfetch 20, two final sets changed because of ANN. In 1d9df5c4, the cosine rank-4 item left the final top 10. In fb67e9a2, the missed rank-8 item moved other ranks, and one item left the final top 10.
- At overfetch 40 and 80, the one remaining change (4942a9a8) is not an ANN miss. The ANN and exact cosine sets are equal. The exact result has two equal scores, and their order changed. The exact scan has no stable order for equal scores either. Thus the real final recall is 1.0 for 200/200 at overfetch 40, 80, and 100.
- BM25 rarely recovers a cosine miss (1 of 6 at overfetch 20). The two branches find mostly different items.
- BM25 latency: p50 0.065 s, p90 0.129 s.

### 4.4 Related edges (searches 2 and 3), exact path

- Edges between the same endpoints (not counting the item): mean 2.67, max 78. 139 of 200 items had none.
- `get_between_nodes`: p50 4.4 ms, p90 8.0 ms, max 44 ms.
- Cosine with `edge_uuids` filter (3b): p50 7.9 ms, p90 32.9 ms, max 171 ms. The plan is `DirectedRelationshipIndexSeek` on `relation_uuid`, also for an empty list. It is not a scan.
- BM25 with `edge_uuids` filter (3a): p50 40 ms, p90 71 ms, max 1.4 s.

Search 3 is cheap. Keep it exact. The adapter already does this.

### 4.5 Index freshness

ANN is only correct if the index contains the newest data. For the 40 newest edges (age 3–9 minutes) and the 40 newest nodes (age 4.5–49 minutes), an ANN query with the item's own vector found the item in 40/40 and 40/40 (`freshness.py`). Neo4j updates vector indexes in the commit. Changes inside the same transaction are not visible to the index. Graphiti writes each episode after its searches, so this does not change ingest behavior. Graphiti handles items of the same episode with `existing_nodes_override` and `existing_edges_override` in both modes.

### 4.6 Ingest load at the same time

`ingest_report.py --days 0.2` at the end of the run: 47 notes ok, note wall p50 321 s, p90 665 s. Neo4j is 63.8% of note time. `cosine_scan_edges` is 78.3% of Neo4j time (1212 queries, mean 67 s, max 234 s). `cosine_scan_nodes` is 19.7% (578 queries, mean 36 s). Concurrent scans on 2 CPUs explain the difference from the sequential means above.

## 5. Interpretation for ingest correctness

- A missed node candidate can cause a duplicate entity only if the missed item is a real duplicate. In all runs, the top-1 candidate was found, and all candidates with score ≥ 0.9 (raw cosine ≥ 0.8) were found. The node misses had scores 0.73–0.81 (raw cosine 0.45–0.62).
- A missed invalidation candidate can cause a missed fact invalidation. At overfetch 100, the final hybrid top 10 was equal to the exact result in 200/200 queries.
- The exact path is not fully deterministic either. Equal scores can change order and can change the final top 10 (item 4942a9a8). This is the same size of effect as the remaining ANN differences at overfetch 40–100.
- 0/400 ANN results were underfilled. Group `mykg` has 100% of the data now. If a second, large group comes into the same database, ANN recall for a small group can drop with no underfill. The fallback protects only when fewer than `limit` items survive the group filter.

## 6. Latency effect

At overfetch 100, one ANN search costs 0.16–0.19 s p50 and 0.19–0.23 s p90 when it runs alone. The exact scans cost 1.8 s (nodes) and 6.3 s (edges) p50 when they run alone, and 36 s and 67 s mean under ingest concurrency. ANN removes nearly all of the 64% Neo4j share of note time. The LLM share (34%) then controls the note time. This is an estimate. Measure it after enablement with `ingest_report.py`.

## 7. Recommendation

1. Enable ANN for ingest searches 1 (node dedupe) and 4b (invalidation cosine). Use `overfetch=100` and `max_candidates=2000`. This gives 2000 edge candidates and 1500 node candidates. Overfetch 80 is also sufficient for nodes, but one value for both kinds is simpler. The extra cost against overfetch 20 is about 0.1 s for each query.
2. Keep searches 2 and 3 exact. They are cheap, and they are the edge dedupe path. The adapter already routes search 3b to exact.
3. Do not reuse `GRAPH_VECTOR_OVERFETCH=20` for ingest. Add a separate ingest setting, for example `INGEST_VECTOR_SEARCH=1` with `INGEST_VECTOR_OVERFETCH=100`. Install the adapter on the ingest driver in `ingest.build_graphiti` only when that setting is on. This keeps a one-variable rollback.
4. Add a recall guard, because the dataset used stored vectors (section 2). Two options:
   - a. Shadow audit (preferred): for a small random share of ingest searches (for example 2%), also run the exact query after the note, and record the recall in `DipinkIngestMetric`. Alert when any final result differs. This adds a little load and gives live evidence with real new vectors.
   - b. A daily read-only job with `recall_eval.py` on 50 new items. Alert below recall 1.0 for scores ≥ 0.9, or below 0.99 mean.
5. Do the evaluation again if a second large group, a new embedding model, or an index option change (for example `vector.quantization.enabled`) comes into use.

## 8. Reproduce

```sh
P=$(kubectl -n graphiti get pod -l app=memory -o name | head -1)
kubectl -n graphiti exec "$P" -- mkdir -p /tmp/vre
kubectl -n graphiti cp docs/eval/scripts/recall_eval.py "${P#pod/}":/tmp/vre/recall_eval.py
kubectl -n graphiti exec "$P" -- python3 /tmp/vre/recall_eval.py sample --edges 200 --nodes 200 --days 7
kubectl -n graphiti exec "$P" -- python3 /tmp/vre/recall_eval.py verify
kubectl -n graphiti exec "$P" -- python3 /tmp/vre/recall_eval.py run --interleave --pause 1
kubectl -n graphiti exec "$P" -- cat /tmp/vre/results.jsonl > results.jsonl
python3 docs/eval/scripts/analyze.py results.jsonl
python3 docs/eval/scripts/details.py results.jsonl
```

The run sends about 2,600 read queries, one at a time. About 400 of them are full exact scans. Run it outside heavy ingest periods when possible.

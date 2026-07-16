# graphiti — the temporal knowledge graph

One image, four roles: note ingest, the graph MCP server, and the memory
maintenance loops. Built on [Graphiti](https://github.com/getzep/graphiti)
(`graphiti-core`) over Neo4j.

## ingest.py

Turns wiki source notes into graph episodes. Each note = one `add_episode`:
episode **name = note slug** (provenance — every extracted fact traces to its
note) and **reference_time parsed from the slug's timestamp prefix** (the
bitemporal timeline is real; backfilled notes land at their original capture
time, so fact supersession orders correctly).

Modes (`INGEST_MODE`): `cron` (resumable bounded batch — the scheduler mode),
`status`, `ingest` (one-shot), `query`.

Reliability properties of `cron` mode:

- **Idempotent + crash-safe.** A note is "done" iff its episode node exists AND
  has ≥1 entity edge; half-written crash victims are detach-deleted and
  re-ingested.
- **Oldest-first**, bounded by `BATCH_SIZE` per tick.
- **Circuit breaker**: 3 consecutive failures abort the batch (quota'd/down
  LLM); the next tick retries the same notes. Progress is never lost.
- **`CONCURRENCY` must stay 1.** Graphiti's edge invalidation is a non-atomic
  read-modify-write; concurrent same-entity writes silently lose supersession.

LLM wiring: OpenAI by default (`OPENAI_API_KEY` + `LLM_MODEL`); set
`LLM_BASE_URL`/`LLM_API_KEY` for any OpenAI-compatible endpoint — that path
uses a client with a compact-schema prompt shim for models that echo JSON
Schema instead of instantiating it. Embeddings are always OpenAI
`text-embedding-3-small`.

Also carries `patch_community_clustering()` — fixes for upstream community
building (bulk adjacency query instead of per-entity round-trips, an iteration
cap on the oscillating label-propagation loop, bounded LLM concurrency).

## mcp/server.py

The read surface. Tools: `graph_answer` (server-side distilled answer — the
default for factual questions), `graph_search` (facts + community + entities +
source excerpt + wiki-mcp semantic fusion hits), `graph_get_note`,
`graph_entity`, `graph_current_facts`, `graph_changes`. Plain HTTP mirrors at
`/api/answer`, `/api/search`, `/api/metrics`, `/health`.

Notable internals:

- **Fusion**: `graph_search` merges wiki-mcp's semantic page/note hits into the
  packet (episodes are BM25-only in graphiti; the wiki's embeddings cover that
  blind spot). Best-effort — if wiki-mcp is down the packet just lacks them.
- **Distiller**: one plain chat completion per `graph_answer`, temperature 0,
  answering **only from the packet** — `not_found` + `escalate` otherwise (the
  no-hallucination property the daily healthcheck probes). Real answers are
  cached for `ANSWER_CACHE_TTL` (default 1h); `not_found`/errors never are.
- **Provenance resolution**: search edges carry episode UUIDs, not names; a
  single batched Cypher resolves them to note slugs.

## loops/

| Job | Cadence | What |
|---|---|---|
| `memory_alerts.py` | 30 min | dead-man checks: ingest freshness, community age, MCP `/health`. Exit 1 = alert. |
| `memory_healthcheck.py` | daily | deep end-to-end: write path (canary if quiet), per-note ingest verification, curation backlog/liveness/lag, MCP exposure incl. `graph_answer` correctness + no-hallucination guard, index freshness, usage counts. Failures drop a note into the inbox. |
| `memory_gaps.py` | weekly | mines both MCPs' query logs for zero-hit / low-relevance / not-found queries and files a "memory gaps" report note — which gets ingested: the memory knows what it's missing. |
| `build_communities.py` | weekly | entity resolution (below) then community rebuild, with unbounded→bounded fallback and transient-error retries. |
| `entity_resolution.py` | (inside rebuild) | merges alias entities ("MyApp" vs "myapp.example.com") — string candidates, LLM confirmation, capped merges, DRY_RUN honored. |
| `contradiction_janitor.py` | monthly | LLM audit of the densest entities' current facts; invalidates mutually-exclusive older facts. Run with `DRY_RUN=1` first. |

## Key env

`NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`, `OPENAI_API_KEY`,
`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`, `GROUP_ID` (graph namespace →
Neo4j database), `NOTES_ROOT` + `INBOX_ROOTS` (where notes live in the
checkout), `BATCH_SIZE`, `WIKI_MCP_BASE` (fusion + loops).

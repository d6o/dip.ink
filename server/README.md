# server — the memory server

One process, one MCP endpoint (`/mcp`), all tools. One image, three roles:

| Role | Command |
|---|---|
| MCP server | `python server.py` (the default CMD) |
| Note ingest | `INGEST_MODE=cron python ingest.py` |
| Memory loops | `python loops/<job>.py` |

## Layout

- `core.py` — the shared FastMCP instance, transport security, and the single
  query-instrumentation JSONL both tool families write to.
- `wiki.py` — the markdown-wiki side: index (embeddings + degraded fallback),
  git clone/refresh, `wiki_search` / `wiki_get` / `wiki_backlinks` /
  `wiki_note_drop`, plus `/api/search`, `/api/page`, `/api/backlinks`,
  `/api/reindex`, `/live`.
- `graph.py` — the Graphiti side: `graph_answer` (distilled answers with
  deterministic provenance grounding), `graph_search` (rich packet with
  in-process wiki fusion), `graph_get_note`, `graph_entity`,
  `graph_current_facts`, `graph_changes`, plus `/api/answer`,
  `/api/graph/search`, `/api/graph/health`.
- `status.py` — shared operational snapshot for `memory_status` / `/api/status`
  (component readiness, index age, inbox/deferred/blocked, review queue,
  ingest pending/partial/lag, communities, query summary, build/version).
  Degrades component-by-component; never returns note bodies, query text, or
  credentials.
- `observability.py` — Prometheus text exposition at `/metrics` (bounded-
  cardinality tool counters + status gauges). Distinct from the JSON
  `/api/metrics` query-log tail used by the gaps miner.
- `server.py` — assembles everything onto one Starlette app; owns the combined
  `/health` (wiki index state + `graph_ready`), `/api/status`, `/metrics`, and
  `/api/metrics`.
- `ingest.py` — notes → Graphiti episodes. Resumable, crash-safe,
  circuit-breaker; **CONCURRENCY must stay 1** (graphiti's edge invalidation is
  a non-atomic read-modify-write). Episode name = note slug (provenance);
  reference_time = the slug's timestamp prefix (real bitemporal timeline).
  Explicit completion metadata + content hash; zero-fact notes can be complete;
  blocked notes are excluded from discovery.
- `loops/` — the self-maintenance jobs (see table below).

## wiki side behavior

- **Startup**: binds HTTP immediately; a supervised background thread clones
  (or refreshes) the wiki repo, scans page metadata, and embeds changed pages.
  Cached unchanged vectors are served while new ones embed; provider outages
  leave a **degraded but searchable** index (explicit lexical fallback) rather
  than a dead server. Bounded retry/backoff, then a normal 5-min refresh loop.
- **Embeddings**: OpenAI `text-embedding-3-small` (1536-dim) by default; cache
  keys hash the exact embedding input (title + `index-description` + body).
  Legacy body-hash vectors are accepted once and rewritten without a forced
  full historical re-embed. `WIKI_MCP_EMBED_PROVIDER=fastembed` for a $0 local
  alternative (`pip install fastembed`).
- **Note drops** are validated (slug regex, size caps, reserved names),
  idempotent across both the live inbox and archived source-note paths,
  serialized against the git working tree, and rolled back if the push fails.
  There is **no automatic secret scanning** — agents must never submit secrets.

## graph side behavior

- **Fusion**: `graph_search` merges the wiki index's semantic hits into its
  packet via a direct in-process call (episodes are BM25-only in graphiti; the
  wiki's embeddings cover that blind spot). Best-effort — an unready index
  just means no `semantic_notes`.
- **Distiller** (`graph_answer`): one plain chat completion, temperature 0,
  answering **only from the packet** — invented provenance is discarded, and
  unsupported answers return `not_found` + `escalate` (the no-hallucination
  property the daily healthcheck probes). Real answers are cached for
  `ANSWER_CACHE_TTL` (default 1h) and invalidated by ingest watermarks;
  `not_found`/errors never are.
- **Provenance**: search edges carry episode UUIDs; a single batched Cypher
  resolves them to note slugs so every fact cites its source note.
- **Isolation**: `GROUP_ID` is a Graphiti **property partition**, not a Neo4j
  database name. Direct Cypher and searches are scoped to it.

## loops/

| Job | Cadence | What |
|---|---|---|
| `memory_alerts.py` | 30 min | dead-man checks: pending note→episode lag (not wall-clock quiet), community age, server `/health`. Quiet memory with zero pending is healthy. Exit 1 = alert. |
| `memory_healthcheck.py` | daily | deep end-to-end: write path (canary if quiet), per-note ingest verification, curation backlog/liveness/lag, server exposure incl. `graph_answer` correctness + no-hallucination guard, index freshness, usage counts. Failures drop a note into the inbox. |
| `memory_gaps.py` | weekly | mines the query log for zero-hit / low-relevance / not-found queries and files a "memory gaps" report note — which gets ingested: the memory knows what it's missing. |
| `build_communities.py` | weekly | entity resolution (below) then community rebuild, with unbounded→bounded fallback and transient-error retries. |
| `entity_resolution.py` | (inside rebuild) | merges alias entities ("MyApp" vs "myapp.example.com") — string candidates, LLM confirmation, capped merges, DRY_RUN honored. |
| `contradiction_janitor.py` | monthly | LLM audit of the densest entities' current facts; report-only by default (`DRY_RUN=1`). |

## Key env

| Var | Default | |
|---|---|---|
| `WIKI_REPO_URL` / `WIKI_REPO_TOKEN` / `WIKI_REPO_USER` | — / — / `token` | the private memory repo (HTTPS + basic-auth token) |
| `OPENAI_API_KEY` | — | embeddings + default extraction/distillation |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_MODEL_LADDER` | — / — / `gpt-4.1-mini` / inherits | OpenAI-compatible extraction endpoint + ordered fallback ladder |
| `DISTILL_*` | inherits extraction | optional independent distiller base URL / key / model / ladder |
| `WIKI_MCP_EMBED_PROVIDER` | `openai` | `openai` or `fastembed` |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | `bolt://neo4j:7687` / `neo4j` / — | graph storage |
| `NEO4J_MAX_POOL` / `NEO4J_ACQ_TIMEOUT` | bounded defaults | explicit driver pool (no post-construction client swap) |
| `CACHE_DIR` | — | persist embeddings + the query-metrics log |
| `MCP_METRICS_PATH` | `auto` | path for query metrics JSONL, or `off` |
| `MCP_ALLOWED_HOSTS` | `localhost,127.0.0.1,memory` | add your ingress hostname (DNS-rebinding protection) |
| `GROUP_ID` | `main` | Graphiti group property partition (not a Neo4j database) |
| `NOTES_ROOT` / `INBOX_ROOTS` | `/notes/wiki/sources/notes` / `/notes/notes` | where notes live in the ingest checkout |
| `ANSWER_CACHE_TTL` / `GRAPH_FUSION` | `3600` / `1` | answer cache TTL; wiki fusion toggle |

## HTTP surface

| Path | Purpose |
|---|---|
| `/mcp` | Streamable-HTTP MCP transport (all tools) |
| `/live` | process liveness (cheap; must stay responsive under load) |
| `/health` | combined readiness (wiki index + `graph_ready`) |
| `/api/status` | same schema as `memory_status` |
| `/metrics` | Prometheus text metrics |
| `/api/metrics` | JSON query-log tail (gaps miner) |
| `/api/search`, `/api/page`, `/api/backlinks`, `/api/reindex` | wiki HTTP |
| `/api/answer`, `/api/graph/search`, `/api/graph/health` | graph HTTP |

Blocking search/reindex/metrics work runs off the event loop so `/live` stays
responsive.

## Run locally

```sh
pip install -r requirements.txt
WIKI_ROOT=/path/to/wiki OPENAI_API_KEY=sk-... NEO4J_PASSWORD=... python server.py
python -m unittest discover -s tests -p 'test_*.py' -v
# Real Neo4j 5.26.2 integration (opt-in):
# NEO4J_INTEGRATION=1 NEO4J_URI=bolt://127.0.0.1:7687 NEO4J_PASSWORD=... \
#   python -m unittest discover -s tests -p 'test_*integration*.py' -v
```

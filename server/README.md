# server — the memory server

One process, one MCP endpoint (`/mcp`), all ten tools. One image, three roles:

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
- `graph.py` — the Graphiti side: `graph_answer` (distilled answers),
  `graph_search` (rich packet with in-process wiki fusion), `graph_get_note`,
  `graph_entity`, `graph_current_facts`, `graph_changes`, plus `/api/answer`,
  `/api/graph/search`, `/api/graph/health`.
- `server.py` — assembles both onto one Starlette app; owns the combined
  `/health` (wiki index state + `graph_ready`) and `/api/metrics`.
- `ingest.py` — notes → Graphiti episodes. Resumable, crash-safe,
  circuit-breaker; **CONCURRENCY must stay 1** (graphiti's edge invalidation is
  a non-atomic read-modify-write). Episode name = note slug (provenance);
  reference_time = the slug's timestamp prefix (real bitemporal timeline).
- `loops/` — the self-maintenance jobs (see table below).

## wiki side behavior

- **Startup**: binds HTTP immediately; a supervised background thread clones
  (or refreshes) the wiki repo, scans page metadata, and embeds changed pages.
  Cached unchanged vectors are served while new ones embed; provider outages
  leave a **degraded but searchable** index (explicit lexical fallback) rather
  than a dead server. Bounded retry/backoff, then a normal 5-min refresh loop.
- **Embeddings**: OpenAI `text-embedding-3-small` (1536-dim) by default; only
  changed pages (content-hash) are re-embedded. Vectors persist to `CACHE_DIR`
  so restarts don't re-pay the embed cost. `WIKI_MCP_EMBED_PROVIDER=fastembed`
  for a $0 local alternative (`pip install fastembed`).
- **Note drops** are validated (slug regex, size caps, reserved names),
  idempotent (payload-hash dedup across retries), serialized against the git
  working tree, and rolled back if the push fails.

## graph side behavior

- **Fusion**: `graph_search` merges the wiki index's semantic hits into its
  packet via a direct in-process call (episodes are BM25-only in graphiti; the
  wiki's embeddings cover that blind spot). Best-effort — an unready index
  just means no `semantic_notes`.
- **Distiller** (`graph_answer`): one plain chat completion, temperature 0,
  answering **only from the packet** — `not_found` + `escalate` otherwise (the
  no-hallucination property the daily healthcheck probes). Real answers are
  cached for `ANSWER_CACHE_TTL` (default 1h); `not_found`/errors never are.
- **Provenance**: search edges carry episode UUIDs; a single batched Cypher
  resolves them to note slugs so every fact cites its source note.

## loops/

| Job | Cadence | What |
|---|---|---|
| `memory_alerts.py` | 30 min | dead-man checks: ingest freshness, community age, server `/health`. Exit 1 = alert. |
| `memory_healthcheck.py` | daily | deep end-to-end: write path (canary if quiet), per-note ingest verification, curation backlog/liveness/lag, server exposure incl. `graph_answer` correctness + no-hallucination guard, index freshness, usage counts. Failures drop a note into the inbox. |
| `memory_gaps.py` | weekly | mines the query log for zero-hit / low-relevance / not-found queries and files a "memory gaps" report note — which gets ingested: the memory knows what it's missing. |
| `build_communities.py` | weekly | entity resolution (below) then community rebuild, with unbounded→bounded fallback and transient-error retries. |
| `entity_resolution.py` | (inside rebuild) | merges alias entities ("MyApp" vs "myapp.example.com") — string candidates, LLM confirmation, capped merges, DRY_RUN honored. |
| `contradiction_janitor.py` | monthly | LLM audit of the densest entities' current facts; invalidates mutually-exclusive older facts. Run with `DRY_RUN=1` first. |

## Key env

| Var | Default | |
|---|---|---|
| `WIKI_REPO_URL` / `WIKI_REPO_TOKEN` / `WIKI_REPO_USER` | — / — / `token` | the private memory repo (HTTPS + basic-auth token) |
| `OPENAI_API_KEY` | — | embeddings + default extraction/distillation |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | — / — / `gpt-4.1-mini` | alternative OpenAI-compatible extraction endpoint |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | `bolt://neo4j:7687` / `neo4j` / — | graph storage |
| `CACHE_DIR` | — | persist embeddings + the query-metrics log |
| `MCP_ALLOWED_HOSTS` | `localhost,127.0.0.1,memory` | add your ingress hostname (DNS-rebinding protection) |
| `GROUP_ID` | `main` | graph namespace → Neo4j database |
| `NOTES_ROOT` / `INBOX_ROOTS` | `/notes/wiki/sources/notes` / `/notes/notes` | where notes live in the ingest checkout |

## Run locally

```sh
pip install -r requirements.txt
WIKI_ROOT=/path/to/wiki OPENAI_API_KEY=sk-... NEO4J_PASSWORD=... python server.py
python -m unittest discover -s tests
```

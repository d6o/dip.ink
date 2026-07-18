# dip.ink

**A self-maintaining memory system for AI agents.** Your agents write down what they learn; a scheduled LLM curator files it into a markdown wiki; a temporal knowledge graph ingests every note; and every future agent session can ask the memory questions and get direct, provenance-backed answers — including "what changed since last week" and "what was true before it changed".

This is not a note-taking app. There is no UI. The write path and the read path are both **agents**: it's memory infrastructure that your Claude Code / Pi / Codex sessions plug into over MCP.

```
                you, working with agents
                          │
         agents capture   │   agents query
                          ▼
            ONE MCP SERVER ("memory", :8080/mcp)
            ┌────────────────────────────────┐
            │ wiki_note_drop   graph_answer    │
            │ wiki_search      graph_search    │
            │ wiki_get         graph_changes   │
            │ wiki_backlinks   graph_get_note  │
            │ memory_status                    │
            └───┬───────────────────┬────────┘
     writes+reads │                   │ reads
                  ▼                   ▼
   ┌─────────────────┐      ┌────────────────┐
   │ private git repo │      │ Graphiti+Neo4j │
   │  notes/  (inbox) │────►│ 15-min ingest  │
   │  wiki/   (pages) │      │ every note →   │
   └───────┬─────────┘      │ temporal facts │
           │                └────────────────┘
   ┌───────┴───────┐
   │ CURATOR (CI)  │
   │ hourly agent  │
   │ notes → pages │
   └───────────────┘
```

## Why this shape

- **Notes are the event log.** Agents drop timestamped source notes into a git inbox. Notes are immutable once filed; git is the backup and the history. Everything downstream is a rebuildable projection.
- **A wiki, not a RAG dump.** An hourly curator agent promotes notes into a small, interlinked markdown wiki (entities, concepts, decisions, syntheses) with a strict linted schema. Knowledge gets *compiled once*, not re-derived per query.
- **A temporal graph, not just embeddings.** [Graphiti](https://github.com/getzep/graphiti) extracts atomic facts from every note into Neo4j with **bitemporal validity**: when a new note contradicts an old fact, the old fact is superseded, not deleted. The memory can answer "what's true now", "what did I believe then", and "what changed lately".
- **Answers, not search results.** `graph_answer` assembles the fat retrieval packet server-side and distills it with one LLM call into `{answer, confidence, sources, escalate}` — ~150 tokens instead of ~1,800 per lookup. Every fact traces back to its source note.
- **The system watches itself.** A daily deep healthcheck exercises the whole pipeline (write → ingest → curate → index → answer, including a no-hallucination probe). A weekly job mines the query logs for questions the memory *couldn't* answer and files them back as a note — the memory learns what it's missing.

## What you need

| Requirement | Notes |
|---|---|
| **An OpenAI API key** | Embeddings (`text-embedding-3-small`) + by default also graph extraction and answer distillation. One key runs everything. Any OpenAI-compatible endpoint can replace the extraction/distillation side (`LLM_BASE_URL` / model ladders). Graphiti's own embeddings still require an OpenAI key even when extraction uses another provider. |
| **A private git repo** | The memory itself — notes + wiki pages. GitHub free private repos work; so does Gitea/GitLab. You get it from `template/` below. |
| **A docker host** | Runs Neo4j, the **one** memory MCP server, and the ingest/health loops via `docker compose`. 2 GB RAM is enough to start. k8s manifests are provided for a production setup. |
| **A CI runner for the curator** | The hourly curation agent runs as a GitHub Actions workflow on the wiki repo (included). Any scheduler that can run a container works. Needs an LLM key for the agent ([Pi](https://github.com/badlogic/pi-mono) by default; bring your own model). |

Steady-state cost is dominated by the extraction + curator LLM calls. With a small model (e.g. `gpt-4.1-mini`) and a personal-use note volume (tens of notes/day) this is cents-per-day territory; embeddings are negligible.

## Quickstart

### 1. Create your private memory repo

```sh
# copy the template into a fresh private repo
git clone https://github.com/d6o/dip.ink.git
cp -r dip.ink/template my-memory && cd my-memory
git init -b main && git add -A && git commit -m "init memory"
gh repo create my-memory --private --source . --push
```

The template contains the wiki skeleton (`wiki/`, `notes/`, `raw/`), the schema contract (`AGENTS.md` — the open agent-context convention that Pi loads natively), the lint/index/rotate/distill scripts, the curator prompts, a small bootstrap source note so first-run healthchecks have something to answer, and the CI workflows.

Then, in the repo's GitHub settings:
- **Secrets → Actions**: add `PI_API_KEY` (the LLM key the curator agent uses — your OpenAI key works).
- Optionally **Variables → Actions**:
  - `PI_PROVIDER` / `PI_MODEL` to pick the curator's model (defaults: `openai` / `gpt-4.1-mini`)
  - `PI_MODELS_JSON` for a custom OpenAI-compatible provider definition (Pi's `models.json` payload)
- **Actions → General**: allow workflows "Read and write permissions" (the curator pushes commits).

The three workflows (`curator` hourly, `synthesis` weekly, `reviewqueue` daily) activate on push. They share one concurrency group (`memory-repo-writer`) so they never race on the same repo. The curator is a **required part of the system**, not an optional extra — without it, notes pile up in the inbox and the wiki never forms.

### 2. Deploy the memory stack

Public runtime images are **immutable tags**, not moving `latest`. v0.1.2 is the first public release pin.

```sh
cd dip.ink
cp .env.example .env
# fill in: OPENAI_API_KEY, WIKI_REPO_URL, WIKI_REPO_TOKEN, NEO4J_PASSWORD
docker compose up -d
```

That starts:

| Service | Port | What |
|---|---|---|
| `memory` | 8080 | **the one MCP server** — all `wiki_*` + `graph_*` + `memory_status` tools at `/mcp` |
| `neo4j` | 7474/7687 (localhost) | graph storage |
| `ingest` | — | every 15 min: pull the repo, ingest new notes into the graph |
| `communities` | — | weekly: entity resolution + community rebuild |
| `gaps` | — | weekly: mine the query log for memory gaps |
| `alerts` | — | every 30 min: dead-man checks (server, ingest lag, communities) |
| `healthcheck` | — | daily: deep end-to-end pipeline verification |
| `contradiction-janitor` | — | monthly: report-only contradiction analysis by default |

Compose and Kubernetes expose the same intended maintenance jobs and the same supported configuration surface (model ladders, distiller overrides, wiki embed provider, metrics path, pool sizes). Documented exceptions are intentional: the curator runs in the memory repo's CI (not Compose/k8s), and `PI_MODELS_JSON` is a Pi-runner variable, not a memory-server env.

Verify:

```sh
curl -s localhost:8080/live | jq .
curl -s localhost:8080/health | jq '.ready, .graph_ready'
curl -s localhost:8080/api/status | jq .
curl -s localhost:8080/metrics | head
curl -s 'localhost:8080/api/search?q=hello&k=3' | jq .
```

> **Exposing beyond localhost:** the MCP server has **no authentication** — the network is the perimeter. Put it behind a VPN/tailnet or an authenticating reverse proxy; do not expose it to the public internet. If you serve it via a hostname, add it to `MCP_ALLOWED_HOSTS`.

### 3. Set up your agents — paste one message

Then paste this into your agent (Claude Code, Pi, ...):

> Retrieve and follow the instructions at:
> https://raw.githubusercontent.com/d6o/dip.ink/main/INSTALL_FOR_AGENTS.md
>
> My memory server is at http://localhost:8080

The agent installs four things into its own environment ([`INSTALL_FOR_AGENTS.md`](./INSTALL_FOR_AGENTS.md) has the per-runtime steps if you'd rather do it by hand):

1. **The tools** — the memory MCP server registration (or the native Pi extension).
2. **The usage contract** ([`AGENTS.md`](./AGENTS.md)) into its global instructions: *search the memory before answering anything operator-specific; capture every non-obvious learning as you go*. Without this the memory silently decays — agents answer from stale training data and never write anything back.
3. **`/recordnotes`** — a command that reviews the session and saves durable learnings via `wiki_note_drop`.
4. **The compaction gate** — a hook that blocks context compaction until `/recordnotes` has run recently for that working directory (on Pi it also wraps `/exit` so graceful exits flush notes first). Compaction is where session memory dies; the hook makes capture happen *before* the loss.

After that your agents will start reading/recording information on their own. If you want to make sure all learnings in a session were stored, just call `/recordnotes`.

### 4. Watch the loop run (first-run expectations)

A fresh template already contains one bootstrap source note under `wiki/sources/notes/`. After `docker compose up -d`:

1. Within ~15 minutes the `ingest` job turns that bootstrap note (and any agent note-drops) into graph episodes.
2. `memory_status` / `/api/status` should show wiki readiness, graph readiness, inbox/deferred/blocked counts, and ingest lag.
3. The default healthcheck probe (`ANSWER_PROBE`, defaulting to a question about the note inbox) should return high/medium confidence with valid provenance once ingest has completed.
4. An agent finishing real work drops a note: `wiki_note_drop("traefik-timeout-fix", "...")` → committed and pushed to your repo's `notes/` inbox.
5. Within the hour, the `curator` workflow promotes it into wiki pages (or updates existing ones), moves the note to `wiki/sources/notes/YYYY/MM/DD/`, and pushes. FLAGged or already-ingested folders go to `notes/.blocked/` instead of blocking the live queue.
6. Any future session asks: *"how did I fix the traefik timeout?"* → `graph_answer` returns the fix, its confidence, and the source-note slug — which `graph_get_note` can fetch verbatim.
7. If the note contradicted an older fact ("timeout is 30s" → "timeout is 600s"), the old fact is marked superseded — `graph_answer` reports the current value and can mention the old one in `superseded_note`.

## The MCP tools

One server, two tool families, plus an operational status tool.

**wiki** (curated pages — the "compiled" layer):

| Tool | Use |
|---|---|
| `wiki_search(query, k)` | semantic search over pages; lexical fallback during provider outages |
| `wiki_get(name)` | full page body + inbound/outbound wikilinks |
| `wiki_backlinks(name)` | what references X? |
| `wiki_note_drop(slug, note_md, attachments?)` | **the write path** — file a note into the inbox |

**graph** (the temporal graph — the "facts" layer):

| Tool | Use |
|---|---|
| `graph_answer(question)` | **use first for factual questions** — distilled `{answer, confidence, sources, escalate}` |
| `graph_search(query, k)` | rich packet: facts (+ provenance + validity), community summary, entities, source excerpt, semantic note hits |
| `graph_changes(subject, since_days)` | temporal diff: new facts + superseded facts — resuming a project after time away is one call |
| `graph_current_facts(subject)` | what's true NOW (superseded excluded) |
| `graph_entity(name)` | a known entity's summary + current facts |
| `graph_get_note(slug)` | provenance fetch: the original source note behind any fact |

**status** (operations — bounded, non-secret):

| Tool / HTTP | Use |
|---|---|
| `memory_status` / `GET /api/status` | one operational snapshot: component readiness, index age, inbox/deferred/blocked counts, review queue, ingest pending/partial/lag, communities, recent query summary, build/version |
| `GET /metrics` | Prometheus text exposition of the same core gauges plus tool counters (see Observability) |
| `GET /api/metrics` | JSON query-log tail used by the weekly gaps miner (not the Prometheus endpoint) |

The server also exposes plain HTTP (`/api/search`, `/api/answer`, `/live`, `/health`, ...) for non-MCP clients and the self-maintenance loops. Both tool families write to one query-instrumentation log, which the weekly gaps miner reads back.

Why one server? `graph_search` fuses the wiki's semantic hits into its packet — in-process, no HTTP hop — and agents register one URL, operators run one container, the healthcheck probes one endpoint.

## The curator

The inbox → wiki promotion is done by a real agent session, headless, on a schedule. Design choices that made it reliable:

- **Small fresh batches.** Each run drains the inbox in sub-batches of 4 notes, each in a fresh agent session (no context bloat, no compounding confusion). Every successful sub-batch is validated (`wikilint` → `wikiindex` → `logrotate` → `wikidistill`), committed, rebased, and pushed *before* the next starts — a later failure can't lose earlier work.
- **Optimistic with receipts.** The curator edits without asking. Anything genuinely needing human judgment (substantial rewrites, contradictions) lands as a one-line bullet in `wiki/Curator review queue.md` — the only place that says "look at this". A daily `reviewqueue` pass auto-resolves entries that later evidence settles.
- **Never delete.** Status changes and migrations add dated subsections; obsolete claims get dated supersede notes. The wiki is an accumulating record, which is exactly what makes the graph's bitemporal layer work.
- **No automatic secret scanning.** There is no server-side or curator secret detector. Security is agent discipline plus a private repo and a gated network: agents must never submit credentials/tokens/passwords to `wiki_note_drop`; reference secret-manager paths only. If a secret is committed, treat it as compromised and rotate it — the pipeline will not make it safe after the fact.
- **Empty-inbox preflight.** The supervisor checks the inbox before any hourly LLM preflight; an empty run makes zero provider calls. Non-OpenAI Pi providers (via `PI_MODELS_JSON`) skip the OpenAI-compatible HTTP preflight.
- **Pi by default, but swappable.** The runner (`curator/pi-runner/`) wraps the [Pi coding agent](https://github.com/badlogic/pi-mono); it validates, commits, and pushes around the agent, and works with any provider Pi supports (OpenAI, Anthropic, custom OpenAI-compatible endpoints via `PI_MODELS_JSON`). Point `PROMPT_PATH` at the same prompts from any other headless agent runner if you prefer.

## Supported configuration

See [`.env.example`](./.env.example) for the full commented list. High-level groups:

| Area | Variables | Notes |
|---|---|---|
| Required | `OPENAI_API_KEY`, `WIKI_REPO_URL`, `WIKI_REPO_TOKEN`, `NEO4J_PASSWORD` | One-key default runs embeddings + extraction + distillation |
| Extraction ladder | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MODEL_LADDER` | Ordered fallbacks; empty ladder inherits `LLM_MODEL` |
| Distiller | `DISTILL_BASE_URL`, `DISTILL_API_KEY`, `DISTILL_MODEL`, `DISTILL_MODEL_LADDER` | Optional independent overrides for `graph_answer` |
| Wiki embeddings | `WIKI_MCP_EMBED_PROVIDER`, `WIKI_MCP_OPENAI_MODEL`, `WIKI_MCP_FASTEMBED_MODEL`, reindex/retry knobs | `openai` (default) or local `fastembed` |
| Cache / metrics | `CACHE_DIR`, `MCP_METRICS_PATH`, `ANSWER_CACHE_TTL`, `GRAPH_FUSION` | Feed status + gaps + Prometheus gauges |
| Graph pool | `GROUP_ID`, `NEO4J_MAX_POOL`, `NEO4J_ACQ_TIMEOUT` | `GROUP_ID` is a Graphiti property partition, **not** a Neo4j database |
| Curator (memory repo CI) | `PI_API_KEY`, `PI_PROVIDER`, `PI_MODEL`, `PI_MODELS_JSON` | Set in the private memory repo's Actions secrets/variables |

## Repo map

```
dip.ink/
├── README.md               ← you are here
├── AGENTS.md               ← agent-facing usage contract (installed into global agent instructions)
├── INSTALL_FOR_AGENTS.md   ← self-install instructions an agent retrieves and follows
├── agent-setup/            ← /recordnotes + compaction gates, per runtime
│   ├── claude-code/        ← skill + PreCompact hook
│   └── pi/                 ← memory extension (native tools), /recordnotes prompt,
│                             compact/exit gate extension
├── docker-compose.yml      ← the whole stack on one host (pinned v0.1.2 images)
├── .env.example
├── .github/workflows/      ← required CI + immutable image publication
├── ci/                     ← CI helpers (YAML/k8s/contracts, Neo4j integration runner, Pi typecheck)
├── template/               ← YOUR memory repo starts as a copy of this
│   ├── AGENTS.md           ← the wiki schema + curation rules (the contract agents follow)
│   ├── wiki/  notes/  raw/
│   ├── scripts/            ← wikilint, wikiindex, logrotate, wikidistill + curator supervisor
│   ├── .pi/prompts/        ← curator prompts (headless auto + interactive)
│   └── .github/workflows/  ← curator (hourly), synthesis (weekly), reviewqueue (daily)
├── server/                 ← THE memory server (one image, three roles)
│   ├── server.py           ← assembles the single MCP + HTTP app
│   ├── core.py             ← shared FastMCP instance + query-metrics log
│   ├── wiki.py             ← wiki_search / wiki_get / wiki_backlinks / wiki_note_drop
│   ├── graph.py            ← graph_answer / graph_search / graph_changes / ...
│   ├── status.py           ← memory_status / /api/status snapshot
│   ├── observability.py    ← Prometheus /metrics
│   ├── ingest.py           ← notes → Graphiti episodes (resumable, crash-safe, circuit-breaker)
│   └── loops/              ← healthcheck, gaps miner, alerts, contradiction janitor,
│                             entity resolution, community builder
├── curator/pi-runner/      ← containerized headless agent runner (validate/commit/rebase/push)
└── deploy/
    ├── k8s/                ← production manifests (kustomize; no Secret in apply set)
    ├── examples/           ← copyable Secret example (apply separately)
    └── observability/      ← optional ServiceMonitor / PrometheusRule / Grafana dashboard
```

## Production (k8s)

`deploy/k8s/` mirrors the compose stack for a cluster: Neo4j Deployment + PVC, the memory-server Deployment, the 15-min ingest CronJob, and the maintenance CronJobs. Images are published to GHCR by this repo's CI and **pinned to `v0.1.2`**.

The Secret example lives **outside** the apply set so a directory apply cannot overwrite real credentials with placeholders:

```sh
cp deploy/examples/dipink-secrets.example.yaml /tmp/dipink-secrets.yaml
# fill real values, then apply the Secret by itself:
kubectl apply -f /tmp/dipink-secrets.yaml
rm /tmp/dipink-secrets.yaml
# kustomization creates the namespace and every deployable resource except Secrets:
kubectl apply -k deploy/k8s
```

`kubectl kustomize deploy/k8s` never includes a Secret. Add your own Ingress in front of the `memory` Service (keep it VPN/tailnet-only or authenticated), and set `MCP_ALLOWED_HOSTS` accordingly.

### Observability (optional)

If you already run kube-prometheus-stack + Grafana (this project's reference operator stack uses namespace `monitoring` and requires label `release: monitoring`):

```sh
kubectl apply -k deploy/observability
```

That pack is intentionally **not** part of `deploy/k8s` so the public quickstart does not require monitoring CRDs. It provides:

- **ServiceMonitor** (`release: monitoring`) scraping the memory Service's `/metrics` endpoint
- **PrometheusRule** alerts for server down, wiki/graph readiness, ingest lag, blocked notes, curator backlog, graph_answer grounding errors, note-drop failures, and stale communities
- **Grafana dashboard ConfigMap** in `monitoring` labeled `grafana_dashboard: "1"` for the dashboard sidecar

After apply, **verify the Prometheus target is ACTIVE**. A known kube-prometheus-stack sharding/relabel gotcha can drop cross-namespace ServiceMonitors even when the object exists — do not assume presence means scrape success. `memory_status` / `/api/status` and the Grafana panels should agree on core counts (inbox, blocked, ingest lag, readiness).

Metric labels are bounded-cardinality only (`tool`, `outcome`, `confidence`, `cached`, `grounded`, `phase`, `version`). No raw query text, note slug, page name, or other private value is used as a label.

## Releases and CI

- Required CI (`.github/workflows/ci.yml`) gates server unit tests, a real Neo4j 5.26.2 integration job, curator supervisor tests, template wikilint/index, workflow/YAML/kustomize smoke, Pi extension typecheck, and both Docker image builds.
- Image publication (`.github/workflows/images.yml`) runs on `main` (moving `main` + immutable full-git-SHA tags) and on `v*` tags (semver + SHA). It does **not** publish `latest`.
- Public manifests and template workflows pin `ghcr.io/d6o/dip.ink/{memory,pi-runner}:v0.1.2`.

Release sequence for maintainers: push main → wait CI green → tag `v0.1.2` → wait image publication green → sync private instances / deploy.

## Operational notes (learned the hard way)

- **Ingest concurrency must stay 1.** Graphiti's `add_episode` does a non-atomic read-modify-write of edge invalidation; concurrent writes to the same entity silently lose supersession. Serial ingest is correct and fast enough (steady state is a couple of notes per tick).
- **Notes ingest oldest-first.** Bitemporal supersession only orders correctly if facts arrive in event order. The ingest sorts by the slug's timestamp prefix, and backfilled notes land at their *original* capture time.
- **The graph is a projection.** You can wipe Neo4j and re-ingest the whole corpus from git at any time (better model, better prompts). Nothing in the graph is source-of-truth.
- **A failed cron job IS the alert.** `memory-alerts` and `memory-healthcheck` run with no retries; a red job in your scheduler is the signal. Healthcheck failures additionally file a note into the inbox, so the failure shows up in the memory itself. Prefer pending-note lag over wall-clock inactivity: a quiet memory with zero pending notes is healthy.
- **`graph_answer` never hallucinates by design** — the distiller answers only from the retrieval packet, provenance is deterministically grounded, and unsupported answers return `not_found` + `escalate`. The daily healthcheck probes this property with a nonsense question and fails loudly if it ever gets an answer.
- **No automatic secret scanning.** Agents never capture secrets; the private repo and gated network are the perimeter.

## License

[MIT](./LICENSE)

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
| **An OpenAI API key** | Embeddings (`text-embedding-3-small`) + by default also graph extraction and answer distillation. One key runs everything. Any OpenAI-compatible endpoint can replace the extraction/distillation side (`LLM_BASE_URL`). |
| **A private git repo** | The memory itself — notes + wiki pages. GitHub free private repos work; so does Gitea/GitLab. You get it from `template/` below. |
| **A docker host** | Runs Neo4j, the two MCP servers, and the ingest/health loops via `docker compose`. 2 GB RAM is enough to start. k8s manifests are provided for a production setup. |
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

The template contains the wiki skeleton (`wiki/`, `notes/`, `raw/`), the schema contract (`CLAUDE.md`), the lint/index/rotate/distill scripts, the curator prompts, and the CI workflows.

Then, in the repo's GitHub settings:
- **Secrets → Actions**: add `PI_API_KEY` (the LLM key the curator agent uses — your OpenAI key works).
- Optionally **Variables → Actions**: `PI_PROVIDER` / `PI_MODEL` to pick the curator's model (defaults: `openai` / `gpt-4.1-mini`).
- **Actions → General**: allow workflows "Read and write permissions" (the curator pushes commits).

The three workflows (`curator` hourly, `synthesis` weekly, `reviewqueue` daily) activate on push. The curator is a **required part of the system**, not an optional extra — without it, notes pile up in the inbox and the wiki never forms.

### 2. Deploy the memory stack

```sh
cd dip.ink
cp .env.example .env
# fill in: OPENAI_API_KEY, WIKI_REPO_URL, WIKI_REPO_TOKEN, NEO4J_PASSWORD
docker compose up -d --build
```

That starts:

| Service | Port | What |
|---|---|---|
| `memory` | 8080 | **the one MCP server** — all `wiki_*` + `graph_*` tools at `/mcp` |
| `neo4j` | 7474/7687 (localhost) | graph storage |
| `ingest` | — | every 15 min: pull the repo, ingest new notes into the graph |
| `communities`, `healthcheck`, `gaps`, `alerts` | — | the self-maintenance loops |

Verify:

```sh
curl -s localhost:8080/health | jq '.ready, .graph_ready'
curl -s 'localhost:8080/api/search?q=hello&k=3' | jq .
```

> **Exposing beyond localhost:** the MCP server has **no authentication** — the network is the perimeter. Put it behind a VPN/tailnet or an authenticating reverse proxy; do not expose it to the public internet. If you serve it via a hostname, add it to `MCP_ALLOWED_HOSTS`.

### 3. Register the memory on your agents

```sh
claude mcp add memory --transport http http://localhost:8080/mcp
```

(Substitute your host/ingress URL for remote machines. Any MCP-capable agent works the same way.)

### 4. Install AGENTS.md globally — the step that makes it all work

Registering the server gives agents the *tools*; [`AGENTS.md`](./AGENTS.md) gives them the *discipline*. Append it to your global agent instructions so **every** session follows it:

```sh
# Claude Code
cat AGENTS.md >> ~/.claude/CLAUDE.md
# or for agents honoring the AGENTS.md convention, place it where yours reads it
```

It tells agents two things, both non-negotiable:

1. **Search before answering** anything about your stack, decisions, or conventions (`graph_answer` first — it's the cheap, distilled path).
2. **Capture every non-obvious learning** with `wiki_note_drop` — err toward capture; duplicates are cheap, missed captures are expensive.

Without this step the memory silently decays: agents answer from stale training data and never write anything back.

### 5. Watch the loop run

1. An agent finishes debugging something and drops a note: `wiki_note_drop("traefik-timeout-fix", "...")` → committed and pushed to your repo's `notes/` inbox.
2. Within 15 min, the `ingest` service turns it into graph facts (episode name = note slug, timeline position = capture time).
3. Within the hour, the `curator` workflow promotes it into wiki pages (or updates existing ones), moves the note to `wiki/sources/notes/YYYY/MM/DD/`, and pushes.
4. Any future session asks: *"how did I fix the traefik timeout?"* → `graph_answer` returns the fix, its confidence, and the source-note slug — which `graph_get_note` can fetch verbatim.
5. If the note contradicted an older fact ("timeout is 30s" → "timeout is 600s"), the old fact is marked superseded — `graph_answer` reports the current value and can mention the old one in `superseded_note`.

## The MCP tools

One server, two tool families.

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

The server also exposes plain HTTP (`/api/search`, `/api/answer`, `/api/metrics`, `/health`, ...) for non-MCP clients and the self-maintenance loops. Both tool families write to one query-instrumentation log, which the weekly gaps miner reads back.

Why one server? `graph_search` fuses the wiki's semantic hits into its packet — in-process, no HTTP hop — and agents register one URL, operators run one container, the healthcheck probes one endpoint.

## The curator

The inbox → wiki promotion is done by a real agent session, headless, on a schedule. Design choices that made it reliable:

- **Small fresh batches.** Each run drains the inbox in sub-batches of 4 notes, each in a fresh agent session (no context bloat, no compounding confusion). Every successful sub-batch is validated (`wikilint` → `wikiindex` → `logrotate` → `wikidistill`), committed, rebased, and pushed *before* the next starts — a later failure can't lose earlier work.
- **Optimistic with receipts.** The curator edits without asking. Anything genuinely needing human judgment (substantial rewrites, contradictions, secrets found in notes) lands as a one-line bullet in `wiki/Curator review queue.md` — the only place that says "look at this". A daily `reviewqueue` pass auto-resolves entries that later evidence settles.
- **Never delete.** Status changes and migrations add dated subsections; obsolete claims get dated supersede notes. The wiki is an accumulating record, which is exactly what makes the graph's bitemporal layer work.
- **Secret hygiene.** Every incoming note is scanned for literal credential prefixes (`sk-`, `ghp_`, `AKIA`, PEM headers, JWTs...). Matches are redacted in place with a vault-path reference and queued for rotation. Notes should never contain secrets, but the pipeline assumes someone will slip.
- **Pi by default, but swappable.** The runner (`curator/pi-runner/`) wraps the [Pi coding agent](https://github.com/badlogic/pi-mono); it validates, commits, and pushes around the agent, and works with any provider Pi supports (OpenAI, Anthropic, custom OpenAI-compatible endpoints via `PI_MODELS_JSON`). Point `PROMPT_PATH` at the same prompts from any other headless agent runner if you prefer.

## Repo map

```
dip.ink/
├── README.md               ← you are here
├── AGENTS.md               ← agent-facing usage contract (install into your global agent instructions)
├── docker-compose.yml      ← the whole stack on one host
├── .env.example
├── template/               ← YOUR memory repo starts as a copy of this
│   ├── CLAUDE.md           ← the wiki schema + curation rules (the contract agents follow)
│   ├── wiki/  notes/  raw/
│   ├── scripts/            ← wikilint, wikiindex, logrotate, wikidistill + curator supervisor
│   ├── .claude/commands/   ← interactive commands (/processnotes, /wikilint, ...)
│   ├── .pi/prompts/        ← the headless curator prompt
│   └── .github/workflows/  ← curator (hourly), synthesis (weekly), reviewqueue (daily)
├── server/                 ← THE memory server (one image, three roles)
│   ├── server.py           ← assembles the single MCP + HTTP app
│   ├── core.py             ← shared FastMCP instance + query-metrics log
│   ├── wiki.py             ← wiki_search / wiki_get / wiki_backlinks / wiki_note_drop
│   ├── graph.py            ← graph_answer / graph_search / graph_changes / ...
│   ├── ingest.py           ← notes → Graphiti episodes (resumable, crash-safe, circuit-breaker)
│   └── loops/              ← healthcheck, gaps miner, alerts, contradiction janitor,
│                             entity resolution, community builder
├── curator/pi-runner/      ← containerized headless agent runner (validate/commit/rebase/push)
└── deploy/k8s/             ← production manifests (Neo4j, memory server, ingest cron, memory loops)
```

## Production (k8s)

`deploy/k8s/` mirrors the compose stack for a cluster: Neo4j Deployment + PVC, the memory-server Deployment, the 15-min ingest CronJob, and the five memory-loop CronJobs. Images are published to GHCR by this repo's CI.

```sh
kubectl apply -f deploy/k8s/namespace.yaml
cp deploy/k8s/secrets.example.yaml /tmp/secrets.yaml   # fill in, apply, delete
kubectl apply -f /tmp/secrets.yaml
kubectl apply -f deploy/k8s/
```

Add your own Ingress in front of the `memory` Service (keep it VPN/tailnet-only or authenticated), and set `MCP_ALLOWED_HOSTS` accordingly.

## Operational notes (learned the hard way)

- **Ingest concurrency must stay 1.** Graphiti's `add_episode` does a non-atomic read-modify-write of edge invalidation; concurrent writes to the same entity silently lose supersession. Serial ingest is correct and fast enough (steady state is a couple of notes per tick).
- **Notes ingest oldest-first.** Bitemporal supersession only orders correctly if facts arrive in event order. The ingest sorts by the slug's timestamp prefix, and backfilled notes land at their *original* capture time.
- **The graph is a projection.** You can wipe Neo4j and re-ingest the whole corpus from git at any time (better model, better prompts). Nothing in the graph is source-of-truth.
- **A failed cron job IS the alert.** `memory-alerts` and `memory-healthcheck` run with no retries; a red job in your scheduler is the signal. Healthcheck failures additionally file a note into the inbox, so the failure shows up in the memory itself.
- **`graph_answer` never hallucinates by design** — the distiller answers only from the retrieval packet and returns `not_found` + `escalate` otherwise. The daily healthcheck probes this property with a nonsense question and fails loudly if it ever gets an answer.

## License

[MIT](./LICENSE)

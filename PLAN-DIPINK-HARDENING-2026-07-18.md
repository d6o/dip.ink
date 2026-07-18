# dip.ink hardening + observability release plan — 2026-07-18

Status: **awaiting final plan approval before swarm launch**
Target release: **v0.1.0** (first immutable public release)
Repos affected:
- `/Users/diego/Playground/dip.ink` (public source of truth)
- `/Users/diego/Playground/mykg` (private instance sync after public release)
- live k8s deployment in namespace `graphiti` (coordinator deploy/verification only)

## Product decisions from Diego

1. Fix the k8s secret-example apply hazard.
2. **Remove the entire curator secret-scanning/redaction flow.** Security contract becomes: agents must never submit secrets; the private repo + gated network are the perimeter. No server-side secret scanner is added.
3. Serialize every workflow that writes the memory repo.
4. Fix Graphiti/Neo4j client lifecycle and test it against a real Neo4j container before shipping.
5. FLAGged notes move to a durable blocked queue for later audit/fix; they must not poison normal oldest-first curation.
6. Make note-drop idempotency durable across inbox → archived-source moves.
7. Move blocking HTTP work off the event loop.
8. Fix embedding cache invalidation for new/future metadata changes without intentionally forcing a full historical re-embed.
9. Track ingest completion/content hash explicitly, including valid zero-fact notes.
10. Deterministically validate graph_answer grounding and provenance.
11. Add a direct `memory_status` interface.
12. Correct alert/freshness semantics.
13. Add a small bootstrap source note to new template repos.
14. Add required CI, immutable releases, and pinned runtime images.
15. Remove packaging/documentation drift and expose supported configuration.
16. Add complete observability using the existing kube-prometheus-stack + Grafana. Do not build a separate UI.

## Non-goals

- No Neo4j/Postgres rewrite.
- No user-facing dashboard application.
- No Helm chart in this release.
- No automatic secret detection/redaction.
- No forced repair/re-embedding of historical immutable source notes solely for the cache-key change.
- No multi-tenant support beyond ensuring GROUP_ID-scoped queries do not cross-contaminate data.

---

## Item 1 — k8s secret apply safety

### Problem
`kubectl apply -f deploy/k8s/` currently includes `secrets.example.yaml` and can overwrite the user's real `dipink-secrets` Secret with placeholders.

### Files
- `README.md`
- `deploy/k8s/secrets.example.yaml`
- new `deploy/k8s/kustomization.yaml` or relocated non-applied example

### Implementation
- Move the example out of the apply set or rename it so kubectl directory apply ignores it.
- Add a `kustomization.yaml` listing only deployable resources.
- Quickstart uses `kubectl apply -k deploy/k8s` after separately applying the real Secret.

### Definition of Done
- `kubectl kustomize deploy/k8s` contains no Secret with placeholder values.
- `kubectl apply --dry-run=client -k deploy/k8s -o name` excludes `secret/dipink-secrets`.
- README install commands cannot overwrite the operator's Secret.

---

## Item 2 — remove curator secret scanning

### Problem
The current prompt claims best-effort scanning/redaction but Diego explicitly chooses agent discipline + gated private infrastructure instead.

### Files
- `template/AGENTS.md`
- `template/.pi/prompts/processnotes-auto.md`
- `template/.pi/prompts/processnotes.md`
- `template/.pi/prompts/reviewqueue-auto.md`
- `template/.pi/prompts/synthesis-auto.md` if referenced
- `README.md`, `curator/README.md`, `template/README.md`
- corresponding mykg files during instance sync

### Implementation
- Remove scan instructions, literal-prefix lists, redact-in-place steps, and “Secrets routed to the vault” review-queue bucket.
- Preserve the global hard rule: agents must never send credentials/tokens/passwords to `wiki_note_drop`; reference secret-manager paths only.
- Do not add server-side scanning.

### Definition of Done
- No curator prompt instructs the agent to scan/redact secrets.
- No documentation claims the curator makes an already-committed secret safe.
- Root/global agent contract still clearly prohibits secret capture.

---

## Item 3 — serialize repo writers

### Problem
Curator, reviewqueue, and synthesis can overlap and conflict on `wiki/log.md` or other shared pages.

### Files
- `template/.github/workflows/curator.yml`
- `template/.github/workflows/reviewqueue.yml`
- `template/.github/workflows/synthesis.yml`
- mykg `.gitea/workflows/*.yml` during instance sync

### Implementation
Use one shared concurrency group:

```yaml
concurrency:
  group: memory-repo-writer
  cancel-in-progress: false
```

### Definition of Done
- All three public workflows and all three mykg workflows use the same group.
- A test/static check fails if a repo-writing workflow uses a different group.
- Sequential manual dispatches complete without rebase conflicts.

---

## Item 4 — Graphiti/Neo4j driver lifecycle

### Problem
`_with_roomy_pool` swaps Graphiti's driver client after construction. graphiti-core schedules index creation in `Neo4jDriver.__init__`, producing unawaited background failures and defunct-connection traces in live health/alert jobs.

### Files
- `server/ingest.py`
- graph/loop callers only if required
- new/expanded server tests

### Implementation
- Construct an explicit configured `Neo4jDriver` and pass it as `graph_driver=` to Graphiti; do not mutate the client afterward.
- Use a bounded default pool appropriate for actual workload (target 25–50, configurable).
- Ensure indices/constraints are explicitly awaited in ingest/setup paths and are not needlessly rebuilt by every read-only short-lived job.
- Remove incorrect comments that GROUP_ID creates/clones a Neo4j database; group isolation is via `group_id` properties.
- Scope direct Cypher reads/mutations to `GROUP_ID` where applicable.

### Definition of Done
- Real Neo4j 5.26.2 integration test creates/uses/closes clients with zero `Task exception was never retrieved`, `IncompleteCommit`, or leaked-driver warnings.
- `memory_alerts`, `memory_healthcheck`, graph server warm/close, and one ingest status call pass against the test Neo4j.
- Live post-deploy healthcheck/alerts logs contain none of the previous background-task traces.

---

## Item 5 — blocked-note quarantine

### Problem
FLAGged or malformed notes remain in the live oldest-first inbox and repeatedly consume curator slots.

### Files
- `template/.pi/prompts/processnotes-auto.md`
- `template/scripts/processnotes-prepare-inbox.sh`
- `template/scripts/test-processnotes-supervisor.sh`
- `template/scripts/wikilint.py` if structural validation belongs there
- `template/notes/README.md`
- bootstrap/template gitkeep as needed

### Implementation
- Add `notes/.blocked/` as a durable quarantine excluded from live/deferred batching.
- FLAG action moves the complete folder to `.blocked/<slug>/` and writes a small machine-readable `BLOCKED.md` or frontmatter reason without changing the source note.
- Previously logged exact duplicates are removed from inbox (or quarantined with reason `already-ingested`) rather than skipped in place.
- `memory_status` exposes blocked count, oldest blocked age, and slugs/reasons (bounded list).

### Definition of Done
- A blocked oldest note does not prevent later valid notes from processing.
- A deduped folder does not remain in the live inbox.
- Supervisor tests cover blocked exclusion, dedup terminal handling, and later-note progress.

---

## Item 6 — archive-aware note-drop idempotency

### Problem
Idempotency currently searches only `notes/`; a retry after curation moves the original into `wiki/sources/notes/` can create a duplicate.

### Files
- `server/wiki.py`
- `server/tests/test_note_drop_resilience.py` or new test module

### Implementation
- Search `capture-hash` across both inbox and canonical archived source-note paths.
- Return the original folder/commit-ish result as `already_exists: true` when found after archival.
- Keep lookup bounded/cached enough for ~10k notes; prefer a startup/refreshed capture-hash index over an O(all files) scan per write if tests show material latency.

### Definition of Done
- Same payload retried before curation is idempotent.
- Same payload retried after source archival is idempotent.
- Different payload with same slug remains a distinct timestamped note.

---

## Item 7 — event-loop-safe HTTP endpoints

### Problem
Plain HTTP search/reindex/metrics paths still perform synchronous embedding, git, or file scans on the Starlette event loop.

### Files
- `server/wiki.py`
- `server/server.py`
- `server/core.py`
- tests

### Implementation
- Convert blocking HTTP handlers to async and run sync work in `anyio.to_thread`.
- Bound/rotate query-metrics storage so `/api/metrics` does not scan an unbounded file forever.
- Preserve current HTTP response schemas.

### Definition of Done
- Tests prove `/api/search`, `/api/reindex`, and `/api/metrics` execute blocking implementation work off the event-loop thread.
- A slow fake embed/git/metrics read does not delay `/live` beyond a tight threshold in an integration test.

---

## Item 8 — future-correct embedding cache keys

### Problem
Cache hashes only page body even though embedding input includes page name + `index-description` + body.

### Files
- `server/wiki.py`
- degraded/cache tests

### Implementation
- New/changed entries use a hash of the exact embedding input.
- Backward-compatible migration: accept an existing legacy body-hash vector once and rewrite cache metadata to the new hash without deliberately re-embedding the full historical corpus. Future metadata changes invalidate correctly.
- Keep document metadata/body/get/backlinks available even if a changed page's embedding is temporarily unavailable.

### Definition of Done
- New page description-only change triggers re-embedding.
- Legacy cache loads without a forced full rebuild.
- After one successful cache save, entries use the new key format.
- `wiki_get` can fetch a scanned page during embedding degradation even if that page lacks a fresh vector.

---

## Item 9 — explicit ingest completion + content hash

### Problem
“Done” is inferred from presence of entity edges, so valid zero-fact notes are repeatedly treated as partial. Slug-only state cannot identify changed source content.

### Files
- `server/ingest.py`
- ingest tests/integration fixtures
- status/metrics integration

### Implementation
- After successful `add_episode`, set explicit dip.ink metadata on the Episodic node: completion flag, content hash, completion timestamp.
- Legacy episodes with entity edges count as done and are lazily upgraded when touched/status is computed if safe.
- Zero-fact episodes can be complete.
- If a source note's hash changes, use Graphiti's episode-removal path and re-add it deliberately; source notes remain operationally read-only, so this is exceptional behavior rather than routine churn.
- All state queries are scoped to GROUP_ID.

### Definition of Done
- Zero-fact fixture ingests once and remains done.
- Crash-created partial fixture is detected, cleaned, and retried.
- Changed-content fixture is detected and reingested once.
- Unchanged fixture is not reprocessed.

---

## Item 10 — graph_answer grounding + cache freshness

### Problem
Distiller confidence/source slugs are trusted without deterministic validation; answer cache can remain stale for one hour after ingest.

### Files
- `server/graph.py`
- tests

### Implementation
- Build allowed provenance slugs from packet facts/excerpt/semantic hits.
- Intersect distiller sources with allowed slugs; invented sources are discarded.
- Non-null answers require valid provenance. Unsupported `high` is downgraded or returns `error/not_found` according to deterministic rules.
- Include a graph ingest watermark/content version in the cache key or clear the answer cache when the latest Episodic completion timestamp changes.
- Instrument grounding rejection/downgrade counts.

### Definition of Done
- Fake distiller invented-source answer is rejected/downgraded.
- Valid sourced answer remains unchanged.
- New-ingest watermark invalidates a previously cached answer.
- Negative/no-hallucination behavior remains green.

---

## Item 11 — memory_status tool + API

### Problem
Agents/operators cannot get one direct operational summary; blocked/review/backlog state is otherwise hidden in repo files or scheduler UIs.

### Files
- new `server/status.py` (preferred ownership boundary)
- `server/server.py`, `server/core.py`
- Pi extension tool declarations
- root agent contract/tool docs
- tests

### Response contract
`memory_status` / `/api/status` returns bounded, non-secret data:
- component readiness: wiki, graph, git clone
- pages indexed/scanned/omitted, index age/degraded
- inbox/deferred/blocked counts and oldest ages
- bounded blocked reasons/slugs
- curator review queue open count
- newest note and newest ingested episode
- ingest pending/partial counts and lag
- latest community age/count
- query usage/error summary (24h)
- current build/version

### Definition of Done
- MCP tool, HTTP API, and Pi extension all expose the same schema.
- Status degrades component-by-component rather than failing wholesale.
- No raw note bodies, query text, credentials, or unbounded label values are returned.

---

## Item 12 — correct alerts/freshness

### Problem
`memory_alerts.py` currently treats no new episodes for ~12h as stale even when no notes arrived, despite claiming to compare repo notes against graph state.

### Files
- `server/loops/memory_alerts.py`
- status/metrics helper
- tests

### Implementation
- Alert on actual pending note→episode lag, not wall-clock inactivity.
- Quiet memory with zero pending notes is healthy.
- Blocked notes and unresolved review queue are visible warning metrics, not necessarily job failures.
- Keep community age and component health checks.

### Definition of Done
- Quiet-period fixture passes.
- Pending note older than threshold fails.
- Recent pending note inside grace passes.
- Community-stale and server-down checks still fail correctly.

---

## Item 13 — bootstrap source note

### Problem
A fresh template has no Graphiti source episode, yet the default positive healthcheck expects an answer about the note inbox.

### Files
- new canonical template source-note folder under `template/wiki/sources/notes/YYYY/MM/DD/...`
- `template/wiki/index.md` regeneration if required
- template log/README only if appropriate

### Implementation
Add one small immutable source note stating the system's stable bootstrap facts, including that agents write to the `notes/` inbox and the curator promotes notes into `wiki/`.

### Definition of Done
- Source note passes wikilint path/frontmatter rules.
- Fresh deployment ingests it and the default `ANSWER_PROBE` returns high/medium with valid provenance.
- It contains no operator-specific data.

---

## Item 14 — required CI + immutable release

### Problem
The repo lacks a top-level required test suite; template/deploy/extension changes can merge without checks; production consumes mutable `latest`; most Python dependencies are open-ended.

### Files
- new `.github/workflows/ci.yml`
- `.github/workflows/images.yml`
- `server/requirements.txt` + generated lock/constraints approach
- template workflows and deploy manifests for release tag
- test/support files

### Implementation
CI gates:
1. server unit tests
2. Graphiti/Neo4j integration test using Neo4j 5.26.2 service/container
3. curator supervisor shell tests
4. template wikilint/index validation
5. YAML parse + `kubectl kustomize`/schema smoke
6. `docker compose config` with fixture env
7. Pi extension TypeScript/typecheck
8. memory + pi-runner Docker builds

Release:
- image workflow triggers on `v*` tags and publishes semver + git SHA tags
- pin public template/k8s runtime to `v0.1.0`, not `latest`
- push commit, wait CI green, tag `v0.1.0`, wait image publication green, then sync/deploy mykg/live

### Definition of Done
- All gates pass on merged main.
- `ghcr.io/d6o/dip.ink/{memory,pi-runner}:v0.1.0` exist.
- Public manifests/workflows and mykg workflows use `v0.1.0`.
- No production workload uses `:latest` after rollout.

---

## Item 15 — packaging/docs/config cleanup

### Problem
Docs/config drift: two-server wording, Obsidian wording, compose/k8s feature mismatch, unused yq, custom provider variables not propagated, preflight probes empty inboxes and assumes OpenAI-compatible endpoints.

### Files
- `README.md`, `server/README.md`, `curator/README.md`, `template/README.md`, `INSTALL_FOR_AGENTS.md`
- `docker-compose.yml`, `.env.example`
- template workflows/supervisor
- `curator/pi-runner/Dockerfile`

### Implementation
- Remove stale two-server/Obsidian/de-Claude drift.
- Add contradiction janitor to Compose or explicitly document/report-only behavior consistently.
- Propagate supported model ladder/distiller/embed/cache/status env vars.
- Pass `PI_MODELS_JSON` from repo config where supported.
- Check inbox before any hourly LLM preflight; empty runs make zero provider calls.
- Make preflight optional/provider-aware for non-OpenAI Pi providers.
- Remove unused `yq` and architecture-specific download from pi-runner.

### Definition of Done
- Documentation matches shipped behavior and generated service list.
- Empty curator run does not call the model endpoint.
- Anthropic/native non-OpenAI provider can run without an OpenAI-compatible preflight.
- Compose and k8s expose the same intended maintenance jobs/config, with documented exceptions.

---

## Item 16 — Prometheus/Grafana observability

### Existing operator stack
- kube-prometheus-stack in namespace `monitoring`
- Prometheus selector requires label `release: monitoring`
- Grafana dashboard sidecar consumes ConfigMaps in `monitoring` labeled `grafana_dashboard: "1"`
- Known cross-namespace sharding relabel gotcha: verify target is ACTIVE after apply; do not assume ServiceMonitor existence means scrape success

### Files
- new `server/observability.py`
- `server/core.py`, `server/wiki.py`, `server/graph.py`, `server/server.py`, status collector hooks
- `server/requirements.txt`
- new `deploy/observability/servicemonitor.yaml`
- new `deploy/observability/prometheusrule.yaml`
- new `deploy/observability/grafana-dashboard.yaml` (or generated JSON + builder)
- docs

### Metrics contract (bounded-cardinality only)
Process/tool metrics:
- `dipink_info{version}`
- `dipink_tool_calls_total{tool,outcome}`
- `dipink_tool_duration_seconds{tool}`
- `dipink_note_drop_total{outcome}`
- `dipink_graph_answer_total{confidence,cached,grounded}`
- `dipink_graph_answer_duration_seconds{phase}`

State gauges from the shared status snapshot:
- `dipink_wiki_index_ready`
- `dipink_wiki_index_degraded`
- `dipink_wiki_pages_indexed`
- `dipink_wiki_index_age_seconds`
- `dipink_graph_ready`
- `dipink_inbox_notes`
- `dipink_deferred_notes`
- `dipink_blocked_notes`
- `dipink_review_queue_open`
- `dipink_ingest_pending_notes`
- `dipink_ingest_partial_notes`
- `dipink_ingest_lag_seconds`
- `dipink_community_age_seconds`

PrometheusRule alerts:
- memory server down
- wiki index unready/degraded/stale
- graph not ready
- pending ingest lag beyond threshold
- blocked notes present for sustained period
- curator backlog/lag beyond threshold
- graph_answer error/not-grounded rate above threshold with minimum volume
- note-drop failures
- communities stale

Grafana dashboard panels:
- component readiness/status row
- inbox/deferred/blocked/review counts
- ingest lag/pending/partial
- tool request rates + p95 duration
- graph_answer confidence/cache/grounding/error distributions
- wiki index pages/age/degraded
- cronjob success/failure via kube-state-metrics
- community age

### Definition of Done
- `/metrics` is Prometheus text format and contains the contract above.
- ServiceMonitor has `release: monitoring` and the live target is verified active, not dropped.
- PrometheusRules load without errors.
- Grafana ConfigMap appears via sidecar and dashboard renders real live data.
- No raw query text, note slug, page name, or other unbounded/private value is used as a metric label.
- `memory_status` and Grafana agree on core counts.

---

## Swarm lane design (disjoint ownership)

### Lane A — server runtime correctness
Owns:
- `server/core.py`
- `server/server.py`
- `server/wiki.py`
- `server/graph.py`
- `server/ingest.py`
- `server/loops/memory_alerts.py`
- `server/loops/memory_healthcheck.py`
- new `server/status.py`, `server/observability.py`
- `server/tests/**`
- `server/requirements.txt` and lock/constraints

Implements items 4, 6, 7, 8, 9, 10, 11, 12, server side of 16.

### Lane B — curator/template behavior
Owns:
- `template/AGENTS.md`
- `template/.pi/prompts/**`
- `template/scripts/**`
- `template/notes/**`
- `template/wiki/**`
- `template/.github/workflows/**`
- `curator/pi-runner/**`

Implements items 2, 3, 5, 13, workflow/runner parts of 14/15.

### Lane C — deploy + monitoring manifests
Owns:
- `deploy/**`
- `docker-compose.yml`
- `.env.example`

Implements items 1, deploy/config parts of 14/15, ServiceMonitor/PrometheusRule/Grafana dashboard for 16. Codes against the metrics contract above without editing server files.

### Lane D — docs + CI
Owns:
- root `README.md`
- `server/README.md`
- `curator/README.md`
- `template/README.md`
- `INSTALL_FOR_AGENTS.md`
- root `AGENTS.md`
- `.github/workflows/**`
- new CI helper files outside other lanes' owned directories

Implements documentation and CI/release parts of 14/15. It must not edit template workflows (Lane B) or deploy manifests (Lane C).

## Integration order

1. Merge Lane A (server interfaces/metrics contract).
2. Merge Lane B (template behavior).
3. Merge Lane C (deploy manifests consume Lane A metrics contract).
4. Merge Lane D (CI/docs).
5. Coordinator resolves only planned cross-lane integration and runs all gates.
6. Push dip.ink main once; wait CI terminal green.
7. Tag/push `v0.1.0`; wait both GHCR images terminal green.
8. Sync mykg instance from released template while preserving instance categories/prompts/config where intentional.
9. Dispatch all three mykg workflows sequentially; verify green.
10. Deploy `memory:v0.1.0` + updated cron manifests to live `graphiti` namespace.
11. Apply/verify ServiceMonitor + PrometheusRules + Grafana dashboard with admin kubeconfig.
12. Run live smoke/e2e: health, status, metrics, note-drop idempotency canary, ingest, graph_answer grounding, no-hallucination, curator blocked-path fixture cleanup.
13. Watch one full scheduled ingest + alerts + healthcheck cycle or manually trigger equivalent jobs.
14. Teardown swarm worktrees/session and capture final migration note.

## Full release gates

- `bash template/scripts/test-processnotes-supervisor.sh`
- `python3 template/scripts/wikilint.py` from a copied/initialized template repo
- server unit tests
- Neo4j integration tests
- Pi extension typecheck
- `docker compose config` with fixture env
- `kubectl kustomize deploy/k8s`
- YAML/schema validation
- `docker build server`
- `docker build curator/pi-runner`
- live `/live`, `/health`, `/api/status`, `/metrics`
- live Prometheus target active + dashboard populated
- no `Task exception was never retrieved` / `IncompleteCommit` in post-release jobs
- no `:latest` in live dip.ink workloads or mykg curator workflows

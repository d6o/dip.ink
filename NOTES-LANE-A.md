# Lane A hardening handoff

## Summary

Lane A is in progress. Items 4, 9, 6, 8, 7, 10, and 11 are complete: runtime/cache/grounding correctness is hardened and agents/operators now have one bounded component-degrading `memory_status` interface over MCP and HTTP.

## Completed items

### Item 4 — Graphiti/Neo4j lifecycle

- Inspected the exact `graphiti-core==0.29.2` wheel before implementation. Its `Neo4jDriver.__init__` schedules `build_indices_and_constraints()` on the running loop and does not expose pool kwargs; its Neo4j `clone()` is inherited as a no-op, so `GROUP_ID` does not create a database.
- Added `DipInkNeo4jDriver`, an explicit `Neo4jDriver` subclass with a configurable pool (default 40) and no untracked constructor task.
- `Graphiti` receives that driver through `graph_driver=`; no post-construction client replacement remains.
- Schema setup is explicitly awaited only in ingest/setup paths. Read-only server/loop clients do not rebuild schema.
- Direct graph reads/mutations in Lane A-owned files are scoped to `GROUP_ID`; graph searches pass `group_ids`.
- Graph server close now clears its singleton after closing, preventing reuse of a closed client.
- Added unit lifecycle/group-scope tests and an opt-in real Neo4j 5.26.2 integration test covering client create/use/close, graph warm/close, alerts, healthcheck, and ingest status with no unhandled task exceptions or leak warnings.

### Item 9 — explicit ingest completion + content hash

- Successful `add_episode` calls now mark the scoped Episodic node with `dipink_ingest_complete`, an exact UTF-8 episode-body SHA-256 in `dipink_content_hash`, and `dipink_completed_at`.
- Explicit completion is authoritative even when extraction produces zero facts.
- Legacy edge-bearing episodes still count as done; when stored content matches the source note they are lazily upgraded with the new metadata.
- Crash-created zero-edge/unmarked episodes are classified as partial, removed, and retried.
- Changed source content is detected by hash, removed through Graphiti's `remove_episode` path (with a scoped malformed-partial fallback), and deliberately re-added once.
- Ingest/status/cron share one structured assessment, process only pending notes oldest-first, expose pending/partial/changed/lag/watermark state, and exclude `notes/.blocked/` from discovery.
- The deep healthcheck now accepts explicit zero-fact completion and legacy edge compatibility, but not an unmarked zero-edge partial.
- Added focused tests for zero-fact completion, partial cleanup/retry, changed remove/re-add, unchanged idempotency, legacy migration, and blocked discovery.

### Item 6 — archive-aware note-drop idempotency

- Capture hashes are indexed across both `notes/` (including nested queues) and `wiki/sources/notes/` canonical archives.
- The index refreshes once per git revision, making normal retries O(1) instead of rescanning ~10k notes per write; the post-fetch/reset lookup forces a refresh.
- Archived retries return the original folder plus bounded location metadata (`path`, `archived`, current commit-ish value) with `already_exists: true` and do not create or push a duplicate.
- Idempotency remains scoped by both capture hash and requested slug, so a different payload using the same slug proceeds to a new timestamped folder.
- Added tests for live-inbox retries, archived retries, full note-drop short-circuiting, same-slug/different-payload behavior, and same-revision cache reuse.

### Item 8 — future-correct embedding cache keys

- New cache entries hash the exact title + `index-description` + body string after the configured truncation window, rather than hashing body alone.
- Legacy body-hash vectors are accepted once without provider calls, then rewritten to the exact-input format on the next successful cache save.
- Description-only changes now invalidate and re-embed as expected.
- The page catalog/backlink graph is now separate from the vector matrix: scanned metadata, `wiki_get`, and backlinks remain available when a changed page has no fresh vector during provider degradation.
- Readiness and `pages_indexed` continue to describe usable vectors; `pages_cataloged` reports all scanned pages retained for non-vector reads.
- Added regression tests for metadata-only invalidation, one-time legacy migration, degraded catalog/get/backlinks, zero-cache degradation, and recovery.

### Item 7 — event-loop-safe HTTP endpoints

- `/api/search` now runs synchronous query embedding plus query-log writes in an AnyIO worker thread.
- `/api/reindex` runs git refresh, filesystem scan, and embedding work in a worker thread.
- `/api/metrics` reads/parses query history in a worker thread.
- Query JSONL storage now rotates at a configurable bounded size (`MCP_METRICS_MAX_BYTES`, default 5 MiB) with a bounded backup count (`MCP_METRICS_BACKUPS`, default 2); reads cover only those bounded files and still return at most 5,000 events.
- Added thread-identity tests for all three endpoints, an ASGI responsiveness test proving slow fake search/reindex/metrics work does not delay `/live`, and log rotation/order bounds tests.

### Item 10 — graph_answer grounding + cache freshness

- Allowed provenance is built deterministically from fact source slugs, the source excerpt, and semantic-note hits.
- Invented sources are removed. A non-null answer with no allowed source is rejected to a `not_found` abstention; mixed valid/invented citations keep only the valid citations.
- `high` confidence backed only by weak semantic/superseded provenance is deterministically downgraded to `medium`; directly supported answers remain unchanged.
- Grounding outcomes are emitted on every graph-answer query event and counted in bounded in-process rejection/downgrade/acceptance buckets.
- Answer-cache keys now include the latest scoped `dipink_completed_at` watermark. A watermark change clears the old cache immediately; watermark query failure disables caching rather than risking stale answers.
- Added tests for invented-source rejection, citation filtering, confidence downgrade, valid-answer preservation, no-hallucination abstention, grounding counters, group-scoped watermark reads, cache hits, and new-ingest invalidation.

### Item 11 — memory_status tool + API

- Added one shared status collector used by the `memory_status` MCP tool and `/api/status`; the Pi extension registers the same zero-argument tool schema.
- The bounded schema covers wiki/graph/git readiness, indexed/scanned/omitted/cataloged pages and index age, inbox/deferred/blocked counts and ages, bounded blocked slugs/reasons, review queue count, newest note/episode, pending/partial/changed ingest and lag, communities, 24-hour usage/errors, and build version/revision.
- Filesystem, hashing, and metrics-log work runs in worker threads; graph checks remain async. Each component is caught independently so one failure never hides healthy sections.
- Status ingest discovery uses the server's actual wiki clone roots, not the ingest CronJob's separate default checkout paths.
- Results are briefly cached and returned as defensive copies; no note bodies, review text, raw query text, credentials, or unbounded collections are exposed.
- Added Pi extension typecheck metadata with pinned local dev dependencies; `npm run typecheck` passes with `skipLibCheck` for third-party SDK declaration defects.
- The collector lives in the already-owned/copied `server.py` rather than a new module because `server/Dockerfile` has a hard-coded copy list and is outside Lane A ownership.
- Added schema/bounds/privacy, component degradation, MCP/API equality, cache-copy, route, and Pi registration tests.

## Important decisions

- `GROUP_ID` remains a Neo4j node/edge property partition. `NEO4J_DATABASE` (default `neo4j`) is the actual database selector.
- Pool defaults are intentionally bounded (`NEO4J_MAX_POOL=40`, `NEO4J_ACQ_TIMEOUT=30`) rather than the previous 500-connection replacement.
- The integration test is skipped in ordinary unit/image builds and runs when `NEO4J_INTEGRATION=1` points at a real Neo4j 5.26.2 instance.
- The ingest hash covers the exact decoded text passed as Graphiti's `episode_body`, not file metadata or extracted-fact count.
- Legacy completion is upgraded only when the stored episode body proves the hash safely; compatibility remains read-only when historical content is unavailable.
- Capture-hash lookup includes quarantined/deferred inbox locations for retry safety, even though blocked notes are excluded from Graphiti ingest.
- Legacy embedding vectors are deliberately accepted once even though historical cache files cannot prove which old metadata produced them; once rewritten, all future metadata changes invalidate exactly.
- The JSON `/api/metrics` compatibility endpoint remains unchanged; bounding is implemented underneath it rather than replacing its response schema.
- `not_found` with a null answer is treated as a correctly grounded abstention; fabricated non-null answers rejected for missing provenance are marked not-grounded for observability.
- Status reports blocked identifiers/reasons only in a bounded list because those are explicitly operational; it never returns source-note or review-queue prose.

## Tests run

- `pytest tests/test_driver_lifecycle.py tests/test_neo4j_integration.py -q` — 4 passed, 1 skipped.
- Real `neo4j:5.26.2` container + `pytest tests/test_neo4j_integration.py -q -s` — 1 passed; no unhandled-task, `IncompleteCommit`, defunct-connection, or leak warning signatures; legacy completion metadata verified in Neo4j.
- `pytest tests/test_ingest_completion.py tests/test_driver_lifecycle.py -q -s` — 10 passed.
- Full server suite after item 9 — 19 passed, 1 skipped.
- `pytest tests/test_note_drop_resilience.py -q` after item 6 — 10 passed.
- `pytest tests/test_degraded_startup.py -q -s` after item 8 — 7 passed.
- `pytest tests/test_http_responsiveness.py -q -s` after item 7 — 5 passed, including three slow-handler `/live` responsiveness cases.
- `pytest tests/test_graph_grounding.py -q -s` after item 10 — 8 passed.
- `pytest tests/test_memory_status.py -q -s` after item 11 — 5 passed.
- Full server suite after item 11 — 45 passed, 1 skipped.
- `(cd agent-setup/pi/extensions/memory && npm install --package-lock=false --ignore-scripts && npm run typecheck)` — passed; temporary `node_modules/` removed.

## Dependencies / coordinator TODOs

- None for item 4.

## Failures / blockers

- None currently.

# Lane B — curator/template hardening notes

## Summary

**COMPLETE.** Implemented approved hardening items 2, 5, 3, 13, and curator/workflow/runner portions of 14–15 in ownership order. All lane validation gates green.

- Item 2: secret-scan/redaction workflow removed; hard "never submit secrets" contract kept.
- Item 5: terminal `notes/.blocked/` quarantine with byte-preserving receipts; exact dedup no longer poisons oldest-first batches.
- Item 3: all three template workflows share `concurrency.group: memory-repo-writer`.
- Item 13: generic immutable bootstrap source note at canonical path; wikilint/index clean; grounds default ANSWER_PROBE.
- Items 14–15 (lane scope): workflows pin `pi-runner:v0.1.0`; pass `PI_MODELS_JSON`; empty curator runs probe zero times; preflight is optional/provider-aware; unused yq download removed from pi-runner.

## Status

- Item 2 — complete.
- Item 5 — complete.
- Item 3 — complete.
- Item 13 — complete.
- Items 14–15 (curator/workflow/runner scope) — complete.

## Design decisions

- Security contract: note authors never submit credentials/tokens/passwords; only secret-manager paths. Curator does not scan/redact post-commit.
- Blocked receipt contract: `notes/.blocked/<slug>/BLOCKED.md` frontmatter `schema-version: 1`, `slug`, enum `reason`, `blocked-at`, `source-path`. Helper adds only this receipt; all pre-existing files byte-preserved.
- Exact prior-ingest detection: `scripts/processnotes-is-ingested.py` searches only `ingest |` / `auto-ingest |` log sections with slug boundaries.
- Shared concurrency group name is exactly `memory-repo-writer` with `cancel-in-progress: false` on curator, reviewqueue, and synthesis.
- Bootstrap source slug: `2026-01-01-000000-dipink-bootstrap` at `wiki/sources/notes/2026/01/01/...`. States agents write to the `notes/` inbox and the curator promotes into `wiki/`. No operator-specific data.
- Supervisor order is always: budget check → prepare inbox → empty-inbox exit → optional preflight → runner. Empty runs never call the provider.
- Preflight rules:
  - `CURATOR_PREFLIGHT=0|false|no|off` → never probe
  - `CURATOR_PREFLIGHT=1|true|yes|on` → always probe when inbox non-empty
  - `CURATOR_PREFLIGHT_OK=1` → skip only batch-1 probe (legacy/workflow reuse)
  - explicit `CURATOR_LLM_BASE_URL` → enable OpenAI-compatible HTTP preflight
  - default: openai (or empty provider) probes; native non-OpenAI (e.g. anthropic) skips
  - `PROBE_BIN` only controls how probes run when enabled, not whether they run
- Workflows pin `ghcr.io/d6o/dip.ink/pi-runner:v0.1.0` (coordinator tags after CI green) and pass `PI_MODELS_JSON` from repo vars.
- pi-runner Dockerfile no longer downloads architecture-specific yq; jq + python3-yaml remain.

## Dependencies added

- None.

## Tests and validation

- `bash template/scripts/test-processnotes-supervisor.sh` — pass. Covers:
  - empty-inbox zero-probe
  - provider-aware preflight skip (anthropic)
  - explicit preflight-off
  - blocked receipt + byte preservation
  - exact ingest-log dedup (incl. archived logs + prefix collision)
  - prepare-inbox blocked exclusion
  - duplicate terminal handling + later-note progress
  - multi-batch probes after first reuse
  - four-batch adaptive budget
  - no-op stop
  - probe error propagation
  - time-budget stop
- `python3 template/scripts/wikilint.py` from template — 0 errors/warnings (bootstrap included)
- `python3 template/scripts/wikiindex.py` — regenerates Sources monthly count for bootstrap
- YAML parse of all three workflows — pass; all use `memory-repo-writer` and `v0.1.0`
- `docker build curator/pi-runner` — pass (`dipink-pi-runner:lane-b-test`)
- `bash -n` on supervisor scripts — pass
- grep under template curator/schema files — no secret-scan/redaction instructions remain

## Failures / deferred work

- Root/non-template docs (README, INSTALL, curator/README outside ownership nuances already present) owned by Lane D.
- mykg instance workflow sync is coordinator post-release work, not this lane.
- Live `v0.1.0` image publish/tag is coordinator-only after CI green.

## Coordinator TODOs

- Lane A/status integration should parse bounded blocked receipts at `notes/.blocked/*/BLOCKED.md` for count, oldest age, slug, and reason.
- Tag `v0.1.0` only after merged CI is green; image workflow publishes `ghcr.io/d6o/dip.ink/pi-runner:v0.1.0` before mykg/live pin is meaningful.
- Lane D CI should fail if any repo-writing workflow concurrency group ≠ `memory-repo-writer`.
- After merge, default healthcheck ANSWER_PROBE (`what is this memory system's note inbox called`) should ground against bootstrap slug `2026-01-01-000000-dipink-bootstrap` once graph ingest runs.

# Lane D hardening notes

## Final summary

**Status: complete for Lane D ownership.** Items 14 (CI/release automation), 15 (docs/config explanation), and 16 (observability docs) are implemented in this worktree on `swarm-dipink-v010-d`.

Delivered:
- Required top-level CI workflow covering server unit tests, real Neo4j 5.26.2 integration, curator supervisor tests, template wikilint/index, YAML parse + actionlint, kustomize/schema smoke, Pi extension typecheck, and both Docker builds.
- Image workflow triggers on `main` and `v*` tags; publishes `main` + full-SHA + semver tags; never publishes `latest`.
- CI helpers under `ci/` enforce cross-lane release contracts without editing Lane A/B/C files.
- Owned docs rewritten: one MCP server, no Obsidian, no curator secret-scan claims, pinned `v0.1.0`, kustomize install with Secret outside the apply set, supported config surface, bootstrap/first-run, `memory_status`/`/api/status`/`/metrics`, and `release: monitoring` observability setup including the ACTIVE-target gotcha.
- Claude Code client support retained in `INSTALL_FOR_AGENTS.md`; project convention remains Pi/AGENTS.md.

Commits:
- `95f833f` `hardening(14): add required CI and immutable image release workflow`
- `20add55` `hardening(15-16): align docs with one-server config, status, and observability`

## Ownership and guardrails

- Branch/worktree: `swarm-dipink-v010-d` at `/Users/diego/Playground/dip.ink-lanes/d`.
- Owned files only: root `README.md`, root `AGENTS.md`, `server/README.md`, `curator/README.md`, `template/README.md`, `INSTALL_FOR_AGENTS.md`, `.github/workflows/**`, new root-level CI support files (`ci/**`, `.gitignore` node_modules ignore), and this note.
- The plan file was not edited.
- No push, live-service mutation, `kubectl apply`, Helm action, or production command was run from this lane.

## Cross-lane assumptions and dependencies

1. **Lane A — server tests/interfaces:**
   - Unit suite: `python -m unittest discover -s tests -p 'test_*.py' -v` from `server/`.
   - Real-container job uses `ci/run-neo4j-integration.sh` with Neo4j 5.26.2 service container.
   - Authoritative opt-in flag observed in Lane A: `NEO4J_INTEGRATION=1` (aliases `RUN_NEO4J_INTEGRATION` / `DIPINK_RUN_NEO4J_INTEGRATION` also set).
   - Expects `server/tests/test_*integration*.py` (Lane A already has `test_neo4j_integration.py`).
   - Docs describe planned `status.py` / `observability.py`, `memory_status`, `/api/status`, and Prometheus `/metrics`. At the time of this lane write, Lane A had completed items 4/6/7/8/9 but status/metrics (11/16 server side) were not yet present in the A worktree. Coordinator must merge A’s final status/metrics work before CI’s post-merge green expectation for those endpoints.
2. **Lane B — curator/template:**
   - CI expects all three template workflows to use `concurrency.group: memory-repo-writer`, image `ghcr.io/d6o/dip.ink/pi-runner:v0.1.0`, and a bootstrap source note at `wiki/sources/notes/YYYY/MM/DD/<slug>/<slug>.md`.
   - Lane B worktree already has concurrency + image pin; bootstrap source note was still missing when checked — CI will fail until B lands item 13.
3. **Lane C — deploy/config/monitoring:**
   - CI expects `deploy/k8s/kustomization.yaml`, `deploy/examples/dipink-secrets.example.yaml`, pinned `memory:v0.1.0`, and `deploy/observability/{servicemonitor,prometheusrule,grafana-dashboard}.yaml`.
   - Public app namespace is `dipink` (not live operator `graphiti`).
   - ServiceMonitor/PrometheusRule may live in `dipink` or `monitoring`; CI accepts either so long as `release: monitoring` is set and the Grafana ConfigMap is in `monitoring` with `grafana_dashboard: "1"`.
   - Secret example path documented as `deploy/examples/dipink-secrets.example.yaml` (not under `deploy/k8s/`).
4. **Merge order:** coordinator merges A, B, C, then D. Pre-merge failures caused only by absent A/B/C outputs are expected in this isolated worktree.

## Design decisions

- **Images path filters removed** from `images.yml` so a pure-docs/tag release still publishes the immutable semver tags public manifests consume.
- **`latest=false`** via docker/metadata-action; main keeps a useful moving `main` tag + full SHA.
- **Neo4j integration refuses zero-test success** if no `test_*integration*.py` module exists.
- **Unit job forces `NEO4J_INTEGRATION=0`** so the opt-in suite stays skipped outside the dedicated job.
- **check-k8s.py** validates rendered kustomize has no Secret, no `:latest`, uses `memory:v0.1.0`, ServiceMonitor port matches Service, and observability pack structure.
- **check-repo-contract.py** statically fails on template concurrency/image/bootstrap drift, missing release inputs, production `:latest`, and stale owned-doc phrases.
- Docs distinguish JSON `/api/metrics` (gaps miner log tail) from Prometheus `/metrics`.
- Claude Code install path still uses `~/.claude/CLAUDE.md`; wording clarifies that is the client path, while the repo convention is AGENTS.md.

## Validation log

| Check | Result |
|---|---|
| Plan read completely | yes (573 lines) |
| `python3 ci/check-yaml.py` | parsed 20 docs / 12 files (pre-merge tree) |
| `actionlint` on workflows | exit 0 |
| Stale phrase grep on owned docs/workflows | no matches |
| `npm ci` + `tsc -p ci/pi/tsconfig.json` | exit 0 |
| `bash template/scripts/test-processnotes-supervisor.sh` | all OK (pre-B blocked tests not present yet) |
| `ci/check-k8s.py` against Lane C rendered kustomize + observability | validated 14 app objects + observability pack |
| `docker compose -f <lane-c> --env-file ci/compose.env config --quiet` | exit 0 |
| `ci/check-repo-contract.py --release` in this worktree | fails only on missing A/B/C artifacts (expected pre-merge) |
| `ci/check-repo-contract.py --template` in this worktree | fails only on pre-B concurrency/image/bootstrap (expected) |
| Local server unit suite | not fully run here (system Python 3.14 lacks deps; CI uses 3.12 + requirements) |

## Failures / coordinator TODOs

- Full post-merge CI cannot pass in this isolated lane until A/B/C land their owned artifacts. After merge, re-run the full CI suite; do not weaken gates.
- Lane A still needs to finish/merge `memory_status` + `/metrics` server implementation for docs/runtime parity of items 11/16.
- Lane B still needs the bootstrap source note for item 13 (CI contract already enforces path shape).
- Live Prometheus target ACTIVE verification and Grafana render are coordinator-only release checks; Lane D documents them only.
- Operator live namespace remains `graphiti`; public manifests use `dipink`. Coordinator adapts on deploy if needed.

## Files touched

- `.github/workflows/ci.yml` (new)
- `.github/workflows/images.yml`
- `ci/check-k8s.py`, `ci/check-repo-contract.py`, `ci/check-yaml.py`, `ci/compose.env`, `ci/run-neo4j-integration.sh`
- `ci/pi/package.json`, `ci/pi/package-lock.json`, `ci/pi/tsconfig.json`
- `.gitignore`
- `README.md`, `AGENTS.md`, `INSTALL_FOR_AGENTS.md`, `server/README.md`, `curator/README.md`, `template/README.md`
- `NOTES-LANE-D.md`

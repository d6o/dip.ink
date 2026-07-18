# Lane C — deploy and observability hardening

## Final summary

**Status: in progress.** Items 1, 15, and 14 are implemented; item 16 remains.

## Status by plan item

- [x] Item 1 — k8s Secret apply safety
- [x] Item 15 — deploy/config parity and supported configuration
- [x] Item 14 — immutable deploy image pinning
- [ ] Item 16 — monitoring manifests and Grafana dashboard

## Design decisions

- The placeholder Secret example lives at `deploy/examples/dipink-secrets.example.yaml`, outside `deploy/k8s`, so neither `kubectl apply -f deploy/k8s/` nor the explicit kustomization can apply it.
- `deploy/k8s/kustomization.yaml` explicitly enumerates deployable resources; it intentionally has no Secret resource.
- The public application namespace remains `dipink`.
- Kubernetes non-secret runtime defaults are centralized in `dipink-config`; `envFrom` keeps the memory server and graph-backed jobs on the same model ladder, group, provider, and pool settings.
- Compose and Kubernetes expose the same six intended maintenance roles: ingest, communities with entity resolution, gaps, alerts, healthcheck, and contradiction janitor.
- Contradiction janitor is report-only by default in both targets (`DRY_RUN=1` / `JANITOR_DRY_RUN=1`).
- Extraction and graph-answer distillation can use separate OpenAI-compatible endpoints/model ladders while the one-key OpenAI default remains intact.
- `PI_MODELS_JSON` is curator/Pi-runner configuration, not a memory-server variable; `.env.example` identifies the cross-lane repository-CI handoff instead of injecting an unused value into runtime containers.
- Compose now consumes the published `memory:v0.1.0` image instead of implicitly building mutable local source; local development can use an explicit override file, while the shipped deploy remains immutable.

## Commands and results

### Item 1

- `kubectl kustomize deploy/k8s` — passed; rendered 13 resources and a PyYAML assertion found zero Secret resources.
- Final dry-run check remains pending until all lane manifests are complete: `kubectl apply --dry-run=client -k deploy/k8s -o name`.

### Item 15

- `docker compose --env-file <temporary-fixture> config` — passed with placeholder-only fixture values.
- Inline PyYAML assertions — passed: exact Compose service set is `neo4j`, `memory`, plus the six intended jobs; exact Kubernetes CronJob role set matches; shared model ladder, distiller, embedding, cache/metrics inputs are present; both janitors default to report-only.
- `kubectl kustomize deploy/k8s` — passed after adding `dipink-config` and shared `envFrom` references.

### Item 14

- `docker compose --env-file <temporary-fixture> config` and `kubectl kustomize deploy/k8s` — passed after image changes.
- Inline image assertion — passed: all seven Compose memory services and all seven rendered Kubernetes memory containers use `ghcr.io/d6o/dip.ink/memory:v0.1.0`; Compose deploy services have no `build` fallback.
- `rg -n 'image:.*:latest' deploy docker-compose.yml` — zero matches.
- PCRE2 assertion for any dip.ink memory tag other than `v0.1.0` — zero matches.

## Dependencies and coordinator TODOs

- Lane D owns README updates. It must replace the old `deploy/k8s/secrets.example.yaml` path with `deploy/examples/dipink-secrets.example.yaml`, replace directory apply with `kubectl apply -k deploy/k8s` after the real Secret is applied separately, and remove `docker compose ... --build` from production quickstarts.
- Lane B owns actual curator workflow propagation of `PI_PROVIDER`, `PI_MODEL`, and `PI_MODELS_JSON`; Lane C only documents that handoff because Compose/Kubernetes do not run the curator.
- Lane A owns `memory_status`; the coordinator should verify after merge that Lane A introduced no additional required status environment variable. The current server branch exposes no status-specific configuration name.
- The coordinator must perform post-merge and live-cluster validation; this lane does not apply resources to live services.

## Failures / limitations

- None yet.

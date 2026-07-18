# Lane C — deploy and observability hardening

## Final summary

**Status: complete.** Items 1, 15, 14, and 16 are implemented and validated in this worktree.

Delivered:
- Secret-example apply hazard eliminated (`deploy/examples/` + kustomization-only apply set).
- Compose and k8s share the six intended maintenance jobs and supported runtime config.
- All dip.ink memory images pinned to `ghcr.io/d6o/dip.ink/memory:v0.1.0` (no `:latest`).
- Optional observability pack: ServiceMonitor + PrometheusRule (`release: monitoring`) and Grafana dashboard ConfigMap in `monitoring` (`grafana_dashboard: "1"`), coded against plan item 16 metric names.

## Status by plan item

- [x] Item 1 — k8s Secret apply safety
- [x] Item 15 — deploy/config parity and supported configuration
- [x] Item 14 — immutable deploy image pinning
- [x] Item 16 — monitoring manifests and Grafana dashboard

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
- Observability resources are intentionally **not** part of `deploy/k8s` so the public quickstart does not require kube-prometheus-stack CRDs. Apply separately: `kubectl apply -k deploy/observability`.
- ServiceMonitor lives in app namespace `dipink` with `release: monitoring` (Prometheus selector). Grafana dashboard ConfigMap lives in `monitoring` with `grafana_dashboard: "1"` for the sidecar.
- memory Service gained `metadata.labels.app: memory` so the ServiceMonitor selector matches.
- Dashboard CronJob queries cover the six public CronJob names: `graphiti-ingest`, `build-communities`, `memory-gaps`, `memory-alerts`, `memory-healthcheck`, `contradiction-janitor`.
- Metric labels stay bounded (`tool`, `outcome`, `confidence`, `cached`, `grounded`, `phase`, `version`); no slug/page/query labels.

## Commands and results

### Item 1

- `kubectl kustomize deploy/k8s` — passed; rendered resources and a PyYAML assertion found zero Secret resources.
- `kubectl apply --dry-run=client -k deploy/k8s -o name` — passed; excludes `secret/dipink-secrets`.

### Item 15

- `docker compose --env-file <temporary-fixture> config` — passed with placeholder-only fixture values.
- Inline PyYAML assertions — passed: exact Compose service set is `neo4j`, `memory`, plus the six intended jobs; exact Kubernetes CronJob role set matches; shared model ladder, distiller, embedding, cache/metrics inputs are present; both janitors default to report-only.
- `kubectl kustomize deploy/k8s` — passed after adding `dipink-config` and shared `envFrom` references.

### Item 14

- `docker compose --env-file <temporary-fixture> config` and `kubectl kustomize deploy/k8s` — passed after image changes.
- Inline image assertion — passed: all seven Compose memory services and all seven rendered Kubernetes memory containers use `ghcr.io/d6o/dip.ink/memory:v0.1.0`; Compose deploy services have no `build` fallback.
- `rg -n 'image:.*:latest' deploy docker-compose.yml` — zero matches.
- PCRE2 assertion for any dip.ink memory tag other than `v0.1.0` — zero matches.

### Item 16

- `python3 deploy/observability/build_dashboard.py` — wrote ConfigMap with 29 panels.
- Structure assertions — ServiceMonitor `release: monitoring` + `/metrics` on port `http`; PrometheusRule 11 alerts matching plan contract; Grafana ConfigMap in `monitoring` with `grafana_dashboard: "1"`; dashboard JSON includes all plan metrics + kube-state-metrics CronJob/Job queries.
- `kubectl kustomize deploy/observability` — renders ServiceMonitor, PrometheusRule, ConfigMap only.
- YAML parse of every file under `deploy/` — passed (12 files).
- Full lane re-validation after commit prep — all checks green (kustomize k8s, dry-run names, compose config, no `:latest`, observability structure).

## Dependencies and coordinator TODOs

- Lane D owns README updates. It must replace the old `deploy/k8s/secrets.example.yaml` path with `deploy/examples/dipink-secrets.example.yaml`, replace directory apply with `kubectl apply -k deploy/k8s` after the real Secret is applied separately, remove `docker compose ... --build` from production quickstarts, and document optional `kubectl apply -k deploy/observability` (requires kube-prometheus-stack CRDs + Grafana sidecar).
- Lane B owns actual curator workflow propagation of `PI_PROVIDER`, `PI_MODEL`, and `PI_MODELS_JSON`; Lane C only documents that handoff because Compose/Kubernetes do not run the curator.
- Lane A owns server `/metrics` implementation for the plan metric contract; this lane only scrapes and charts those names. Coordinator should verify after merge that emitted metric names/labels match the dashboard/alerts.
- Coordinator live post-merge steps (not done here): apply observability to cluster, confirm Prometheus target ACTIVE (watch for kube-prometheus-stack sharding-relabel drop of cross-ns ServiceMonitors), confirm Grafana sidecar loads `grafana-dashboard-dipink`, adapt public `dipink` namespace resources to live `graphiti` if needed.
- The coordinator must perform post-merge and live-cluster validation; this lane does not apply resources to live services.

## Failures / limitations

- None blocking. Live scrape/dashboard population cannot be verified in-lane (no kubectl against production; dry-run only).
- If the cluster Prometheus ruleSelector is namespace-scoped to `monitoring` only, the PrometheusRule in `dipink` may need a coordinator move to `monitoring` at apply time; label `release: monitoring` matches Diego's existing convention used by other rules.

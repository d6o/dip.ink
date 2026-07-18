# Lane C — deploy and observability hardening

## Final summary

**Status: in progress.** Item 1 is implemented; items 15, 14, and 16 remain.

## Status by plan item

- [x] Item 1 — k8s Secret apply safety
- [ ] Item 15 — deploy/config parity and supported configuration
- [ ] Item 14 — immutable deploy image pinning
- [ ] Item 16 — monitoring manifests and Grafana dashboard

## Design decisions

- The placeholder Secret example lives at `deploy/examples/dipink-secrets.example.yaml`, outside `deploy/k8s`, so neither `kubectl apply -f deploy/k8s/` nor the explicit kustomization can apply it.
- `deploy/k8s/kustomization.yaml` explicitly enumerates deployable resources; it intentionally has no Secret resource.
- The public application namespace remains `dipink`.

## Commands and results

### Item 1

- `kubectl kustomize deploy/k8s` — passed; rendered 13 resources and a PyYAML assertion found zero Secret resources.
- Final dry-run check remains pending until all lane manifests are complete: `kubectl apply --dry-run=client -k deploy/k8s -o name`.

## Dependencies and coordinator TODOs

- Lane D owns README updates. It must replace the old `deploy/k8s/secrets.example.yaml` path with `deploy/examples/dipink-secrets.example.yaml` and replace directory apply with `kubectl apply -k deploy/k8s` after the real Secret is applied separately.
- The coordinator must perform post-merge and live-cluster validation; this lane does not apply resources to live services.

## Failures / limitations

- None yet.

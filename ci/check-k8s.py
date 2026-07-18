#!/usr/bin/env python3
"""Structural smoke checks for rendered app and Prometheus/Grafana manifests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

PINNED_MEMORY = "ghcr.io/d6o/dip.ink/memory:v0.1.4"
APP_NAMESPACE = "dipink"


def load_documents(path: Path) -> list[dict[str, Any]]:
    documents = []
    for index, document in enumerate(yaml.safe_load_all(path.read_text(encoding="utf-8")), start=1):
        if document is None:
            continue
        if not isinstance(document, dict):
            raise ValueError(f"{path}: document {index} is not a mapping")
        for key in ("apiVersion", "kind", "metadata"):
            if key not in document:
                raise ValueError(f"{path}: document {index} is missing {key}")
        metadata = document.get("metadata")
        if not isinstance(metadata, dict) or not metadata.get("name"):
            raise ValueError(f"{path}: document {index} has no metadata.name")
        documents.append(document)
    if not documents:
        raise ValueError(f"{path}: contains no Kubernetes objects")
    return documents


def labels(resource: dict[str, Any]) -> dict[str, Any]:
    metadata = resource.get("metadata")
    value = metadata.get("labels", {}) if isinstance(metadata, dict) else {}
    return value if isinstance(value, dict) else {}


def namespace(resource: dict[str, Any]) -> str | None:
    metadata = resource.get("metadata")
    value = metadata.get("namespace") if isinstance(metadata, dict) else None
    return str(value) if value is not None else None


def require_single(resources: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [resource for resource in resources if resource.get("kind") == kind]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {kind}, found {len(matches)}")
    return matches[0]


def pod_images(resource: dict[str, Any]) -> list[str]:
    spec = resource.get("spec")
    if not isinstance(spec, dict):
        return []
    pod_spec: Any = None
    if resource.get("kind") in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"}:
        pod_spec = spec.get("template", {}).get("spec")
    elif resource.get("kind") == "CronJob":
        pod_spec = spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec")
    elif resource.get("kind") == "Job":
        pod_spec = spec.get("template", {}).get("spec")
    elif resource.get("kind") == "Pod":
        pod_spec = spec
    if not isinstance(pod_spec, dict):
        return []
    containers = list(pod_spec.get("initContainers", []) or []) + list(
        pod_spec.get("containers", []) or []
    )
    return [
        str(container.get("image"))
        for container in containers
        if isinstance(container, dict) and container.get("image")
    ]


def validate_rendered(resources: list[dict[str, Any]]) -> set[str]:
    kinds = {str(resource.get("kind")) for resource in resources}
    if "Secret" in kinds:
        raise ValueError(
            "kubectl kustomize output includes a Secret; the placeholder example must stay outside the apply set"
        )

    service_port_names: set[str] = set()
    release_memory_images = 0
    for resource in resources:
        if resource.get("kind") == "Service":
            spec = resource.get("spec", {})
            for port in spec.get("ports", []) if isinstance(spec, dict) else []:
                if isinstance(port, dict) and port.get("name"):
                    service_port_names.add(str(port["name"]))
        for image in pod_images(resource):
            if image.endswith(":latest") or image.endswith("/latest"):
                raise ValueError(f"rendered workload uses mutable image {image}")
            if ":latest" in image.split("@", 1)[0]:
                raise ValueError(f"rendered workload uses mutable image {image}")
            if image == PINNED_MEMORY:
                release_memory_images += 1
    if release_memory_images == 0:
        raise ValueError(f"rendered workloads do not use {PINNED_MEMORY}")
    return service_port_names


def validate_service_monitor(resource: dict[str, Any], service_port_names: set[str]) -> None:
    # Public manifests keep the ServiceMonitor next to the app Service so the
    # scrape target is unambiguous; the Prometheus selector still requires
    # release: monitoring.
    sm_ns = namespace(resource)
    if sm_ns not in {APP_NAMESPACE, "monitoring"}:
        raise ValueError(
            f"ServiceMonitor namespace must be {APP_NAMESPACE!r} or 'monitoring'; got {sm_ns!r}"
        )
    if labels(resource).get("release") != "monitoring":
        raise ValueError("ServiceMonitor metadata.labels.release must equal monitoring")
    spec = resource.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("ServiceMonitor has no spec")
    namespace_selector = spec.get("namespaceSelector")
    match_names = (
        namespace_selector.get("matchNames", []) if isinstance(namespace_selector, dict) else []
    )
    if APP_NAMESPACE not in match_names and not (
        isinstance(namespace_selector, dict) and namespace_selector.get("any") is True
    ):
        raise ValueError(
            f"ServiceMonitor namespaceSelector.matchNames must include {APP_NAMESPACE}"
        )
    selector = spec.get("selector")
    if (
        not isinstance(selector, dict)
        or not isinstance(selector.get("matchLabels"), dict)
        or not selector["matchLabels"]
    ):
        raise ValueError("ServiceMonitor needs a bounded matchLabels selector")
    endpoints = spec.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise ValueError("ServiceMonitor needs at least one endpoint")
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise ValueError("ServiceMonitor endpoint is not a mapping")
        if endpoint.get("path", "/metrics") != "/metrics":
            raise ValueError("ServiceMonitor must scrape the Prometheus /metrics endpoint")
        port = endpoint.get("port")
        if not port or str(port) not in service_port_names:
            raise ValueError(
                f"ServiceMonitor endpoint port {port!r} does not match a rendered Service port name"
            )


def validate_prometheus_rule(resource: dict[str, Any]) -> None:
    rule_ns = namespace(resource)
    if rule_ns not in {APP_NAMESPACE, "monitoring"}:
        raise ValueError(
            f"PrometheusRule namespace must be {APP_NAMESPACE!r} or 'monitoring'; got {rule_ns!r}"
        )
    if labels(resource).get("release") != "monitoring":
        raise ValueError("PrometheusRule metadata.labels.release must equal monitoring")
    groups = resource.get("spec", {}).get("groups", [])
    rules = [
        rule
        for group in groups
        if isinstance(group, dict)
        for rule in group.get("rules", [])
        if isinstance(rule, dict)
    ]
    if len(rules) < 9:
        raise ValueError(
            f"PrometheusRule must cover the nine planned alert families; found {len(rules)} rules"
        )
    for rule in rules:
        if not rule.get("alert") or rule.get("expr") is None:
            raise ValueError("every Prometheus alert rule needs alert and expr")
    names = " ".join(str(rule.get("alert", "")).lower() for rule in rules)
    required_keywords = (
        "down",
        "wiki",
        "graph",
        "ingest",
        "blocked",
        "curator",
        "answer",
        "note",
        "communit",
    )
    missing = [keyword for keyword in required_keywords if keyword not in names]
    if missing:
        raise ValueError(
            f"PrometheusRule alert names do not expose planned families: {', '.join(missing)}"
        )


def validate_grafana(resource: dict[str, Any]) -> None:
    if namespace(resource) != "monitoring":
        raise ValueError("Grafana dashboard ConfigMap must live in namespace monitoring")
    if str(labels(resource).get("grafana_dashboard")) != "1":
        raise ValueError('Grafana dashboard ConfigMap needs grafana_dashboard: "1"')
    data = resource.get("data")
    if not isinstance(data, dict):
        raise ValueError("Grafana dashboard ConfigMap has no data")
    dashboards = [(name, value) for name, value in data.items() if str(name).endswith(".json")]
    if len(dashboards) != 1:
        raise ValueError(f"expected one dashboard JSON entry, found {len(dashboards)}")
    name, raw = dashboards[0]
    try:
        dashboard = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Grafana JSON in {name}: {exc}") from exc
    if (
        not dashboard.get("title")
        or not isinstance(dashboard.get("panels"), list)
        or not dashboard["panels"]
    ):
        raise ValueError("Grafana dashboard needs a title and at least one panel")
    serialized = json.dumps(dashboard)
    for metric in (
        "dipink_wiki_index_ready",
        "dipink_graph_ready",
        "dipink_ingest_lag_seconds",
        "dipink_tool_calls_total",
    ):
        if metric not in serialized:
            raise ValueError(f"Grafana dashboard does not query required metric {metric}")


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"usage: {Path(sys.argv[0]).name} RENDERED_K8S OBSERVABILITY_DIR",
            file=sys.stderr,
        )
        return 2
    rendered_path = Path(sys.argv[1])
    observability_dir = Path(sys.argv[2])
    try:
        rendered = load_documents(rendered_path)
        service_port_names = validate_rendered(rendered)

        expected = {
            "servicemonitor.yaml",
            "prometheusrule.yaml",
            "grafana-dashboard.yaml",
        }
        actual = (
            {path.name for path in observability_dir.glob("*.yaml")}
            if observability_dir.is_dir()
            else set()
        )
        missing = expected - actual
        if missing:
            raise ValueError(f"missing observability manifest(s): {', '.join(sorted(missing))}")
        observability = [
            resource
            for name in sorted(expected)
            for resource in load_documents(observability_dir / name)
        ]
        service_monitor = require_single(observability, "ServiceMonitor")
        prometheus_rule = require_single(observability, "PrometheusRule")
        grafana = require_single(observability, "ConfigMap")
        validate_service_monitor(service_monitor, service_port_names)
        validate_prometheus_rule(prometheus_rule)
        validate_grafana(grafana)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    print(
        f"validated {len(rendered)} rendered app object(s) and "
        f"observability pack against {PINNED_MEMORY}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

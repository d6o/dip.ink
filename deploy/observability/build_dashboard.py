#!/usr/bin/env python3
"""Build the Grafana dashboard ConfigMap deterministically (stdlib only)."""

from __future__ import annotations

import json
from pathlib import Path

DATASOURCE = {"type": "prometheus", "uid": "prometheus"}
CRONJOBS = (
    "(graphiti-ingest|build-communities|memory-gaps|memory-alerts|"
    "memory-healthcheck|contradiction-janitor)"
)


def target(expr: str, legend: str, ref_id: str, *, instant: bool = False) -> dict:
    item = {
        "datasource": DATASOURCE,
        "editorMode": "code",
        "expr": expr,
        "legendFormat": legend,
        "range": not instant,
        "refId": ref_id,
    }
    if instant:
        item["instant"] = True
    return item


def row(panel_id: int, title: str, y: int) -> dict:
    return {
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "id": panel_id,
        "panels": [],
        "title": title,
        "type": "row",
    }


def stat(
    panel_id: int,
    title: str,
    expr: str,
    x: int,
    y: int,
    *,
    width: int = 4,
    unit: str = "short",
    legend: str = "",
    thresholds: list[dict] | None = None,
    text_mode: str = "auto",
) -> dict:
    return {
        "datasource": DATASOURCE,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": thresholds or [{"color": "green", "value": None}],
                },
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"h": 4, "w": width, "x": x, "y": y},
        "id": panel_id,
        "options": {
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showPercentChange": False,
            "textMode": text_mode,
            "wideLayout": True,
        },
        "pluginVersion": "11.0.0",
        "targets": [target(expr, legend, "A", instant=True)],
        "title": title,
        "type": "stat",
    }


def timeseries(
    panel_id: int,
    title: str,
    targets: list[dict],
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    unit: str = "short",
) -> dict:
    return {
        "datasource": DATASOURCE,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "axisCenteredZero": False,
                    "axisColorMode": "text",
                    "axisLabel": "",
                    "axisPlacement": "auto",
                    "barAlignment": 0,
                    "drawStyle": "line",
                    "fillOpacity": 12,
                    "gradientMode": "none",
                    "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                    "insertNulls": False,
                    "lineInterpolation": "linear",
                    "lineWidth": 2,
                    "pointSize": 5,
                    "scaleDistribution": {"type": "linear"},
                    "showPoints": "never",
                    "spanNulls": False,
                    "stacking": {"group": "A", "mode": "none"},
                    "thresholdsStyle": {"mode": "off"},
                },
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "red", "value": 80},
                    ],
                },
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"h": height, "w": width, "x": x, "y": y},
        "id": panel_id,
        "options": {
            "legend": {"calcs": ["lastNotNull"], "displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
        },
        "pluginVersion": "11.0.0",
        "targets": targets,
        "title": title,
        "type": "timeseries",
    }


ready_thresholds = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
bad_at_one = [{"color": "green", "value": None}, {"color": "red", "value": 1}]

panels = [
    row(1, "Component readiness and status", 0),
    stat(2, "Build version", "max by (version) (dipink_info)", 0, 1, legend="{{version}}", text_mode="name"),
    stat(3, "Prometheus target up", 'max(up{namespace="dipink", service="memory"})', 4, 1, thresholds=ready_thresholds),
    stat(4, "Wiki index ready", "max(dipink_wiki_index_ready)", 8, 1, thresholds=ready_thresholds),
    stat(5, "Wiki index degraded", "max(dipink_wiki_index_degraded)", 12, 1, thresholds=bad_at_one),
    stat(6, "Graph ready", "max(dipink_graph_ready)", 16, 1, thresholds=ready_thresholds),
    stat(7, "Wiki pages indexed", "max(dipink_wiki_pages_indexed)", 20, 1),
    timeseries(
        8,
        "Wiki index age",
        [target("max(dipink_wiki_index_age_seconds)", "index age", "A")],
        0,
        5,
        12,
        6,
        unit="s",
    ),
    timeseries(
        9,
        "Community age",
        [target("max(dipink_community_age_seconds)", "community age", "A")],
        12,
        5,
        12,
        6,
        unit="s",
    ),
    row(10, "Inbox, curator, and ingest state", 11),
    stat(11, "Inbox notes", "max(dipink_inbox_notes)", 0, 12),
    stat(12, "Deferred notes", "max(dipink_deferred_notes)", 4, 12),
    stat(13, "Blocked notes", "max(dipink_blocked_notes)", 8, 12, thresholds=bad_at_one),
    stat(14, "Review queue open", "max(dipink_review_queue_open)", 12, 12),
    stat(15, "Ingest pending", "max(dipink_ingest_pending_notes)", 16, 12),
    stat(16, "Ingest partial", "max(dipink_ingest_partial_notes)", 20, 12, thresholds=bad_at_one),
    timeseries(
        17,
        "Oldest pending ingest lag",
        [target("max(dipink_ingest_lag_seconds)", "ingest lag", "A")],
        0,
        16,
        24,
        6,
        unit="s",
    ),
    row(18, "Tool and graph_answer traffic", 22),
    timeseries(
        19,
        "Tool request rate",
        [target("sum by (tool, outcome) (rate(dipink_tool_calls_total[5m]))", "{{tool}} / {{outcome}}", "A")],
        0,
        23,
        12,
        7,
        unit="reqps",
    ),
    timeseries(
        20,
        "Tool p95 duration",
        [
            target(
                "histogram_quantile(0.95, sum by (le, tool) (rate(dipink_tool_duration_seconds_bucket[5m])))",
                "{{tool}}",
                "A",
            )
        ],
        12,
        23,
        12,
        7,
        unit="s",
    ),
    timeseries(
        21,
        "Note-drop outcomes",
        [target("sum by (outcome) (rate(dipink_note_drop_total[5m]))", "{{outcome}}", "A")],
        0,
        30,
        8,
        7,
        unit="reqps",
    ),
    timeseries(
        22,
        "graph_answer confidence / errors",
        [target("sum by (confidence) (rate(dipink_graph_answer_total[5m]))", "{{confidence}}", "A")],
        8,
        30,
        8,
        7,
        unit="reqps",
    ),
    timeseries(
        23,
        "graph_answer cache and grounding",
        [
            target("sum by (cached) (rate(dipink_graph_answer_total[5m]))", "cached={{cached}}", "A"),
            target("sum by (grounded) (rate(dipink_graph_answer_total[5m]))", "grounded={{grounded}}", "B"),
        ],
        16,
        30,
        8,
        7,
        unit="reqps",
    ),
    timeseries(
        24,
        "graph_answer p95 duration by phase",
        [
            target(
                "histogram_quantile(0.95, sum by (le, phase) (rate(dipink_graph_answer_duration_seconds_bucket[5m])))",
                "{{phase}}",
                "A",
            )
        ],
        0,
        37,
        24,
        6,
        unit="s",
    ),
    row(25, "Kubernetes CronJob and Job health (kube-state-metrics)", 43),
    timeseries(
        26,
        "Seconds since last successful CronJob",
        [
            target(
                f'time() - max by (cronjob) (kube_cronjob_status_last_successful_time{{namespace="dipink", cronjob=~"{CRONJOBS}"}})',
                "{{cronjob}}",
                "A",
            )
        ],
        0,
        44,
        12,
        8,
        unit="s",
    ),
    timeseries(
        27,
        "CronJob active and suspended",
        [
            target(
                f'max by (cronjob) (kube_cronjob_status_active{{namespace="dipink", cronjob=~"{CRONJOBS}"}})',
                "active {{cronjob}}",
                "A",
            ),
            target(
                f'max by (cronjob) (kube_cronjob_spec_suspend{{namespace="dipink", cronjob=~"{CRONJOBS}"}})',
                "suspended {{cronjob}}",
                "B",
            ),
        ],
        12,
        44,
        12,
        8,
    ),
    timeseries(
        28,
        "Recent CronJob-created Job failures",
        [
            target(
                f'max by (job_name) (kube_job_status_failed{{namespace="dipink", job_name=~"{CRONJOBS}-[0-9]+"}})',
                "{{job_name}}",
                "A",
            )
        ],
        0,
        52,
        12,
        8,
    ),
    timeseries(
        29,
        "Recent CronJob-created Job successes",
        [
            target(
                f'max by (job_name) (kube_job_status_succeeded{{namespace="dipink", job_name=~"{CRONJOBS}-[0-9]+"}})',
                "{{job_name}}",
                "A",
            )
        ],
        12,
        52,
        12,
        8,
    ),
]

dashboard = {
    "annotations": {
        "list": [
            {
                "builtIn": 1,
                "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True,
                "hide": True,
                "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts",
                "type": "dashboard",
            }
        ]
    },
    "description": "dip.ink memory-server, backlog, ingest, graph_answer, and scheduled-job health.",
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 1,
    "id": None,
    "links": [],
    "liveNow": False,
    "panels": panels,
    "refresh": "30s",
    "schemaVersion": 39,
    "tags": ["dipink", "memory", "observability"],
    "templating": {"list": []},
    "time": {"from": "now-24h", "to": "now"},
    "timepicker": {},
    "timezone": "browser",
    "title": "dip.ink memory",
    "uid": "dipink-memory",
    "version": 1,
    "weekStart": "",
}

payload = json.dumps(dashboard, indent=2, sort_keys=False)
required_metrics = (
    "dipink_info",
    "dipink_tool_calls_total",
    "dipink_tool_duration_seconds",
    "dipink_note_drop_total",
    "dipink_graph_answer_total",
    "dipink_graph_answer_duration_seconds",
    "dipink_wiki_index_ready",
    "dipink_wiki_index_degraded",
    "dipink_wiki_pages_indexed",
    "dipink_wiki_index_age_seconds",
    "dipink_graph_ready",
    "dipink_inbox_notes",
    "dipink_deferred_notes",
    "dipink_blocked_notes",
    "dipink_review_queue_open",
    "dipink_ingest_pending_notes",
    "dipink_ingest_partial_notes",
    "dipink_ingest_lag_seconds",
    "dipink_community_age_seconds",
)
missing = [metric for metric in required_metrics if metric not in payload]
if missing:
    raise SystemExit(f"dashboard is missing contract metrics: {missing}")
for metric in (
    "kube_cronjob_status_last_successful_time",
    "kube_cronjob_status_active",
    "kube_cronjob_spec_suspend",
    "kube_job_status_failed",
    "kube_job_status_succeeded",
):
    if metric not in payload:
        raise SystemExit(f"dashboard is missing kube-state-metrics query: {metric}")

header = """# Generated by deploy/observability/build_dashboard.py; do not edit by hand.
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboard-dipink
  namespace: monitoring
  labels:
    grafana_dashboard: \"1\"
data:
  dipink.json: |-
"""
indented_payload = "\n".join(f"    {line}" for line in payload.splitlines())
output = header + indented_payload + "\n"
Path(__file__).with_name("grafana-dashboard.yaml").write_text(output, encoding="utf-8")
print("wrote grafana-dashboard.yaml with", len(panels), "panels")

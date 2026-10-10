"""Per-note ingest measurements.

The ingest job records one measurement for each note attempt. The record says
where the time went (LLM calls, embeddings, Neo4j queries, other time such as
retry waits), which prompts and models ran, how many tokens they used, and how
each failure happened. It does not change ingest behavior.

Each record goes to two places:

- stdout, as one line that starts with ``INGEST_METRIC `` (job logs), and
- Neo4j, as a ``(:DipinkIngestMetric)`` node (outside the Graphiti labels),
  so ``ingest_report.py`` can analyze history after the job pods are gone.

No record contains note text, prompts, or model output. Error messages are
bounded to 200 characters.
"""
from __future__ import annotations

import contextvars
import json
import os
import socket
import time
from collections import defaultdict
from dataclasses import dataclass, field

ERROR_MAX = 200

_current: contextvars.ContextVar["NoteMetrics | None"] = contextvars.ContextVar(
    "dipink_note_metrics", default=None
)
_prompt: contextvars.ContextVar[str] = contextvars.ContextVar("dipink_prompt_name", default="")


def current() -> "NoteMetrics | None":
    return _current.get()


def set_prompt(name: str | None) -> contextvars.Token:
    return _prompt.set(name or "unknown")


def reset_prompt(token: contextvars.Token) -> None:
    _prompt.reset(token)


def prompt_name() -> str:
    return _prompt.get() or "unknown"


def bounded(text: object, limit: int = ERROR_MAX) -> str:
    value = " ".join(str(text).split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def failure_kind(error: BaseException) -> str:
    """A short, stable class for a failed LLM call or note."""
    name = type(error).__name__
    text = str(error).lower()
    status = getattr(error, "status_code", None)
    if name == "EmptyResponseError" or "empty response" in text:
        return "empty"
    if name == "JSONDecodeError":
        return "json_truncated" if "unterminated" in text or "expecting" in text else "json_invalid"
    if name == "ValidationError" or "validation error" in text:
        return "schema"
    if status == 429 or "ratelimit" in name.lower() or "429" in text:
        return "rate_limit"
    if isinstance(status, int) and status >= 500:
        return "provider_5xx"
    if isinstance(status, int) and 400 <= status < 500:
        return f"provider_{status}"
    if "timeout" in name.lower() or "timed out" in text:
        return "timeout"
    if "connection" in name.lower() or "connection" in text:
        return "connection"
    return name


def _union_seconds(intervals: list[tuple[float, float]]) -> float:
    """Wall time covered by possibly overlapping intervals."""
    total = 0.0
    end = None
    start = None
    for a, b in sorted(intervals):
        if end is None or a > end:
            if end is not None:
                total += end - start
            start, end = a, b
        else:
            end = max(end, b)
    if end is not None:
        total += end - start
    return total


@dataclass
class NoteMetrics:
    slug: str
    body_chars: int
    attempt: int = 1
    started: float = field(default_factory=time.monotonic)
    llm_calls: list[dict] = field(default_factory=list)
    embed: list[tuple[float, float, int]] = field(default_factory=list)
    neo4j: list[tuple[float, float]] = field(default_factory=list)
    episode_retries: int = 0

    def _offset(self, t: float) -> float:
        return t - self.started

    def record_llm(
        self,
        *,
        model: str,
        started: float,
        ended: float,
        outcome: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        finish_reason: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.llm_calls.append({
            "prompt": prompt_name(),
            "model": model,
            "t0": round(self._offset(started), 2),
            "s": round(ended - started, 2),
            "outcome": outcome,
            "in_tok": prompt_tokens,
            "out_tok": completion_tokens,
            "finish": finish_reason,
            "max_tok": max_tokens,
        })

    def record_embed(self, started: float, ended: float, count: int) -> None:
        self.embed.append((self._offset(started), self._offset(ended), count))

    def record_neo4j(self, started: float, ended: float) -> None:
        self.neo4j.append((self._offset(started), self._offset(ended)))

    def summary(
        self,
        *,
        outcome: str,
        error: BaseException | None = None,
        nodes: int | None = None,
        edges: int | None = None,
    ) -> dict:
        wall = time.monotonic() - self.started
        llm_iv = [(c["t0"], c["t0"] + c["s"]) for c in self.llm_calls]
        embed_iv = [(a, b) for a, b, _ in self.embed]
        busy = _union_seconds(llm_iv + embed_iv + self.neo4j)
        by_prompt: dict[str, dict] = defaultdict(lambda: {
            "calls": 0, "s": 0.0, "fail": 0, "in_tok": 0, "out_tok": 0, "max_out_tok": 0,
        })
        by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "s": 0.0, "fail": 0})
        failures: dict[str, int] = defaultdict(int)
        truncated = 0
        for call in self.llm_calls:
            p = by_prompt[call["prompt"]]
            m = by_model[call["model"]]
            p["calls"] += 1
            m["calls"] += 1
            p["s"] += call["s"]
            m["s"] += call["s"]
            p["in_tok"] += call["in_tok"] or 0
            p["out_tok"] += call["out_tok"] or 0
            p["max_out_tok"] = max(p["max_out_tok"], call["out_tok"] or 0)
            if call["finish"] == "length":
                truncated += 1
            if call["outcome"] != "ok":
                p["fail"] += 1
                m["fail"] += 1
                failures[call["outcome"]] += 1
        for d in (*by_prompt.values(), *by_model.values()):
            d["s"] = round(d["s"], 1)
        slowest = sorted(self.llm_calls, key=lambda c: c["s"], reverse=True)[:5]
        return {
            "slug": self.slug,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "job": os.environ.get("HOSTNAME") or socket.gethostname(),
            "version": os.environ.get("DIPINK_VERSION", ""),
            "attempt": self.attempt,
            "outcome": outcome,
            "error_kind": failure_kind(error) if error else "",
            "error": bounded(f"{type(error).__name__}: {error}") if error else "",
            "body_chars": self.body_chars,
            "wall_s": round(wall, 1),
            "llm_calls": len(self.llm_calls),
            "llm_sum_s": round(sum(c["s"] for c in self.llm_calls), 1),
            "llm_wall_s": round(_union_seconds(llm_iv), 1),
            "llm_in_tok": sum(c["in_tok"] or 0 for c in self.llm_calls),
            "llm_out_tok": sum(c["out_tok"] or 0 for c in self.llm_calls),
            "llm_truncated": truncated,
            "llm_failures": dict(failures),
            "embed_calls": len(self.embed),
            "embed_inputs": sum(n for _, _, n in self.embed),
            "embed_wall_s": round(_union_seconds(embed_iv), 1),
            "neo4j_queries": len(self.neo4j),
            "neo4j_wall_s": round(_union_seconds(self.neo4j), 1),
            # Time with no LLM, embedding, or Neo4j work in flight: retry
            # waits, Python work, and unmeasured Neo4j session writes.
            "idle_s": round(max(0.0, wall - busy), 1),
            "episode_retries": self.episode_retries,
            "nodes": nodes,
            "edges": edges,
            "by_prompt": dict(by_prompt),
            "by_model": dict(by_model),
            "slowest_calls": slowest,
        }


def start(slug: str, body_chars: int, attempt: int) -> tuple[NoteMetrics, contextvars.Token]:
    metrics = NoteMetrics(slug=slug, body_chars=body_chars, attempt=attempt)
    return metrics, _current.set(metrics)


def finish(token: contextvars.Token) -> None:
    _current.reset(token)


def emit(record: dict) -> None:
    print("INGEST_METRIC " + json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


# --- Neo4j persistence -------------------------------------------------------

_SCALARS = (
    "slug", "at", "job", "version", "attempt", "outcome", "error_kind", "error", "body_chars",
    "wall_s", "llm_calls", "llm_sum_s", "llm_wall_s", "llm_in_tok", "llm_out_tok",
    "llm_truncated", "embed_calls", "embed_inputs", "embed_wall_s", "neo4j_queries",
    "neo4j_wall_s", "idle_s", "episode_retries", "nodes", "edges",
)


async def persist(driver, record: dict) -> None:
    """Store one record. A failure here never fails the ingest."""
    props = {k: record.get(k) for k in _SCALARS}
    props["detail"] = json.dumps({
        k: record[k] for k in ("llm_failures", "by_prompt", "by_model", "slowest_calls")
    }, separators=(",", ":"))
    try:
        await driver.client.execute_query(
            "CREATE (m:DipinkIngestMetric) SET m = $props",
            props=props,
            database_=getattr(driver, "_database", None),
        )
    except Exception as error:  # noqa: BLE001
        print(f"[metrics] persist failed: {type(error).__name__}", flush=True)


async def ensure_index(driver) -> None:
    try:
        await driver.client.execute_query(
            "CREATE INDEX dipink_ingest_metric_slug IF NOT EXISTS "
            "FOR (m:DipinkIngestMetric) ON (m.slug)",
            database_=getattr(driver, "_database", None),
        )
    except Exception as error:  # noqa: BLE001
        print(f"[metrics] index setup failed: {type(error).__name__}", flush=True)


async def prior_attempts(driver, slug: str) -> int:
    """Number of earlier recorded attempts for a slug (0 when unknown)."""
    try:
        records, _, _ = await driver.client.execute_query(
            "MATCH (m:DipinkIngestMetric {slug: $slug}) RETURN count(m) AS n",
            slug=slug,
            database_=getattr(driver, "_database", None),
        )
        return int(records[0]["n"]) if records else 0
    except Exception:  # noqa: BLE001
        return 0

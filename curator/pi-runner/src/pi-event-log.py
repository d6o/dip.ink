#!/usr/bin/env python3
"""Render Pi's verbose JSON event stream as concise CI telemetry."""

import json
import os
import sys
import time

started = time.monotonic()
turns = 0
tool_started = {}
tool_count = 0
tool_errors = 0
compactions = 0
retries = 0
final_text = ""
usage_totals = {}
api_keys = {
    value
    for value in (os.environ.get("PI_API_KEY", ""),)
    if value
}


def elapsed() -> str:
    return f"{time.monotonic() - started:.1f}s"


def safe(value, limit=4000) -> str:
    text = str(value).replace("\r", "")
    for api_key in api_keys:
        text = text.replace(api_key, "<redacted>")
    if len(text) > limit:
        return text[:limit] + f"\n... <truncated {len(text) - limit} chars>"
    return text


def log(message: str) -> None:
    print(f"[pi {elapsed()}] {message}", file=sys.stderr, flush=True)


def message_text(message) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        item.get("text", "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    )


def usage_summary(usage) -> str:
    if not isinstance(usage, dict):
        return ""
    fields = []
    for key in ("input", "output", "cacheRead", "cacheWrite", "reasoning", "totalTokens"):
        if key in usage:
            fields.append(f"{key}={usage[key]}")
    cost = usage.get("cost")
    if isinstance(cost, dict) and "total" in cost:
        fields.append(f"cost={cost['total']}")
    elif cost is not None:
        fields.append(f"cost={cost}")
    return " ".join(fields)


if "--stderr" in sys.argv[1:]:
    for raw_line in sys.stdin:
        line = raw_line.rstrip("\r\n")
        if line:
            log("stderr: " + safe(line, 1000))
    raise SystemExit(0)


for raw_line in sys.stdin:
    try:
        event = json.loads(raw_line)
    except json.JSONDecodeError:
        log("warning: ignored malformed JSON event")
        continue

    event_type = event.get("type")
    if event_type == "session":
        log(f"session started id={str(event.get('id', 'unknown'))[:8]}")
    elif event_type == "turn_start":
        turns += 1
        log(f"turn {turns} started")
    elif event_type == "tool_execution_start":
        tool_count += 1
        call_id = str(event.get("toolCallId", "unknown"))
        tool_started[call_id] = time.monotonic()
        log(f"tool start: {safe(event.get('toolName', 'unknown'), 80)}")
    elif event_type == "tool_execution_end":
        call_id = str(event.get("toolCallId", "unknown"))
        duration = time.monotonic() - tool_started.pop(call_id, time.monotonic())
        is_error = bool(event.get("isError"))
        tool_errors += int(is_error)
        state = "error" if is_error else "ok"
        log(f"tool end: {safe(event.get('toolName', 'unknown'), 80)} {state} ({duration:.1f}s)")
    elif event_type == "compaction_start":
        compactions += 1
        log(f"compaction started reason={safe(event.get('reason', 'unknown'), 80)}")
    elif event_type == "compaction_end":
        state = "aborted" if event.get("aborted") else "complete"
        retry = " retrying" if event.get("willRetry") else ""
        log(f"compaction {state}{retry}")
    elif event_type == "auto_retry_start":
        retries += 1
        log(
            f"retry {event.get('attempt', '?')}/{event.get('maxAttempts', '?')} "
            f"after {event.get('delayMs', '?')}ms: {safe(event.get('errorMessage', ''), 300)}"
        )
    elif event_type == "auto_retry_end":
        state = "succeeded" if event.get("success") else "failed"
        log(f"retry {event.get('attempt', '?')} {state}")
    elif event_type == "message_end":
        message = event.get("message", {})
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = message_text(message)
            if text:
                final_text = text
            usage = message.get("usage")
            if isinstance(usage, dict):
                for key in ("input", "output", "cacheRead", "cacheWrite", "reasoning", "totalTokens"):
                    value = usage.get(key)
                    if isinstance(value, (int, float)):
                        usage_totals[key] = usage_totals.get(key, 0) + value
                cost = usage.get("cost")
                value = cost.get("total") if isinstance(cost, dict) else cost
                if isinstance(value, (int, float)):
                    usage_totals["cost"] = usage_totals.get("cost", 0) + value

if final_text:
    log("final response:\n" + safe(final_text))
if usage_totals:
    summary = usage_summary(usage_totals)
    if summary:
        log("run usage: " + safe(summary, 500))
log(
    f"stream ended turns={turns} tools={tool_count} tool_errors={tool_errors} "
    f"compactions={compactions} retries={retries}"
)

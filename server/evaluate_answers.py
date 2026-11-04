"""Offline fixture validation or sequential live capture for human review.

Private questions/responses go only to a new mode-0600 JSONL artifact. Console
output contains counts and hashes, never questions, answers, or credentials.
Literal claim checks are review hints, not accuracy or entailment judgments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime
import time
import urllib.request
import urllib.parse

USER_AGENT = "OpenAI File Downloader, XaiImageApiFetch/1.0"
MAX_BYTES = 8 * 1024 * 1024


def hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def load_dataset(path):
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1 or not isinstance(data.get("dataset_version"), str):
        raise ValueError("invalid dataset version")
    cases = data.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        raise ValueError("invalid dataset size")
    ids = set()
    for case in cases:
        if not isinstance(case.get("id"), str) or case["id"] in ids:
            raise ValueError("invalid case identity")
        ids.add(case["id"])
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            raise ValueError("full question required")
        for key in ("expected_claims", "forbidden_claims"):
            if not isinstance(case.get(key), list) or not all(isinstance(x, str) for x in case[key]):
                raise ValueError("claim lists required")
        if not isinstance(case.get("reviewed_sources"), list):
            raise ValueError("reviewed source list required")
        for source in case["reviewed_sources"]:
            if not isinstance(source.get("slug"), str) or not source["slug"] or not isinstance(source.get("date"), str):
                raise ValueError("reviewed source identity and date required")
            datetime.fromisoformat(source["date"].replace("Z", "+00:00"))
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class MemoryClient:
    """Minimal read-only MCP client. No automatic retries or write tools."""
    def __init__(self, endpoint, timeout=600):
        parts = urllib.parse.urlsplit(endpoint)
        if parts.scheme not in {"http", "https"} or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("invalid endpoint")
        if not 1 <= timeout <= 900:
            raise ValueError("invalid timeout")
        self.endpoint, self.timeout = endpoint, timeout
        self.session = None
        self.serial = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def rpc(self, method, params, *, notification=False):
        self.serial += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = self.serial
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["mcp-session-id"] = self.session
        request = urllib.request.Request(self.endpoint, data=json.dumps(body).encode(), headers=headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            self.session = response.headers.get("mcp-session-id") or self.session
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("response too large")
        if notification:
            return None
        text = raw.decode()
        try:
            messages = json.loads(text)
            messages = messages if isinstance(messages, list) else [messages]
        except json.JSONDecodeError:
            blocks = text.replace("\r\n", "\n").split("\n\n")
            messages = [json.loads("\n".join(line[5:].strip() for line in block.splitlines() if line.startswith("data:")))
                        for block in blocks if any(line.startswith("data:") for line in block.splitlines())]
        message = next(m for m in messages if m.get("id") == self.serial)
        if message.get("error"):
            raise RuntimeError("MCP request failed")
        return message["result"]

    def initialize(self):
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "dipink-evaluation", "version": "1"}})
        self.rpc("notifications/initialized", {}, notification=True)

    def answer(self, question):
        result = self.rpc("tools/call", {"name": "graph_answer", "arguments": {"question": question}})
        if result.get("isError"):
            raise RuntimeError("memory tool failed")
        parsed = result.get("structuredContent")
        if parsed is None:
            parsed = json.loads(next(c["text"] for c in result.get("content", []) if c.get("type") == "text"))
        if not isinstance(parsed, dict):
            raise ValueError("invalid answer")
        return parsed


def fixture(case):
    import graph
    value, _, action = graph._validate_distilled_answer(case["draft"], case["packet"], case["question"])
    return value, action == case["expected_action"]


def classify(response):
    if not isinstance(response, dict):
        raise ValueError("invalid answer shape")
    if response.get("confidence") == "error" or "error_type" in response:
        return "error"
    if (response.get("confidence") not in {"high", "medium", "low", "not_found"}
            or "answer" not in response or not isinstance(response.get("answer"), (str, type(None)))
            or not isinstance(response.get("sources", []), list)):
        raise ValueError("invalid answer shape")
    return "abstention" if not response.get("answer") or response.get("confidence") == "not_found" else "answer"


def review_hints(case, response):
    text = str(response.get("answer") or "").casefold()
    return {"expected_literal_hits": [claim.casefold() in text for claim in case["expected_claims"]],
            "forbidden_literal_hits": [claim.casefold() in text for claim in case["forbidden_claims"]],
            "unreviewed_citations": len(set(response.get("sources") or []) - {s["slug"] for s in case["reviewed_sources"]})}


def summary(metadata, rows, reviews=None):
    total = metadata["case_count"]
    ids = [row["id"] for row in rows]
    if (total < 1 or len(ids) > total or len(set(ids)) != len(ids)
            or not set(ids).issubset(metadata["case_ids"])
            or any(row["status"] not in {"answer", "abstention", "error"} for row in rows)):
        raise ValueError("invalid capture denominator or case identity")
    counts = {state: sum(row["status"] == state for row in rows) for state in ("answer", "abstention", "error")}
    counts["missing"] = total - len(rows)
    result = {"dataset_sha256": metadata["dataset_sha256"], "runner_sha256": metadata["runner_sha256"],
              "mode": metadata["mode"], "total": total, **counts,
              "fixture_passes": sum(row.get("fixture_pass") is True for row in rows) if metadata["mode"] == "fixture" else None,
              "human_reviewed": 0, "human_supported_fraction_of_all": None,
              "interpretation": "confidence and literal checks are not correctness"}
    if reviews is not None:
        if reviews.get("dataset_sha256") != metadata["dataset_sha256"] or reviews.get("run_id") != metadata["run_id"]:
            raise ValueError("review belongs to a different dataset or run")
        verdicts = reviews["verdicts"]
        by_id = {row["id"]: row for row in rows}
        if any(key not in by_id or value not in {"supported", "unsupported", "uncertain"} for key, value in verdicts.items()):
            raise ValueError("invalid review")
        if any(value == "supported" and by_id[key]["status"] == "error" for key, value in verdicts.items()):
            raise ValueError("an error cannot be a supported answer")
        result["human_reviewed"] = len(verdicts)
        result["human_supported_fraction_of_all"] = sum(v == "supported" for v in verdicts.values()) / total
    return result


def capture(data, output, *, mode, answer=None, label="unlabeled"):
    import uuid
    metadata = {"type": "run", "run_id": uuid.uuid4().hex, "dataset_version": data["dataset_version"],
                "dataset_sha256": hash_json(data), "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "case_count": len(data["cases"]), "case_ids": [case["id"] for case in data["cases"]],
                "mode": mode, "label": label}
    # Never overwrite an earlier run or create a world-readable private artifact.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    rows = []
    with os.fdopen(fd, "w") as file:
        file.write(json.dumps(metadata) + "\n"); file.flush()
        for case in data["cases"]:
            started = time.monotonic()
            passed = None
            try:
                if mode == "fixture":
                    response, passed = fixture(case)
                else:
                    response = answer(case["question"])
                state = classify(response)
                hints = review_hints(case, response)
            except Exception as error:
                response, state, hints = {"error_type": type(error).__name__}, "error", {}
            row = {"type": "case", "id": case["id"], "question": case["question"],
                   "expected_claims": case["expected_claims"], "forbidden_claims": case["forbidden_claims"],
                   "reviewed_sources": case["reviewed_sources"], "response": response,
                   "status": state, "fixture_pass": passed, "review_hints": hints,
                   "duration_ms": round((time.monotonic() - started) * 1000), "human_verdict": None}
            file.write(json.dumps(row, ensure_ascii=False) + "\n"); file.flush()
            rows.append(row)
    return summary(metadata, rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode in ("fixture", "live"):
        item = sub.add_parser(mode)
        item.add_argument("dataset", type=Path)
        item.add_argument("--output", required=True, type=Path)
        item.add_argument("--label", default="unlabeled")
        if mode == "live":
            item.add_argument("--endpoint", required=True)
            item.add_argument("--timeout", type=int, default=600)
            item.add_argument("--allow-synthetic-live", action="store_true")
    item = sub.add_parser("summarize")
    item.add_argument("capture", type=Path)
    item.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    if args.mode == "summarize":
        records = [json.loads(line) for line in args.capture.read_text().splitlines()]
        result = summary(records[0], records[1:], json.loads(args.reviews.read_text()) if args.reviews else None)
    else:
        data = load_dataset(args.dataset)
        answer = None
        if args.mode == "live":
            if data.get("synthetic") and not args.allow_synthetic_live:
                raise ValueError("synthetic live evaluation requires an explicitly seeded test instance")
            client = MemoryClient(args.endpoint, args.timeout)
            try:
                client.initialize()
                answer = client.answer
            except Exception as error:
                error_type = type(error).__name__
                answer = lambda question: {"confidence": "error", "error_type": error_type}
        result = capture(data, args.output, mode=args.mode, answer=answer, label=args.label)
    print(json.dumps(result, sort_keys=True))
    return int((args.mode == "fixture" and result["fixture_passes"] != result["total"])
               or result["error"] > 0 or result["missing"] > 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__}))
        raise SystemExit(1)

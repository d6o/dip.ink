#!/usr/bin/env python3
"""wikilint — enforce the wiki schema and conventions.

Usage:
    python3 scripts/wikilint.py              # scan everything under wiki/
    python3 scripts/wikilint.py wiki/foo.md  # scan specific files
    python3 scripts/wikilint.py --quiet      # only print issues (suppress summary)
    python3 scripts/wikilint.py --json       # machine-readable JSON output

Severity:
    error    — must fix (frontmatter parse, filename != H1, broken wikilink)
    warning  — should fix (case mismatch, status enum drift, malformed sources/dates/type/tags)
    info     — consider (page-length cap, with `length-exempt: true` escape hatch)

Exit code:
    0   clean
    1   errors found
    2   warnings (no errors)

The schema this enforces lives in AGENTS.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

# Add scripts/ to sys.path so `wikiutil` resolves whether invoked directly or imported.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wikiutil import WIKI_ROOT, is_archive, is_skipped  # noqa: E402

ALLOWED_TYPES = {"entity", "concept", "source", "synthesis", "decision", "log", "index"}
ENTITY_STATUSES = {
    "live", "degraded", "dormant", "retired", "broken",
    "candidate", "not-installed", "legacy",
}
DECISION_STATUSES = {"undecided", "decided", "superseded"}
LENGTH_CAPS = {"concept": 150, "entity": 250}

# Pages where filename != H1 by convention; not flagged.
H1_EXEMPT = {"index", "log", "MEMORY"}

# Issue types
ERR = "error"
WARN = "warning"
INFO = "info"


def list_pages(paths: list[str]) -> list[Path]:
    """Return wiki pages to lint, skipping README.md and frozen note folders."""
    if paths:
        out = []
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                out.extend(f for f in pp.rglob("*.md") if not is_skipped(f))
            elif pp.is_file() and not is_skipped(pp):
                out.append(pp)
        return out
    return [f for f in WIKI_ROOT.rglob("*.md") if not is_skipped(f)]


def read_pages(pages: list[Path]) -> dict[Path, dict]:
    """Return {path: {"content": str, "frontmatter": dict|None, "body": str, "lines": int, "name": str}}."""
    info = {}
    for fp in pages:
        try:
            content = fp.read_text(encoding="utf-8")
        except Exception as e:
            info[fp] = {"error": f"cannot read: {e}"}
            continue
        m = re.match(r"^---\n(.*?)\n---\n?", content, re.DOTALL)
        fm = None
        body = content
        fm_error = None
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError as e:
                fm_error = f"YAML parse error: {e}"
            body = content[m.end():]
        info[fp] = {
            "content": content,
            "frontmatter": fm,
            "frontmatter_error": fm_error,
            "body": body,
            "lines": content.count("\n") + (0 if content.endswith("\n") else 1),
            "name": fp.stem,
        }
    return info


def strip_code(text: str) -> str:
    """Strip fenced code blocks and inline code spans (so we don't lint examples)."""
    # Fenced ```...```
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Inline `...` (single-backtick spans, non-greedy, single-line)
    text = re.sub(r"`[^`\n]+`", "", text)
    return text


_WIKILINK_RE = re.compile(r"\[\[([^\]\n]+?)\]\]")


def parse_wikilink(raw: str) -> str:
    r"""Return the target portion of a wikilink, handling Markdown-table escaped pipes.

    [[Foo]]                -> "Foo"
    [[Foo|bar]]            -> "Foo"
    [[Foo\|bar]]           -> "Foo"  (escaped pipe in table cells)
    [[Foo#section]]        -> "Foo"  (anchor stripped)
    """
    # Replace escaped pipe with real pipe so the alias split works
    raw = raw.replace(r"\|", "|")
    target = raw.split("|", 1)[0].strip()
    target = target.split("#", 1)[0].strip()
    return target


def find_wikilinks(text: str) -> list[str]:
    """Return list of wikilink targets in the text (skipping code)."""
    stripped = strip_code(text)
    return [parse_wikilink(m.group(1)) for m in _WIKILINK_RE.finditer(stripped)]


def is_source_note_page(fp: Path) -> bool:
    """True for canonical source notes under wiki/sources/notes/YYYY/MM/DD/slug/slug.md."""
    parts = fp.parts
    for i, part in enumerate(parts[:-1]):
        if part == "sources" and i + 1 < len(parts) and parts[i + 1] == "notes":
            return fp.stem == fp.parent.name
    return False


def lint(pages_info: dict[Path, dict]) -> list[dict]:
    issues = []

    # Build name index for wikilink resolution (case-insensitive)
    name_to_path = {}
    for fp, info in pages_info.items():
        if "name" in info:
            name_to_path[info["name"].lower()] = info["name"]

    for fp, info in sorted(pages_info.items()):
        if "error" in info:
            issues.append({"severity": ERR, "file": str(fp), "msg": info["error"]})
            continue

        fm = info["frontmatter"]
        body = info["body"]
        name = info["name"]

        # 1. Frontmatter parse
        if info["frontmatter_error"]:
            issues.append({"severity": ERR, "file": str(fp), "msg": info["frontmatter_error"]})
            continue
        if fm is None:
            issues.append({"severity": ERR, "file": str(fp), "msg": "no frontmatter"})
            continue

        typ = fm.get("type")

        # 2. Filename matches H1
        if name not in H1_EXEMPT and not is_archive(fp):
            h1m = re.search(r"^# (.+?)$", body, re.MULTILINE)
            if h1m:
                h1 = h1m.group(1).strip()
                if h1 != name:
                    issues.append({
                        "severity": ERR, "file": str(fp),
                        "msg": f"filename {name!r} != H1 {h1!r}",
                    })

        # 3. type enum
        if typ is None:
            issues.append({"severity": WARN, "file": str(fp), "msg": "type missing"})
        elif typ not in ALLOWED_TYPES:
            issues.append({
                "severity": WARN, "file": str(fp),
                "msg": f"type {typ!r} not in {sorted(ALLOWED_TYPES)}",
            })

        # 4. status enum (per type)
        status = fm.get("status")
        if status is not None:
            if typ == "entity" and status not in ENTITY_STATUSES:
                issues.append({
                    "severity": WARN, "file": str(fp),
                    "msg": f"entity status {status!r} not in {sorted(ENTITY_STATUSES)}",
                })
            elif typ == "decision" and status not in DECISION_STATUSES:
                issues.append({
                    "severity": WARN, "file": str(fp),
                    "msg": f"decision status {status!r} not in {sorted(DECISION_STATUSES)}",
                })
            elif typ in ("concept", "synthesis", "source", "log", "index") and status:
                # Most non-entity/decision pages shouldn't have a status; flag as info.
                issues.append({
                    "severity": INFO, "file": str(fp),
                    "msg": f"{typ} pages don't take a status (got {status!r})",
                })

        # 5. tags lowercase + hyphenated
        tags = fm.get("tags")
        if tags is not None:
            if not isinstance(tags, list):
                issues.append({
                    "severity": WARN, "file": str(fp),
                    "msg": f"tags not a list (got {type(tags).__name__})",
                })
            else:
                for t in tags:
                    if not isinstance(t, str):
                        issues.append({
                            "severity": WARN, "file": str(fp),
                            "msg": f"tag {t!r} not a string",
                        })
                        continue
                    if t != t.lower():
                        issues.append({
                            "severity": WARN, "file": str(fp),
                            "msg": f"tag {t!r} should be lowercase",
                        })
                    if " " in t:
                        issues.append({
                            "severity": WARN, "file": str(fp),
                            "msg": f"tag {t!r} should be hyphenated (no spaces)",
                        })

        # 6. dates
        for key in ("created", "updated"):
            if key in fm:
                v = fm[key]
                # Accept YYYY-MM-DD strings or date objects
                ok = (isinstance(v, str) and bool(re.match(r"^\d{4}-\d{2}-\d{2}$", v))) \
                    or hasattr(v, "isoformat")
                if not ok:
                    issues.append({
                        "severity": WARN, "file": str(fp),
                        "msg": f"{key} not YYYY-MM-DD: {v!r}",
                    })

        # 7. sources is YAML list of strings (wikilinks)
        if "sources" in fm:
            v = fm["sources"]
            if not isinstance(v, list):
                issues.append({
                    "severity": WARN, "file": str(fp),
                    "msg": f"sources not a list (got {type(v).__name__})",
                })
            else:
                for s in v:
                    if not isinstance(s, str):
                        issues.append({
                            "severity": WARN, "file": str(fp),
                            "msg": f"sources entry {s!r} not a string",
                        })

        # 8. decision-type frontmatter completeness
        if typ == "decision":
            if "status" not in fm:
                issues.append({
                    "severity": WARN, "file": str(fp),
                    "msg": "decision page missing status",
                })
            if fm.get("status") == "superseded" and "superseded-by" not in fm:
                issues.append({
                    "severity": WARN, "file": str(fp),
                    "msg": "superseded decision should have superseded-by: \"[[X]]\"",
                })

        # 9. source-note path shape: wiki/sources/notes/YYYY/MM/DD/<slug>/<slug>.md
        # with the slug's date prefix matching the directory date. Catches
        # curator mis-nesting like notes/YYYY-MM-DD/<slug>/ or doubly nested
        # <slug>/<slug>/ folders.
        if is_source_note_page(fp):
            parts = fp.parts
            for i, part in enumerate(parts[:-1]):
                if part == "sources" and i + 1 < len(parts) and parts[i + 1] == "notes":
                    rel = parts[i + 2:]
                    ok = (
                        len(rel) == 5
                        and re.fullmatch(r"\d{4}", rel[0]) is not None
                        and re.fullmatch(r"\d{2}", rel[1]) is not None
                        and re.fullmatch(r"\d{2}", rel[2]) is not None
                        and rel[3].startswith(f"{rel[0]}-{rel[1]}-{rel[2]}-")
                    )
                    if not ok:
                        issues.append({
                            "severity": ERR, "file": str(fp),
                            "msg": "mis-nested source note; expected "
                                   "wiki/sources/notes/YYYY/MM/DD/<slug>/<slug>.md",
                        })
                    break

        # 10. wikilink targets exist (case-insensitive); flag case mismatch
        if not is_source_note_page(fp):
            for target in find_wikilinks(body):
                if not target:
                    continue
                tl = target.lower()
                if tl not in name_to_path:
                    issues.append({
                        "severity": ERR, "file": str(fp),
                        "msg": f"broken wikilink: [[{target}]]",
                    })
                elif name_to_path[tl] != target:
                    issues.append({
                        "severity": WARN, "file": str(fp),
                        "msg": f"case mismatch: [[{target}]] should be [[{name_to_path[tl]}]]",
                    })

        # Also lint the sources frontmatter wikilinks for case
        if isinstance(fm.get("sources"), list):
            for s in fm["sources"]:
                if not isinstance(s, str):
                    continue
                m = _WIKILINK_RE.search(s)
                if not m:
                    continue
                target = parse_wikilink(m.group(1))
                tl = target.lower()
                if tl and tl not in name_to_path:
                    issues.append({
                        "severity": ERR, "file": str(fp),
                        "msg": f"broken wikilink in sources: [[{target}]]",
                    })
                elif tl and name_to_path[tl] != target:
                    issues.append({
                        "severity": WARN, "file": str(fp),
                        "msg": f"case mismatch in sources: [[{target}]] should be [[{name_to_path[tl]}]]",
                    })

        # 10. page length caps
        cap = LENGTH_CAPS.get(typ)
        if cap is not None and not fm.get("length-exempt"):
            n = info["lines"]
            if n > cap:
                issues.append({
                    "severity": INFO, "file": str(fp),
                    "msg": f"{typ} page is {n} lines (cap {cap}); split or set `length-exempt: true`",
                })

    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    pages = list_pages(args.paths)
    if not pages:
        print("no pages to lint", file=sys.stderr)
        return 0

    info = read_pages(pages)
    issues = lint(info)

    by_severity = defaultdict(int)
    for i in issues:
        by_severity[i["severity"]] += 1

    if args.json:
        print(json.dumps({"issues": issues, "summary": dict(by_severity), "pages": len(pages)}, indent=2))
    else:
        for i in issues:
            print(f"{i['severity']:7s} {i['file']}: {i['msg']}")
        if not args.quiet:
            print(
                f"\n{len(pages)} pages scanned, "
                f"{by_severity[ERR]} errors, {by_severity[WARN]} warnings, {by_severity[INFO]} info",
                file=sys.stderr,
            )

    if by_severity[ERR]:
        return 1
    if by_severity[WARN]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

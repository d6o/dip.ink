#!/usr/bin/env python3
"""Read-only blocked/source comparison. Reports hashes and classifications, never bodies.

Normalization removes frontmatter and the first H1, normalizes line endings,
and trims trailing line whitespace. A normalized match is not deletion approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized_body(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("---\n"):
        match = re.match(r"\A---\n.*?\n---(?:\n|$)", text, re.S)
        if not match:
            raise ValueError("invalid frontmatter boundary")
        text = text[match.end():]
    text = text.lstrip("\n")
    text = re.sub(r"\A# [^\n]*(?:\n|$)", "", text, count=1)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip().encode()


def contents(folder: Path, page: Path, *, blocked=False):
    if folder.is_symlink() or page.is_symlink():
        raise ValueError("symlink")
    attachments = {}
    for file in sorted(folder.rglob("*")):
        if file.is_symlink():
            raise ValueError("symlink")
        if not file.is_file() or file == page or (blocked and file == folder / "BLOCKED.md"):
            continue
        attachments[str(file.relative_to(folder))] = digest(file.read_bytes())
    body = normalized_body(page)
    if not body:
        raise ValueError("empty normalized body")
    return digest(body), attachments


def audit(root: Path):
    blocked = root / "notes" / ".blocked"
    sources = root / "wiki" / "sources" / "notes"
    if not blocked.is_dir() or not sources.is_dir() or blocked.is_symlink() or sources.is_symlink():
        raise ValueError("required audit roots unavailable")
    by_slug = {}
    for file in sources.rglob("*.md"):
        if file.stem == file.parent.name:
            by_slug.setdefault(file.stem, []).append(file)
    for folder in sorted(blocked.iterdir()):
        if folder.name.startswith("."):
            continue
        if folder.is_symlink():
            yield {"slug": folder.name, "classification": "unreadable", "error_type": "Symlink"}
            continue
        if not folder.is_dir():
            continue
        row = {"slug": folder.name, "comparison": "normalized_body_and_exact_attachments"}
        try:
            matches = by_slug.get(folder.name, [])
            if not matches:
                yield dict(row, classification="missing")
                continue
            candidates = [p for p in (folder / (folder.name + ".md"), folder / "NOTE.md") if p.is_file()]
            if len(candidates) != 1 or len(matches) != 1:
                yield dict(row, classification="ambiguous", source_count=len(matches), note_count=len(candidates))
                continue
            left, la = contents(folder, candidates[0], blocked=True)
            right, ra = contents(matches[0].parent, matches[0])
            yield dict(row, classification="exact_duplicate" if (left, la) == (right, ra) else "divergent",
                       body_equal=left == right, attachments_equal=la == ra,
                       blocked_body_sha256=left, source_body_sha256=right,
                       blocked_attachments=len(la), source_attachments=len(ra))
        except (OSError, ValueError, UnicodeError) as error:
            yield dict(row, classification="unreadable", error_type=type(error).__name__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Read-only wiki repository checkout")
    args = parser.parse_args()
    try:
        for row in audit(args.root):
            print(json.dumps(row, sort_keys=True))
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__}))
        raise SystemExit(1)

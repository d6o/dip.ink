#!/usr/bin/env python3
"""Exit 0 when an exact note slug appears in an ingest log entry.

Only `ingest |` and `auto-ingest |` sections are considered. Matching uses
slug boundaries so similarly prefixed folders do not collide.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
ENTRY_RE = re.compile(r"^## \[[^\]]+\] (?:auto-)?ingest \|")
HEADING_RE = re.compile(r"^## ")


def log_paths() -> list[Path]:
    paths = [Path("wiki/log.md")]
    archive_dir = Path("wiki/log")
    if archive_dir.is_dir():
        paths.extend(sorted(archive_dir.glob("*.md")))
    return [path for path in paths if path.is_file()]


def entry_contains(path: Path, pattern: re.Pattern[str]) -> bool:
    in_ingest_entry = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if HEADING_RE.match(line):
            in_ingest_entry = bool(ENTRY_RE.match(line))
            continue
        if in_ingest_entry and pattern.search(line):
            return True
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <note-slug>", file=sys.stderr)
        return 2

    slug = sys.argv[1]
    if not SLUG_RE.fullmatch(slug):
        print(f"invalid note slug: {slug}", file=sys.stderr)
        return 2

    pattern = re.compile(rf"(?<![A-Za-z0-9._-]){re.escape(slug)}/?(?![A-Za-z0-9._-])")
    for path in log_paths():
        if entry_contains(path, pattern):
            print(path)
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

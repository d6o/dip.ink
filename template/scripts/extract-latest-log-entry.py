#!/usr/bin/env python3
"""Print the most-recent `## [...]` entry from wiki/log.md.

Used by the curator workflow to extract the release
body. Reads wiki/log.md (relative to repo root), finds the first
`## [` heading (entries are append-at-top), and prints from that
heading through the character before the next `## [` heading or EOF.

Exits 1 with stderr message if no entry found. Exits 0 with empty
stdout if log.md is missing (caller decides what to do).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

LOG = Path("wiki/log.md")
ZERO_REVIEW_QUEUE_LINE = re.compile(
    r"^Review queue:\s*(?:0 entries added|none\b).*?(?:\n|$)",
    flags=re.MULTILINE,
)


def main() -> int:
    if not LOG.exists():
        return 0
    text = LOG.read_text(encoding="utf-8")
    # Match the first ## [...] heading (anchored to line start). Capture
    # from that heading through the line before the next ## [ heading or
    # EOF. Entries are top-of-file so the first match is the newest.
    m = re.search(
        r"^## \[.+?$.*?(?=^## \[|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not m:
        print("no `## [` entry found in wiki/log.md", file=sys.stderr)
        return 1
    body = ZERO_REVIEW_QUEUE_LINE.sub("", m.group(0)).strip()
    sys.stdout.write(body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

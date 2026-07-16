#!/usr/bin/env python3
"""logrotate — move old wiki/log.md entries into wiki/log/YYYY-Www.md.

Each entry in wiki/log.md starts with `## [YYYY-MM-DD ...] <action> | <title>`.
Entries older than KEEP_DAYS (default 14) are moved to per-week archive files
under wiki/log/. The most recent KEEP_DAYS of entries stay in wiki/log.md plus a
header that points at the archive.

Usage:
    python3 scripts/logrotate.py              # rotate, default 14-day window
    python3 scripts/logrotate.py --days 60    # different window
    python3 scripts/logrotate.py --check      # exit 1 if rotation would change anything
    python3 scripts/logrotate.py --dry-run    # print plan, don't write
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

WIKI_ROOT = Path("wiki")
LOG_PATH = WIKI_ROOT / "log.md"
ARCHIVE_DIR = WIKI_ROOT / "log"
KEEP_DAYS = 14

ENTRY_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})(?:[^\]]*)\] ", re.MULTILINE)


def parse_log(content: str) -> tuple[str, list[tuple[dt.date, str]]]:
    """Return (header, list of (date, entry_text)).

    Header is everything before the first `## [...]` heading.
    Each entry includes its `## [YYYY-MM-DD] ...` line and body up to the next entry.
    """
    matches = list(ENTRY_RE.finditer(content))
    if not matches:
        return content, []
    header = content[: matches[0].start()]
    entries = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        date_str = m.group(1)
        try:
            d = dt.date.fromisoformat(date_str)
        except ValueError:
            print(f"skipping entry with invalid date {date_str!r}", file=sys.stderr)
            continue
        entries.append((d, content[m.start() : end]))
    return header, entries


def render_log(header: str, entries: list[tuple[dt.date, str]]) -> str:
    """Reassemble the log.md from header + entries (newest first)."""
    out = [header.rstrip()] if header.strip() else []
    for _, entry in entries:
        out.append(entry.rstrip())
    return "\n\n".join(out) + "\n"


def split_by_week(entries: list[tuple[dt.date, str]]) -> dict[str, list[tuple[dt.date, str]]]:
    by_week: dict[str, list[tuple[dt.date, str]]] = {}
    for d, e in entries:
        iso = d.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        by_week.setdefault(key, []).append((d, e))
    return by_week


def archive_header(week: str) -> str:
    return (
        f"---\ntype: log\n---\n\n"
        f"# log {week}\n\n"
        "Archive slice of `wiki/log.md`. Entries below were rotated out of the live "
        "log when they aged past 14 days. Append-only — nothing else writes to this file.\n\n"
        f"See [[log]] for the live log (last 14 days). See `wiki/log/` for sibling weeks.\n"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=KEEP_DAYS)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if rotation would change anything")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not LOG_PATH.exists():
        print(f"{LOG_PATH} does not exist", file=sys.stderr)
        return 0

    today = dt.date.today()
    cutoff = today - dt.timedelta(days=args.days)
    content = LOG_PATH.read_text(encoding="utf-8")
    header, entries = parse_log(content)

    keep, rotate = [], []
    for d, e in entries:
        (keep if d >= cutoff else rotate).append((d, e))

    if not rotate:
        if args.check or args.dry_run:
            print(f"nothing to rotate (cutoff {cutoff}; {len(entries)} entries all newer)")
        return 0

    by_week = split_by_week(rotate)

    if args.check:
        print(
            f"rotation pending: {len(rotate)} entries would move to "
            f"{len(by_week)} archive file(s)",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(f"cutoff: {cutoff} (today minus {args.days} days)")
        print(f"would keep {len(keep)} entries in wiki/log.md")
        print(f"would rotate {len(rotate)} entries into {len(by_week)} archive(s):")
        for week, items in sorted(by_week.items(), reverse=True):
            existing = ARCHIVE_DIR / f"{week}.md"
            verb = "append" if existing.exists() else "create"
            print(f"  {week}: {len(items)} entries ({verb} {existing})")
        return 0

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # Append to per-week archives. Within each archive, entries are stored
    # newest-first (matching log.md convention).
    for week, items in by_week.items():
        items.sort(key=lambda x: x[0], reverse=True)
        archive_path = ARCHIVE_DIR / f"{week}.md"
        if archive_path.exists():
            existing = archive_path.read_text(encoding="utf-8")
            existing_header, existing_entries = parse_log(existing)
            merged = items + existing_entries
            merged.sort(key=lambda x: x[0], reverse=True)
            new_archive = render_log(existing_header or archive_header(week), merged)
        else:
            new_archive = render_log(archive_header(week), items)
        archive_path.write_text(new_archive, encoding="utf-8")

    # Rewrite the live log with kept entries only.
    keep.sort(key=lambda x: x[0], reverse=True)
    new_log = render_log(header, keep)
    LOG_PATH.write_text(new_log, encoding="utf-8")

    print(
        f"rotated {len(rotate)} entries from log.md into "
        f"{len(by_week)} archive file(s). "
        f"{len(keep)} entries remain in log.md.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

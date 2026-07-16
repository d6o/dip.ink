#!/usr/bin/env python3
"""wikiindex — regenerate wiki/index.md from page frontmatter.

Reads every page under wiki/. Groups by `type` (entity, concept, synthesis,
decision, source). For entities, groups by `category:` from frontmatter.
For everything else, alphabetical. For sources, collapses to monthly counts
(the per-source list is too long to be useful in index.md; readers should
browse wiki/sources/notes/ directly or grep wiki/log.md).

Description text per entry comes from `index-description:` frontmatter when
present, otherwise the first non-empty paragraph after the H1, plain-text
truncated.

Usage:
    python3 scripts/wikiindex.py            # write wiki/index.md
    python3 scripts/wikiindex.py --check    # exit 1 if regenerated would differ
    python3 scripts/wikiindex.py --stdout   # print to stdout instead of writing
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wikiutil import WIKI_ROOT, is_skipped, read_frontmatter as _shared_read_frontmatter  # noqa: E402

INDEX_PATH = WIKI_ROOT / "index.md"

# Hardcoded ordering for entity categories. Add new ones here when invented.
# Order of entity-category sections in index.md. Adjust to taste — the
# curator prompts tell the agent to pick from this list and to propose new
# categories rather than invent them silently.
ENTITY_CATEGORY_ORDER = [
    "Infrastructure",
    "Services",
    "Projects",
    "Devices",
    "External services",
    "Tools",
    "People",
    "Legacy / orphaned",
]


read_frontmatter = _shared_read_frontmatter


def first_paragraph(content: str) -> str:
    """Return the first non-empty paragraph after the H1, plain text."""
    body = re.sub(r"^---\n.*?\n---\n?", "", content, count=1, flags=re.DOTALL)
    body = re.sub(r"^# .+?\n", "", body, count=1)
    # Strip leading blank lines
    body = body.lstrip()
    # First paragraph is everything until a blank line
    para = body.split("\n\n", 1)[0]
    # Plain-text-ify: drop wikilink aliases, drop markdown emphasis/code marks
    para = re.sub(r"\[\[([^\]|]+?)\|([^\]]+)\]\]", r"\2", para)  # [[X|Y]] -> Y
    para = re.sub(r"\[\[([^\]]+?)\]\]", r"\1", para)  # [[X]] -> X
    para = re.sub(r"`([^`]+)`", r"\1", para)
    para = re.sub(r"\*\*([^*]+)\*\*", r"\1", para)
    para = re.sub(r"\*([^*]+)\*", r"\1", para)
    para = re.sub(r"\s+", " ", para).strip()
    if len(para) > 220:
        # truncate at sentence boundary if possible
        cut = para[:220]
        last_period = cut.rfind(". ")
        if last_period > 100:
            return cut[: last_period + 1]
        return cut.rstrip() + "…"
    return para


def page_info(fp: Path) -> dict | None:
    content = fp.read_text(encoding="utf-8")
    fm = read_frontmatter(content)
    if fm is None:
        return None
    name = fp.stem
    desc = fm.get("index-description") or first_paragraph(content)
    return {
        "name": name,
        "type": fm.get("type"),
        "category": fm.get("category"),
        "status": fm.get("status"),
        "tags": fm.get("tags") or [],
        "created": fm.get("created"),
        "description": desc or "",
        "url": fm.get("url"),
        "path": fp,
    }


def collect() -> dict:
    pages = [f for f in WIKI_ROOT.rglob("*.md") if not is_skipped(f)]
    by_type = collections.defaultdict(list)
    for fp in pages:
        info = page_info(fp)
        if info is None:
            continue
        by_type[info["type"]].append(info)
    return by_type


def render_entry(p: dict) -> str:
    desc = p["description"].strip()
    if desc:
        return f"- [[{p['name']}]] — {desc}"
    return f"- [[{p['name']}]]"


def render_entities(entities: list[dict]) -> str:
    out = ["## Entities", ""]
    by_cat = collections.defaultdict(list)
    uncategorized = []
    for e in entities:
        if e["category"]:
            by_cat[e["category"]].append(e)
        else:
            uncategorized.append(e)

    seen_cats = set()
    for cat in ENTITY_CATEGORY_ORDER:
        members = sorted(by_cat.get(cat, []), key=lambda p: p["name"].lower())
        if not members:
            continue
        seen_cats.add(cat)
        out.append(f"### {cat}")
        for m in members:
            out.append(render_entry(m))
        out.append("")

    # Any unknown categories (warn, then output)
    extra_cats = [c for c in by_cat.keys() if c not in seen_cats]
    if extra_cats:
        for cat in sorted(extra_cats):
            members = sorted(by_cat[cat], key=lambda p: p["name"].lower())
            out.append(f"### {cat}")
            for m in members:
                out.append(render_entry(m))
            out.append("")
        print(
            f"warning: {len(extra_cats)} entity categor{'y' if len(extra_cats)==1 else 'ies'} "
            f"not in ENTITY_CATEGORY_ORDER (added at end): {', '.join(sorted(extra_cats))}",
            file=sys.stderr,
        )

    if uncategorized:
        out.append("### Uncategorized")
        out.append("")
        out.append(
            f"_{len(uncategorized)} entit{'y' if len(uncategorized)==1 else 'ies'} "
            "without a `category:` frontmatter field. Add one to slot them above._"
        )
        out.append("")
        for u in sorted(uncategorized, key=lambda p: p["name"].lower()):
            out.append(render_entry(u))
        out.append("")

    return "\n".join(out)


def render_simple_section(title: str, items: list[dict]) -> str:
    out = [f"## {title}", ""]
    for p in sorted(items, key=lambda x: x["name"].lower()):
        out.append(render_entry(p))
    out.append("")
    return "\n".join(out)


def render_decisions(decisions: list[dict]) -> str:
    out = ["## Decisions", ""]
    for p in sorted(decisions, key=lambda x: x["name"].lower()):
        status = p.get("status") or "?"
        desc = p["description"].strip()
        prefix = f"- [[{p['name']}]] **[{status}]**"
        out.append(f"{prefix} — {desc}" if desc else prefix)
    out.append("")
    return "\n".join(out)


def render_sources(sources: list[dict]) -> str:
    """Collapse sources into a monthly summary instead of one line per source."""
    out = ["## Sources", ""]
    out.append(
        f"{len(sources)} source pages, one per ingested document/note. "
        "Listed by month below; for the full list browse `wiki/sources/notes/` directly. "
        "For ingest narratives across batches see [[log]]."
    )
    out.append("")
    by_month = collections.Counter()
    by_month_pages = collections.defaultdict(list)
    for p in sources:
        created = p.get("created")
        if created is None:
            created = getattr(p.get("path"), "stem", "")
        created_s = str(created)
        # Month from `created: YYYY-MM-DD`, source-note slugs
        # `YYYY-MM-DD-HHMMSS-...`, or legacy `(YYYY-MM-DD)` suffixes.
        m = re.match(r"(\d{4}-\d{2})", created_s)
        if not m:
            m = re.match(r"(\d{4}-\d{2})", p["name"])
        if not m:
            m = re.search(r"\((\d{4}-\d{2})", p["name"])
        month = m.group(1) if m else "undated"
        by_month[month] += 1
        by_month_pages[month].append(p["name"])
    for month in sorted(by_month.keys(), reverse=True):
        out.append(f"- **{month}** — {by_month[month]} sources")
    out.append("")
    return "\n".join(out)


def render(by_type: dict) -> str:
    parts = [
        "---",
        "type: index",
        "---",
        "",
        "",
        "# index",
        "",
        "Catalog of every wiki page. **Auto-generated from page frontmatter** by `scripts/wikiindex.py` "
        "(invoked via `/wikilint` at the end of every `/processnotes`). Do not edit by hand — instead "
        "edit the source page's `index-description:` and `category:` fields, then regenerate.",
        "",
        "Read this first when answering queries.",
        "",
    ]

    parts.append(render_entities(by_type.get("entity", [])))
    parts.append(render_simple_section("Concepts", by_type.get("concept", [])))
    parts.append(render_simple_section("Syntheses", by_type.get("synthesis", [])))
    parts.append(render_decisions(by_type.get("decision", [])))
    parts.append(render_sources(by_type.get("source", [])))

    return "\n".join(parts).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if generated content differs from current index.md")
    ap.add_argument("--stdout", action="store_true", help="print to stdout instead of writing")
    args = ap.parse_args()

    by_type = collect()
    new_content = render(by_type)

    if args.stdout:
        sys.stdout.write(new_content)
        return 0

    if args.check:
        if not INDEX_PATH.exists():
            print("index.md does not exist", file=sys.stderr)
            return 1
        cur = INDEX_PATH.read_text(encoding="utf-8")
        if cur != new_content:
            print("index.md is out of date — run scripts/wikiindex.py to regenerate", file=sys.stderr)
            return 1
        return 0

    INDEX_PATH.write_text(new_content, encoding="utf-8")
    n_total = sum(len(v) for v in by_type.values())
    print(f"wrote index.md: {n_total} pages indexed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

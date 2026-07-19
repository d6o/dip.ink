"""wikiutil — shared helpers for the wiki maintenance scripts.

Imported by wikilint.py, wikiindex.py, and wikidistill.py. Single source of
truth for which paths are wiki content, which are source-note attachments, and
which are archive files exempt from filename↔H1 matching.
"""
from __future__ import annotations

import re
from pathlib import Path

WIKI_ROOT = Path("wiki")


def is_skipped(p: Path) -> bool:
    """True for paths walkers should ignore.

    - `README.md` anywhere — directory documentation, not wiki content.
    - Markdown attachments under `wiki/sources/notes/YYYY/MM/DD/<slug>/`.
      The canonical source-note page is `<slug>.md`; anything else in the
      folder is an attachment and should not be indexed as a wiki page.
    """
    if p.name == "README.md":
        return True
    parts = p.parts
    for i, part in enumerate(parts[:-1]):
        if part == "sources" and i + 1 < len(parts) and parts[i + 1] == "notes":
            if p.suffix != ".md":
                return True
            return p.stem != p.parent.name
    return False


def is_archive(p: Path) -> bool:
    """True for date-keyed archive files where filename ≠ H1 is intentional.

    - `wiki/log/YYYY-Www.md` — weekly log archives, written by `logrotate.py`.
      H1 is `# log YYYY-Www`, filename is `YYYY-Www`.
    - Legacy `wiki/log/YYYY-MM.md` monthly archives, if any exist.
    - `wiki/distill/YYYY-MM-DD.md` — periodic snapshots, written by
      `wikidistill.py`. H1 is `# Wiki distill (YYYY-MM-DD)`.

    These are auto-generated and date-keyed; lint exempts them from filename↔H1.
    """
    if "log" in p.parts and re.match(r"^\d{4}-W\d{2}$", p.stem):
        return True
    if "log" in p.parts and re.match(r"^\d{4}-\d{2}$", p.stem):
        return True
    if "distill" in p.parts and re.match(r"^\d{4}-\d{2}-\d{2}$", p.stem):
        return True
    return False


def read_frontmatter(content: str) -> dict | None:
    """Extract and parse YAML frontmatter from a markdown file's content.

    Returns:
      - `None` if there's no frontmatter block.
      - `{}` if the frontmatter is empty.
      - The parsed dict otherwise.
      - `None` (with no error raised) on YAML parse failure — the caller should
        re-parse via PyYAML directly if it needs to surface the error.
    """
    import yaml  # local import; some callers may run without yaml available

    m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return None
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None


def list_pages(root: Path = WIKI_ROOT) -> list[Path]:
    """Walk `root` and return all wiki content `*.md` files (skipping per `is_skipped`)."""
    return [f for f in root.rglob("*.md") if not is_skipped(f)]

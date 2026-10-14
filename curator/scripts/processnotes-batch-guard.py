#!/usr/bin/env python3
"""Check the prepared inbox without an LLM or third-party modules.

The parent retains the manifest digest outside the child session. This check
protects a batch boundary, not the repository against a hostile process.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys


class GuardError(Exception):
    """A safe error code. Never include note text in an error."""


def require(condition, code):
    if not condition:
        raise GuardError(code)


def exists(path):
    return os.path.lexists(path)


def inside(path, root):
    return path == root or root in path.parents


def check_repo_root(root):
    # A Git worktree can have a .git file instead of a directory.
    for candidate in (root, *root.parents):
        if exists(candidate / ".git"):
            require(candidate == root, "not-repository-root")
            return
    raise GuardError("not-repository-root")


def safe_path(root, relative):
    """Reject traversal and symbolic links in path ancestors."""
    parts = Path(relative).parts
    require(not Path(relative).is_absolute() and ".." not in parts, "invalid-path")
    path = root
    for part in parts:
        path = path / part
        if exists(path):
            require(not path.is_symlink(), "symlink-ancestor")
    return path


def read_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), "not-regular-file")
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode)
            == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode),
            "file-changed-during-check")
    return digest.hexdigest(), before.st_size


def check_link_target(item, target, root):
    """Check each link target before any filesystem access outside the root."""
    path = Path(os.path.abspath(item.parent / target))
    for _ in range(40):
        require(inside(path, root), "external-symlink")
        parts = path.relative_to(root).parts
        current = root
        for index, part in enumerate(parts):
            current = current / part
            if current.is_symlink():
                path = Path(os.path.abspath(current.parent / os.readlink(current)))
                path = path.joinpath(*parts[index + 1:])
                break
        else:
            return
    raise GuardError("symlink-cycle")


def snapshot(path, root):
    """Record path, type, mode, bytes, and link identity. Do not follow links."""
    result = {}
    if not exists(path):
        return result

    def visit(item, key):
        require(len(result) < 200000, "too-many-paths")
        info = item.lstat()
        entry = {"mode": stat.S_IMODE(info.st_mode)}
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(item)
            check_link_target(item, target, root)
            entry.update(type="link", target=target)
        elif stat.S_ISREG(info.st_mode):
            digest, size = read_file(item)
            entry.update(type="file", sha256=digest, size=size)
        elif stat.S_ISDIR(info.st_mode):
            entry.update(type="dir")
        else:
            raise GuardError("unsupported-file-type")
        result[key] = entry
        if entry["type"] == "dir":
            for child in sorted(item.iterdir(), key=lambda p: p.name):
                visit(child, f"{key}/{child.name}" if key else child.name)

    visit(path, "")
    return result


def notes_path(root, name):
    path = Path(name)
    if path.is_absolute():
        require(inside(path, root), "notes-outside-repo")
        path = path.relative_to(root)
    require(path.parts and str(path) != ".", "invalid-notes-root")
    return safe_path(root, str(path))


def inventories(notes):
    groups = {}
    for label, path in (("live", notes), ("held", notes / ".deferred"),
                        ("blocked", notes / ".blocked")):
        if not exists(path):
            groups[label] = {}
            continue
        require(path.is_dir() and not path.is_symlink(), "invalid-inbox-root")
        entries = {}
        for item in path.iterdir():
            if label == "live" and item.name in (".deferred", ".blocked"):
                continue
            if item.is_symlink():
                # Queue identities must be real entries, not aliases.
                raise GuardError("symlink-inbox-entry")
            elif item.is_dir() and not item.name.startswith("."):
                entries[item.name] = item
        groups[label] = entries
    return groups


def preflight(root, notes):
    groups = inventories(notes)
    seen = set()
    for entries in groups.values():
        require(not seen.intersection(entries), "duplicate-inbox-identity")
        seen.update(entries)
    for label in ("live", "held"):
        for slug, folder in groups[label].items():
            canonical, legacy = folder / f"{slug}.md", folder / "NOTE.md"
            require(not (canonical != legacy and exists(canonical) and exists(legacy)),
                    "ambiguous-note-source")
            require(not canonical.is_symlink() and not legacy.is_symlink(),
                    "symlink-note-source")
    if exists(notes):
        for item in notes.iterdir():
            if item.name.startswith(".") or item.name == "README.md" or not item.name.endswith(".md"):
                continue
            require(not item.is_symlink() and item.is_file(), "invalid-bare-note")
            slug = item.name[:-3]
            require(slug and not slug.startswith("."), "invalid-bare-identity")
            require(slug not in seen and not exists(notes / slug), "bare-note-collision")
            seen.add(slug)
    # Detect unsafe trees before the shell wraps or blocks any note.
    snapshot(notes, root)
    snapshot(safe_path(root, "raw"), root)
    source_tree = snapshot(safe_path(root, "wiki/sources/notes"), root)
    source_slugs = set()
    for name, entry in source_tree.items():
        path = Path(name)
        if entry["type"] == "file" and path.name == f"{path.parent.name}.md":
            require(path.parent.name not in source_slugs, "duplicate-source-identity")
            source_slugs.add(path.parent.name)
    return groups


def prepare(root, notes, cap):
    groups = preflight(root, notes)
    pool = {**groups["live"], **groups["held"]}
    selected = sorted(pool)[:cap]
    expected = {slug: snapshot(path, root) for slug, path in pool.items()}
    (notes / ".deferred").mkdir(parents=True, exist_ok=True)
    for slug in sorted(pool):
        destination = notes / slug if slug in selected else notes / ".deferred" / slug
        if pool[slug] != destination:
            require(not exists(destination), "move-destination-exists")
            pool[slug].rename(destination)
    actual = inventories(notes)
    require(sorted(actual["live"]) == selected and len(actual["live"]) <= cap,
            "prepared-selection-mismatch")
    require(sorted(actual["held"]) == sorted(set(pool) - set(selected)),
            "prepared-held-mismatch")
    for slug, path in {**actual["live"], **actual["held"]}.items():
        require(snapshot(path, root) == expected[slug], "preparation-changed-payload")
    return {"pool": len(pool), "live": len(selected), "held": len(actual["held"]),
            "blocked": len(actual["blocked"]), "cap": cap}


def first_h1(lines):
    """Do not treat a code line inside a Markdown fence as a title."""
    fence = None
    for index, line in enumerate(lines):
        if fence is not None:
            marker, length = fence
            closing = rf"^ {{0,3}}{re.escape(marker)}{{{length},}}[ \t]*(?:\r?\n)?$"
            if re.fullmatch(closing, line):
                fence = None
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)", line)
        if opening is not None:
            marker, suffix = opening.groups()
            if marker[0] != "`" or "`" not in suffix:
                fence = (marker[0], len(marker))
                continue
        if line.startswith("# "):
            return index
    return None


def source_parts(data):
    """Hash source text after the permitted frontmatter and H1 changes.

    Keep the original metadata blocks except the source schema fields. The
    comparison permits new schema fields and a new title, not a new body.
    """
    require(len(data) <= 16 * 1024 * 1024, "source-too-large")
    text = data.decode("utf-8")
    lines = text.splitlines(keepends=True)
    require(lines and lines[0].strip() == "---", "source-frontmatter-missing")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    require(end is not None, "source-frontmatter-missing")
    blocks = {}
    key = None
    for line in lines[1:end]:
        match = re.match(r"^(?:([A-Za-z0-9_-]+)|\"([A-Za-z0-9_-]+)\"|'([A-Za-z0-9_-]+)')\s*:", line)
        if match:
            key = next(value for value in match.groups() if value is not None)
            require(key not in blocks, "duplicate-frontmatter-key")
            blocks[key] = []
        if key is None:
            key = "__preamble"
            blocks[key] = []
        blocks[key].append(line)
    mutable = {"type", "tags", "created", "updated", "index-description", "name", "title"}
    metadata = {key: hashlib.sha256("".join(value).rstrip("\r\n").encode()).hexdigest()
                for key, value in blocks.items() if key not in mutable}
    body = lines[end + 1:]
    full_body = hashlib.sha256("".join(body).strip("\r\n").encode()).hexdigest()
    h1 = first_h1(body)
    title = body.pop(h1).rstrip("\r\n") if h1 is not None else None
    digest = hashlib.sha256("".join(body).strip("\r\n").encode()).hexdigest()
    return {"body": digest, "full_body": full_body, "metadata": metadata}, title, blocks


def source_input(folder, slug):
    canonical = folder / f"{slug}.md"
    legacy = folder / "NOTE.md"
    path = canonical if exists(canonical) else legacy
    require(exists(path) and path.is_file() and not path.is_symlink(), "selected-source-missing")
    require(not (exists(canonical) and exists(legacy) and canonical != legacy),
            "ambiguous-selected-source")
    with open(path, "rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    parts, _, _ = source_parts(data)
    return path.name, parts


def canonical_path(slug):
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})-", slug)
    require(match is not None, "canonical-date-missing")
    year, month, day = match.groups()
    dt.date(int(year), int(month), int(day))
    return f"wiki/sources/notes/{year}/{month}/{day}/{slug}"


def source_identity_exists(tree, slug):
    """Include source folders and canonical pages at any prior source path."""
    for name, entry in tree.items():
        path = Path(name)
        if path.name == slug and entry["type"] in ("dir", "link"):
            return True
        if path.parent.name == slug and path.name == f"{slug}.md":
            return True
    return False


def counts(state):
    return {"selected": len(state["selected"]), "held": state["held_count"],
            "blocked": state["blocked_count"], "cap": state["cap"]}


def capture(root, notes, cap, state_path):
    groups = preflight(root, notes)
    require(not any(not item.name.startswith(".") and item.name.endswith(".md")
                    and item.name != "README.md" for item in notes.iterdir()),
            "unprepared-bare-note")
    pool = sorted(set(groups["live"]) | set(groups["held"]))
    require(sorted(groups["live"]) == pool[:cap] and len(groups["live"]) <= cap,
            "capture-selection-mismatch")
    state = {"version": 1, "notes": str(notes.relative_to(root)), "cap": cap,
             "selected": {}, "held_count": len(groups["held"]),
             "blocked_count": len(groups["blocked"])}
    tree = snapshot(notes, root)
    for slug, folder in groups["live"].items():
        source_name, parts = source_input(folder, slug)
        state["selected"][slug] = {"tree": snapshot(folder, root),
                                    "source_name": source_name, "source": parts}
        tree = {path: entry for path, entry in tree.items()
                if path != slug and not path.startswith(slug + "/")}
    state["protected_notes"] = tree
    state["raw"] = snapshot(safe_path(root, "raw"), root)
    state["sources"] = snapshot(safe_path(root, "wiki/sources/notes"), root)
    # Capture both sides of each queue move, even if a source path did not exist.
    absent = []
    for slug in groups["held"]:
        absent.append(f"{state['notes']}/{slug}")
    for slug in groups["live"]:
        absent.append(f"{state['notes']}/.deferred/{slug}")
    for slug in groups["blocked"]:
        absent.extend((f"{state['notes']}/{slug}", f"{state['notes']}/.deferred/{slug}"))
    state["absent"] = absent
    require(all(not exists(root / path) for path in absent), "capture-absence-mismatch")
    state_path = Path(os.path.abspath(state_path))
    require(not inside(state_path.resolve(), root), "state-inside-repo")
    require(state_path.parent.is_dir(), "state-parent-missing")
    data = (json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    require(len(data) <= 64 * 1024 * 1024, "state-too-large")
    # A parent may supply an empty mktemp file. Never replace a populated state.
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size == 0, "state-not-empty")
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {"sha256": hashlib.sha256(data).hexdigest(), **counts(state)}


def check_blocked(folder, slug, prepared, root):
    tree = snapshot(folder, root)
    receipt = tree.pop("BLOCKED.md", None)
    require(receipt is not None and receipt["type"] == "file" and receipt["size"] <= 4096,
            "blocked-receipt-missing")
    require(tree == prepared["tree"], "blocked-payload-changed")
    text = (folder / "BLOCKED.md").read_text(encoding="utf-8")
    require(f"slug: {json.dumps(slug)}" in text.splitlines(), "blocked-receipt-identity")
    require(any(f"reason: {json.dumps(reason)}" in text.splitlines() for reason in
                ("already-ingested", "corrupt-input", "unparseable-input", "malformed-note",
                 "needs-operator-review")), "blocked-receipt-reason")


def check_canonical(folder, slug, prepared, root):
    tree = snapshot(folder, root)
    expected = dict(prepared["tree"])
    original = expected.pop(prepared["source_name"])
    canonical = tree.pop(f"{slug}.md", None)
    require(canonical is not None and canonical["type"] == "file"
            and original["mode"] == canonical["mode"], "canonical-source-missing")
    require(tree == expected, "canonical-attachments-changed")
    with open(folder / f"{slug}.md", "rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    parts, title, blocks = source_parts(data)
    # The curator can replace the first H1 or add an H1 before the original.
    require(parts["body"] in (prepared["source"]["body"], prepared["source"]["full_body"]),
            "canonical-body-changed")
    require(all(parts["metadata"].get(key) == value
                for key, value in prepared["source"]["metadata"].items()),
            "canonical-metadata-changed")
    require(title == f"# {slug}", "canonical-title-mismatch")
    require("type" in blocks and re.fullmatch(r"type:\s*['\"]?source['\"]?\s*",
                                             "".join(blocks["type"])),
            "canonical-type-mismatch")


def verify(root, state_path, digest, prepared_stage=False):
    require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "invalid-state-digest")
    require(not inside(Path(state_path).resolve(), root), "state-inside-repo")
    fd = os.open(state_path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600,
                "invalid-state-mode")
        data = stream.read(64 * 1024 * 1024 + 1)
    require(len(data) <= 64 * 1024 * 1024, "state-too-large")
    require(hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest), "state-digest-mismatch")
    state = json.loads(data)
    require(state["version"] == 1 and state["cap"] > 0, "invalid-state-schema")
    notes = notes_path(root, state["notes"])
    actual = inventories(notes)
    require(len(actual["live"]) <= state["cap"]
            and set(actual["live"]).issubset(state["selected"]), "live-boundary-changed")
    for path in state["absent"]:
        require(not exists(safe_path(root, path)), "prepared-absence-changed")
    require(snapshot(safe_path(root, "raw"), root) == state["raw"], "raw-changed")
    tree = snapshot(notes, root)
    source_tree = snapshot(safe_path(root, "wiki/sources/notes"), root)
    allowed_sources = set()
    for slug, prepared in state["selected"].items():
        live = notes / slug
        blocked = notes / ".blocked" / slug
        try:
            canonical_rel = canonical_path(slug)
        except (GuardError, ValueError):
            canonical_rel = None
        canonical = safe_path(root, canonical_rel) if canonical_rel else None
        baseline = canonical_rel.removeprefix("wiki/sources/notes/") if canonical_rel else None
        new_canonical = canonical is not None and exists(canonical) and baseline not in state["sources"]
        require(sum((exists(live), exists(blocked), new_canonical)) <= 1,
                "selected-action-ambiguous")
        if exists(live):
            require(canonical is None or not exists(canonical) or (prepared_stage and not new_canonical),
                    "canonicalized-input-still-live")
            require(snapshot(live, root) == prepared["tree"], "selected-live-changed")
            prefix = slug
        elif exists(blocked):
            check_blocked(blocked, slug, prepared, root)
            prefix = f".blocked/{slug}"
        elif new_canonical:
            require(not source_identity_exists(state["sources"], slug),
                    "canonical-identity-exists")
            check_canonical(canonical, slug, prepared, root)
            prefix = None
            for key in source_tree:
                if key == baseline or key.startswith(baseline + "/"):
                    allowed_sources.add(key)
            parts = Path(baseline).parts
            allowed_sources.update(str(Path(*parts[:i])) for i in range(1, len(parts)))
            allowed_sources.add("")
        else:
            prefix = None  # A selected folder can be dropped.
        if prefix is not None:
            tree = {key: value for key, value in tree.items()
                    if key != prefix and not key.startswith(prefix + "/")}
    require(tree == state["protected_notes"], "held-or-blocked-state-changed")
    for key, value in state["sources"].items():
        require(source_tree.get(key) == value, "existing-source-changed")
    require(set(source_tree) - set(state["sources"]) <= allowed_sources, "unexpected-source-path")
    return {"sha256": digest, **counts(state), "live": len(actual["live"])}


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # Argument errors can include arbitrary user text. Emit only a code.
        self.exit(2, "batch-guard: invalid-arguments\n")


def main():
    parser = SafeParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "prepare", "capture"):
        sub = commands.add_parser(command)
        sub.add_argument("--notes-dir", default="notes")
        if command != "preflight":
            sub.add_argument("--cap", type=int, required=True)
        if command == "capture":
            sub.add_argument("--state", required=True)
    sub = commands.add_parser("verify")
    sub.add_argument("--state", required=True)
    sub.add_argument("--sha256", required=True)
    sub.add_argument("--repo", required=True)
    sub.add_argument("--prepared", action="store_true", help="Check the captured pre-Pi state only")
    args = parser.parse_args()
    try:
        root = Path(args.repo if args.command == "verify" else os.getcwd()).resolve()
        require(root.is_dir(), "repo-missing")
        check_repo_root(root)
        if args.command == "verify":
            result = verify(root, args.state, args.sha256, prepared_stage=args.prepared)
        else:
            notes = notes_path(root, args.notes_dir)
            if args.command == "preflight":
                groups = preflight(root, notes)
                result = {key: len(entries) for key, entries in groups.items()}
            else:
                require(args.cap > 0, "invalid-cap")
                if args.command == "prepare":
                    result = prepare(root, notes, args.cap)
                else:
                    result = capture(root, notes, args.cap, args.state)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except GuardError as exc:
        print(f"batch-guard: {exc}", file=sys.stderr)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, RecursionError):
        # Exceptions can contain a note body or an unbounded path. Do not print them.
        print("batch-guard: invalid-or-unreadable-state", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())

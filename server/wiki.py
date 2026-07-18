"""wiki — the markdown-wiki side of the dip.ink memory server.

Indexes wiki/*.md (excluding frozen note folders + READMEs) using OpenAI
embeddings (default) or a local fastembed model. Registers on the shared
FastMCP instance (core.mcp):

  - MCP tools: wiki_search, wiki_get, wiki_backlinks, wiki_note_drop
  - Plain HTTP routes (mounted by server.py): /api/search, /api/page/<name>,
    /api/backlinks/<name>, /api/reindex, /live, /health

The wiki repo is mounted (or git-cloned) at WIKI_CLONE_PATH inside the
container.

Index strategy: bind HTTP first, then bootstrap in a supervised background
thread. Cached unchanged vectors are published with freshly scanned metadata
before changed pages are embedded; provider failures leave a ready degraded
baseline when possible, or a live/unready process when no valid cache exists.
Retries use bounded backoff. Normal refreshes hash each page and only re-embed
changed content. POST /reindex triggers an immediate refresh.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import os
import re
import shutil
import subprocess
import threading
import time

import anyio.to_thread
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from core import CACHE_DIR, log, mcp, now_iso as _now_iso, record_query as _record_query

# --- Config ---

WIKI_ROOT = Path(os.environ.get("WIKI_ROOT", "wiki"))
# When WIKI_REPO_URL is set, the pod clones (or refreshes) the wiki repo into
# WIKI_CLONE_PATH at startup, overrides WIKI_ROOT to that path, and uses the
# same clone for git push from the `wiki_note_drop` tool. When unset (local
# dev), the static WIKI_ROOT is used as-is and the write tool is disabled.
WIKI_REPO_URL = os.environ.get("WIKI_REPO_URL", "")
WIKI_BRANCH = os.environ.get("WIKI_BRANCH", "main")
WIKI_CLONE_PATH = Path(os.environ.get("WIKI_CLONE_PATH", "/var/lib/memory/wiki"))
# Token-based HTTPS auth for the wiki repo host (GitHub PAT, Gitea/GitLab API
# token, ...). Sent as HTTP Basic auth (username WIKI_REPO_USER, password
# WIKI_REPO_TOKEN) via git's http.<origin>.extraheader, so the token never
# appears in the remote URL. Without it the wiki_note_drop tool returns an
# error; pulls still work for public repos.
WIKI_REPO_TOKEN = os.environ.get("WIKI_REPO_TOKEN", "")
WIKI_REPO_USER = os.environ.get("WIKI_REPO_USER", "token")
GIT_USER_NAME = os.environ.get("WIKI_GIT_USER_NAME", "wiki-mcp")
GIT_USER_EMAIL = os.environ.get("WIKI_GIT_USER_EMAIL", "wiki-mcp@localhost")
# Embedding provider:
#   "openai"    — text-embedding-3-small via OpenAI API (1536-dim). The
#                 default. Cheap (fractions of a cent per full reindex) and
#                 gives much sharper cosine separation than the local fallback.
#   "fastembed" — BAAI/bge-small-en-v1.5 via local ONNX (384-dim). No API key
#                 needed; requires `pip install fastembed` (see requirements.txt).
EMBED_PROVIDER = os.environ.get("WIKI_MCP_EMBED_PROVIDER", "openai")
OPENAI_MODEL = os.environ.get("WIKI_MCP_OPENAI_MODEL", "text-embedding-3-small")
FASTEMBED_MODEL = os.environ.get("WIKI_MCP_FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
REINDEX_INTERVAL_SEC = int(os.environ.get("WIKI_MCP_REINDEX_SEC", "300"))
REINDEX_RETRY_INITIAL_SEC = int(os.environ.get("WIKI_MCP_RETRY_INITIAL_SEC", "5"))
REINDEX_RETRY_MAX_SEC = int(os.environ.get("WIKI_MCP_RETRY_MAX_SEC", "300"))
BACKGROUND_REINDEX_ENABLED = os.environ.get("WIKI_MCP_BACKGROUND_REINDEX", "1").lower() not in {
    "0", "false", "no", "disabled",
}


# --- Note-drop validation limits ---

# Used by the wiki_note_drop MCP tool. Matches the notes/ inbox protocol
# documented in the wiki repo's AGENTS.md.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
NOTE_BODY_MAX = 256 * 1024              # 256 KB source-note body cap
ATTACHMENT_COUNT_MAX = 20
ATTACHMENT_TEXT_MAX = 256 * 1024        # 256 KB per text attachment
ATTACHMENT_BINARY_MAX = 2 * 1024 * 1024  # 2 MB per binary attachment (decoded)

# Single-flight lock around any git op touching the working tree (clone, pull,
# fetch+reset, commit, push). Both the periodic reindex pull and the note-drop
# tool acquire this lock; reads (search, get, backlinks) don't.
_repo_lock = threading.Lock()


@dataclass(frozen=True)
class ExistingNoteDrop:
    folder: str
    relative_dir: str
    archived: bool


_capture_hash_lock = threading.Lock()
_capture_hash_root: Path | None = None
_capture_hash_revision_value: str | None = None
_capture_hash_index: dict[str, list[ExistingNoteDrop]] = {}


# --- Git plumbing for clone-at-startup, periodic pull, and note-drop push ---


def _run_git(*args: str, cwd: Path | str | None = None, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a git command, capturing stdout+stderr. Raises RuntimeError on
    non-zero exit when check=True so callers can wrap in try/except."""
    cmd: list[str] = ["git"]
    if cwd is not None:
        cmd.extend(["-C", str(cwd)])
    cmd.extend(args)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (rc={res.returncode}): {res.stderr.strip()}")
    return res


def _extraheader_key() -> str:
    """git config key scoping the auth header to the wiki repo's origin."""
    m = re.match(r"^(https?://[^/]+)/", WIKI_REPO_URL)
    origin = m.group(1) if m else WIKI_REPO_URL
    return f"http.{origin}/.extraheader"


def _auth_header_value() -> str:
    creds = base64.b64encode(f"{WIKI_REPO_USER}:{WIKI_REPO_TOKEN}".encode()).decode()
    return f"Authorization: Basic {creds}"


_EXTRAHEADER_KEY = _extraheader_key()


def _clear_stale_git_locks() -> None:
    """Remove leftover git lock files (e.g. .git/index.lock after the container
    was killed mid-commit). Only safe because every caller holds _repo_lock and
    nothing else runs git against this clone."""
    git_dir = WIKI_CLONE_PATH / ".git"
    if not git_dir.is_dir():
        return
    for lock in git_dir.rglob("*.lock"):
        try:
            lock.unlink()
            log.warning("removed stale git lock %s", lock)
        except FileNotFoundError:
            pass


def _clone_or_pull_wiki() -> Path | None:
    """If WIKI_REPO_URL is set, ensure a writable clone exists at WIKI_CLONE_PATH
    (clone on first run, fetch+reset on subsequent runs) and return the path.
    Also persists committer identity and the Authorization extraheader so
    later fetches and pushes work without the token appearing in the remote URL.
    Returns None when WIKI_REPO_URL is unset — caller keeps the static WIKI_ROOT.
    """
    if not WIKI_REPO_URL:
        log.info("WIKI_REPO_URL not set; using static WIKI_ROOT=%s (note-drop disabled)", WIKI_ROOT)
        return None

    # Pass the auth header inline for any git command issued BEFORE the persisted
    # config exists (initial clone). For commands inside an existing clone we
    # rely on the persisted extraheader (set further down).
    inline_auth: list[str] = []
    if WIKI_REPO_TOKEN:
        inline_auth = ["-c", f"{_EXTRAHEADER_KEY}={_auth_header_value()}"]

    git_dir = WIKI_CLONE_PATH / ".git"
    if git_dir.exists():
        log.info("refreshing existing clone at %s (branch=%s)", WIKI_CLONE_PATH, WIKI_BRANCH)
        _clear_stale_git_locks()
        try:
            _run_git("fetch", "origin", cwd=WIKI_CLONE_PATH)
            _run_git("reset", "--hard", f"origin/{WIKI_BRANCH}", cwd=WIKI_CLONE_PATH)
            _run_git("clean", "-fd", cwd=WIKI_CLONE_PATH)
        except Exception as e:
            log.warning("startup refresh failed: %s; continuing with existing tree", e)
    else:
        log.info("cloning %s into %s (branch=%s)", WIKI_REPO_URL, WIKI_CLONE_PATH, WIKI_BRANCH)
        WIKI_CLONE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Clone failures are caught by the background supervisor; /live stays up
        # and the attempt is retried with bounded backoff.
        _run_git(
            *inline_auth,
            "clone", "--depth=1", "--branch", WIKI_BRANCH,
            WIKI_REPO_URL, str(WIKI_CLONE_PATH),
            timeout=180,
        )
    # Always (re)write committer identity + auth header — picks up a rotated
    # token on next pod restart without having to manually clear .git/config.
    _run_git("config", "user.email", GIT_USER_EMAIL, cwd=WIKI_CLONE_PATH)
    _run_git("config", "user.name", GIT_USER_NAME, cwd=WIKI_CLONE_PATH)
    if WIKI_REPO_TOKEN:
        _run_git("config", _EXTRAHEADER_KEY, _auth_header_value(), cwd=WIKI_CLONE_PATH)
    else:
        # Clear any stale token from a previous run so we don't fetch as a
        # ghost identity when WIKI_REPO_TOKEN is intentionally unset.
        _run_git("config", "--unset-all", _EXTRAHEADER_KEY, cwd=WIKI_CLONE_PATH, check=False)
        log.warning("WIKI_REPO_TOKEN not set; wiki_note_drop will return write-disabled errors")
    return WIKI_CLONE_PATH


# Never clone or index during module import: HTTP must bind even when git or the
# embedding provider is unavailable. The supervised background bootstrap below
# prepares this path and retries. A PVC-backed existing clone remains readable.
if WIKI_REPO_URL:
    WIKI_ROOT = WIKI_CLONE_PATH


# --- Wiki traversal helpers (mirrors scripts/wikiutil.py) ---


def is_skipped(p: Path) -> bool:
    """True for paths to ignore: README.md and source-note attachments."""
    if p.name == "README.md":
        return True
    parts = p.parts
    for i, part in enumerate(parts[:-1]):
        if part == "sources" and i + 1 < len(parts) and parts[i + 1] == "notes":
            if p.suffix != ".md":
                return True
            return p.stem != p.parent.name
    return False


def read_frontmatter_and_body(content: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n?", content, re.DOTALL)
    if not m:
        return {}, content
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, content[m.end():]


def hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def find_wikilinks(body: str) -> set[str]:
    """Return set of wikilink targets in body (skipping fenced code)."""
    stripped = re.sub(r"```[\s\S]*?```", "", body)
    stripped = re.sub(r"`[^`\n]+`", "", stripped)
    out = set()
    for m in re.finditer(r"\[\[([^\]\n]+?)\]\]", stripped):
        raw = m.group(1).replace(r"\|", "|")
        target = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if target:
            out.add(target)
    return out


def note_drop_payload_hash(note_md: str, decoded_text: dict[str, bytes], decoded_bin: dict[str, bytes]) -> str:
    """Stable idempotency key for a note-drop request."""
    h = hashlib.sha256()
    h.update(b"note_md\0")
    h.update(note_md.encode("utf-8"))
    for name, content in sorted(decoded_text.items()):
        h.update(b"\0text\0")
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(content)
    for name, content in sorted(decoded_bin.items()):
        h.update(b"\0binary\0")
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(content)
    return h.hexdigest()


def source_note_markdown(folder_name: str, note_md: str, capture_hash: str | None = None) -> str:
    """Return a wiki-compatible source page for an inbox note.

    Capturing agents still pass the old NOTE.md shape (`captured`, `session`,
    `topic`). The inbox file now uses the final source-page filename so the
    auto-curator can move it into `wiki/sources/notes/YYYY/MM/DD/...` without
    creating a second stub page.
    """
    fm, body = read_frontmatter_and_body(note_md)
    date = folder_name[:10]
    tags = fm.get("tags") if isinstance(fm.get("tags"), list) else []
    clean_tags = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        slug = re.sub(r"[^a-z0-9-]+", "-", tag.lower()).strip("-")
        if slug:
            clean_tags.append(slug)
    if "source-note" not in clean_tags:
        clean_tags.insert(0, "source-note")

    session = fm.get("session") or ""
    topic = fm.get("topic") or ""
    desc = fm.get("index-description") or session or topic or f"Captured source note {folder_name}."
    desc = str(desc).strip()
    if desc and not desc.endswith("."):
        desc += "."

    new_fm = {
        "type": "source",
        "tags": clean_tags,
        "created": date,
        "updated": date,
        "index-description": desc[:220],
    }
    if capture_hash:
        new_fm["capture-hash"] = capture_hash
    for key in ("captured", "session", "topic"):
        if key in fm:
            new_fm[key] = fm[key]
    for key, value in fm.items():
        if key not in new_fm:
            new_fm[key] = value

    body = body.lstrip()
    if not body.startswith(f"# {folder_name}\n"):
        body = f"# {folder_name}\n\n{body}"
    return "---\n" + yaml.safe_dump(new_fm, sort_keys=False, allow_unicode=True) + "---\n\n" + body.rstrip() + "\n"


def _capture_hash_revision(root: Path) -> str | None:
    """Cheap current-HEAD token; a changed checkout triggers one index refresh."""
    git_dir = root / ".git"
    head = git_dir / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref:"):
            ref_name = value.split(":", 1)[1].strip()
            ref = git_dir / ref_name
            if ref.exists():
                return ref.read_text(encoding="utf-8").strip()
            packed = git_dir / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith(("#", "^")):
                        sha, name = line.split(" ", 1)
                        if name == ref_name:
                            return sha
        return value
    except OSError:
        return None


def _refresh_capture_hash_index(*, force: bool = False) -> None:
    """Index capture hashes across live inboxes and canonical archives.

    The scan is O(notes) only once per git revision (or every call for a
    non-git test/dev tree). Normal retries are O(1), which remains practical at
    ~10k source notes.
    """
    global _capture_hash_root, _capture_hash_revision_value, _capture_hash_index
    root = Path(WIKI_ROOT)
    revision = _capture_hash_revision(root)
    with _capture_hash_lock:
        if (
            not force
            and revision is not None
            and _capture_hash_root == root
            and _capture_hash_revision_value == revision
        ):
            return

        indexed: dict[str, list[ExistingNoteDrop]] = defaultdict(list)
        locations = (
            (root / "notes", False),
            (root / "wiki" / "sources" / "notes", True),
        )
        for location, archived in locations:
            if not location.exists():
                continue
            for source_file in location.rglob("*.md"):
                folder = source_file.parent
                if source_file.stem != folder.name:
                    continue
                try:
                    fm, _body = read_frontmatter_and_body(
                        source_file.read_text(encoding="utf-8")
                    )
                except Exception:
                    continue
                capture_hash = fm.get("capture-hash")
                if not isinstance(capture_hash, str) or not capture_hash:
                    continue
                try:
                    relative_dir = folder.relative_to(root).as_posix()
                except ValueError:
                    continue
                indexed[capture_hash].append(ExistingNoteDrop(
                    folder=folder.name,
                    relative_dir=relative_dir,
                    archived=archived,
                ))

        _capture_hash_root = root
        _capture_hash_revision_value = revision
        _capture_hash_index = dict(indexed)


def find_existing_note_drop(
    slug: str,
    capture_hash: str,
    *,
    force_refresh: bool = False,
) -> ExistingNoteDrop | None:
    """Find a retried payload in notes/ or wiki/sources/notes/."""
    _refresh_capture_hash_index(force=force_refresh)
    suffix = f"-{slug}"
    with _capture_hash_lock:
        matches = list(_capture_hash_index.get(capture_hash, ()))
    for existing in matches:
        if existing.folder.endswith(suffix):
            return existing
    return None


def note_drop_result(
    folder: str | ExistingNoteDrop,
    sha: str = "",
    already_exists: bool = False,
) -> dict:
    if isinstance(folder, ExistingNoteDrop):
        folder_name = folder.folder
        relative_dir = folder.relative_dir
        archived = folder.archived
    else:
        folder_name = folder
        relative_dir = f"notes/{folder_name}"
        archived = False
    if already_exists and not sha:
        sha = _capture_hash_revision(Path(WIKI_ROOT)) or ""
    result = {
        "ok": True,
        "folder": folder_name,
        "source_file": f"{folder_name}.md",
        "path": relative_dir,
        "archived": archived,
        "commit": sha,
        "pushed": True,
        "already_exists": already_exists,
    }
    # Best-effort web URL for the pushed folder (host-specific path shape).
    repo_web = re.sub(r"\.git$", "", WIKI_REPO_URL)
    if "github.com" in WIKI_REPO_URL or "gitlab" in WIKI_REPO_URL:
        result["url"] = f"{repo_web}/tree/{WIKI_BRANCH}/{relative_dir}"
    elif repo_web.startswith("http"):
        result["url"] = f"{repo_web}/src/branch/{WIKI_BRANCH}/{relative_dir}"  # gitea/forgejo
    return result


# --- In-memory index ---


EMBED_HASH_FORMAT = "embedding-input-v1"
LEGACY_HASH_FORMAT = "legacy-body-v0"


@dataclass
class Page:
    name: str
    path: Path
    type: str | None
    category: str | None
    status: str | None
    tags: list[str] = field(default_factory=list)
    description: str = ""
    body: str = ""
    body_hash: str = ""  # legacy cache compatibility only
    embedding_hash: str = ""
    cache_hash_format: str = EMBED_HASH_FORMAT
    wikilinks_out: set[str] = field(default_factory=set)
    embedding: np.ndarray | None = None


class Embedder:
    """Pluggable embedding provider. Either OpenAI (text-embedding-3-small) or
    fastembed (local ONNX). Both return L2-normalized float32 vectors; the
    Index does cosine via plain dot product on stacked rows.
    """

    def __init__(self, provider: str):
        self.provider = provider
        if provider == "openai":
            from openai import OpenAI
            self.client = OpenAI()  # picks up OPENAI_API_KEY from env
            self.model_name = OPENAI_MODEL
            self.dim = 1536  # text-embedding-3-small
            # text-embedding-3-small accepts 8191 tokens/input. Feed most of a
            # page rather than the old 2.5k-char (bge-era) truncation. `batch`
            # is computed after this block to keep tokens-per-request bounded.
            self.max_embed_chars = 16000
        elif provider == "fastembed":
            from fastembed import TextEmbedding
            cache_dir = os.environ.get("FASTEMBED_CACHE_DIR")
            kwargs = {"cache_dir": cache_dir} if cache_dir else {}
            self.model = TextEmbedding(FASTEMBED_MODEL, **kwargs)
            self.model_name = FASTEMBED_MODEL
            sample = list(self.model.embed(["dimension probe"]))[0]
            self.dim = len(sample)
            self.batch = 32  # ONNX activation memory bound
            self.max_embed_chars = 2500  # bge-small context window ≈ 512 tokens
        else:
            raise ValueError(f"unknown embedding provider: {provider!r}")

        # Per-deploy override of the body truncation window (chars).
        _cap = os.environ.get("WIKI_MCP_MAX_EMBED_CHARS")
        if _cap:
            self.max_embed_chars = int(_cap)
        if self.provider == "openai":
            # Keep batch × per-input tokens bounded (~3 chars/token, target
            # ≤250k tokens/request) so large pages never trip OpenAI's
            # per-request input ceiling.
            self.batch = max(8, 250_000 // max(1, self.max_embed_chars // 3))
        log.info(
            "embedder: %s/%s (dim=%d, max_chars=%d, batch=%d)",
            self.provider, self.model_name, self.dim, self.max_embed_chars, self.batch,
        )

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        if self.provider == "openai":
            out: list[np.ndarray] = []
            for i in range(0, len(texts), self.batch):
                chunk = texts[i:i + self.batch]
                resp = self.client.embeddings.create(model=self.model_name, input=chunk)
                # OpenAI returns L2-normalized vectors already
                out.extend(np.asarray(d.embedding, dtype=np.float32) for d in resp.data)
            return out
        # fastembed
        vectors = list(self.model.embed(texts, batch_size=self.batch))
        out2: list[np.ndarray] = []
        for v in vectors:
            arr = np.asarray(v, dtype=np.float32)
            arr /= np.linalg.norm(arr) + 1e-12
            out2.append(arr)
        return out2


class IndexNotReadyError(RuntimeError):
    pass


def _safe_error_summary(error: BaseException) -> str:
    """Expose useful provider state without response bodies, prompts, or headers."""
    status = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    parts = [type(error).__name__]
    if status is not None:
        parts.append(f"status={status}")
    if code:
        parts.append(f"code={code}")
    return parts[0] + (("(" + ", ".join(parts[1:]) + ")") if len(parts) > 1 else "")


class Index:
    def __init__(self, root: Path, embedder: Embedder, cache_dir: Path | None = None):
        self.root = root
        self.embedder = embedder
        # Fold the embed window into the model identity as a coarse guard. Each
        # page additionally hashes the exact title + description + body slice
        # sent to the provider, so any future input metadata change invalidates.
        self.model_name = f"{embedder.provider}/{embedder.model_name}@{embedder.max_embed_chars}"
        self.dim = embedder.dim
        self.cache_dir = cache_dir
        log.info("index object ready (model=%s, dim=%d, cache=%s)", self.model_name, self.dim, cache_dir or "disabled")

        # Cache entries are vectors only. They are not served until scan() has
        # supplied current paths, metadata, bodies, and wikilinks.
        self._cached_pages: dict[str, Page] = self._load_cache()
        self.pages: dict[str, Page] = {}
        self.matrix: np.ndarray = np.zeros((0, self.dim), dtype=np.float32)
        self.names: list[str] = []
        self.backlinks: dict[str, set[str]] = defaultdict(set)
        self._lock = threading.RLock()
        self._reindex_lock = threading.Lock()
        self._last_indexed = 0.0
        self._last_success = 0.0
        self._last_attempt = 0.0
        self._cache_loaded = len(self._cached_pages)
        self._status = "initializing"
        self._ready = False
        self._degraded = False
        self._last_error: str | None = None
        self._omitted_pages = 0
        self._scanned_pages = 0

    def _cache_path(self) -> Path | None:
        return (self.cache_dir / "index.npz") if self.cache_dir else None

    def _load_cache(self) -> dict[str, Page]:
        path = self._cache_path()
        if path is None or not path.exists():
            return {}
        try:
            data = np.load(path, allow_pickle=False)
            cached_model = str(data["model"])
            if cached_model != self.model_name:
                log.warning(
                    "ignoring cache: model %r != current %r (rebuild from scratch)",
                    cached_model, self.model_name,
                )
                return {}
            names = [str(n) for n in data["names"]]
            hashes = [str(h) for h in data["hashes"]]
            hash_format = (
                str(data["hash_format"])
                if "hash_format" in data.files
                else LEGACY_HASH_FORMAT
            )
            if hash_format not in {EMBED_HASH_FORMAT, LEGACY_HASH_FORMAT}:
                log.warning("ignoring cache with unknown hash format %r", hash_format)
                return {}
            vectors = data["vectors"].astype(np.float32)
            if vectors.shape != (len(names), self.dim):
                log.warning("cache vector shape mismatch %s != (%d,%d); ignoring",
                            vectors.shape, len(names), self.dim)
                return {}
            out: dict[str, Page] = {}
            for n, h, v in zip(names, hashes, vectors):
                out[n] = Page(
                    name=n,
                    path=Path(""),
                    type=None,
                    category=None,
                    status=None,
                    body_hash=h if hash_format == LEGACY_HASH_FORMAT else "",
                    embedding_hash=h if hash_format == EMBED_HASH_FORMAT else "",
                    cache_hash_format=hash_format,
                    embedding=v,
                )
            log.info("loaded %d cached embeddings from %s", len(out), path)
            return out
        except Exception as e:
            log.warning("failed to read cache at %s: %s; rebuilding", path, e)
            return {}

    def _save_cache(self, pages: dict[str, Page]) -> None:
        """Atomically persist a complete coherent index snapshot."""
        path = self._cache_path()
        if path is None:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            usable = {
                name: page for name, page in pages.items()
                if page.embedding is not None and page.embedding_hash
            }
            names = sorted(usable)
            if not names:
                return
            hashes = [usable[n].embedding_hash for n in names]
            vectors = np.stack([usable[n].embedding for n in names]).astype(np.float32)
            tmp = path.with_suffix(".tmp.npz")
            np.savez(
                tmp,
                model=self.model_name,
                dim=self.dim,
                names=np.array(names),
                hashes=np.array(hashes),
                hash_format=EMBED_HASH_FORMAT,
                vectors=vectors,
            )
            tmp.replace(path)
            log.info("saved %d embeddings to %s (%d KB)", len(names), path, path.stat().st_size // 1024)
        except Exception as e:
            log.warning("failed to write cache to %s: %s", path, e)

    # -- Page discovery + parse --

    def scan(self) -> dict[str, Page]:
        new_pages: dict[str, Page] = {}
        for fp in self.root.rglob("*.md"):
            if is_skipped(fp):
                continue
            try:
                content = fp.read_text(encoding="utf-8")
            except Exception as e:
                log.warning("failed to read %s: %s", fp, e)
                continue
            fm, body = read_frontmatter_and_body(content)
            page = Page(
                name=fp.stem,
                path=fp,
                type=fm.get("type"),
                category=fm.get("category"),
                status=fm.get("status"),
                tags=list(fm.get("tags") or []),
                description=fm.get("index-description") or "",
                body=body,
                body_hash=hash_body(body),
                wikilinks_out=find_wikilinks(body),
            )
            new_pages[page.name] = page
        return new_pages

    # -- Embedding --

    def _embed_text_for_page(self, p: Page) -> str:
        """What gets fed to the embedding model. Title + description + body, capped."""
        parts = [p.name]
        if p.description:
            parts.append(p.description)
        if p.body:
            parts.append(p.body)
        text = "\n\n".join(parts)
        return text[:self.embedder.max_embed_chars]

    @staticmethod
    def _embedding_hash(text: str) -> str:
        """Hash the exact provider input, after all composition/truncation."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _publish(
        self,
        pages: dict[str, Page],
        *,
        status: str,
        degraded: bool,
        error: BaseException | None,
        omitted_pages: int,
        scanned_pages: int,
        successful: bool = False,
    ) -> None:
        usable = {name: page for name, page in pages.items() if page.embedding is not None}
        names = sorted(usable)
        matrix = (
            np.stack([usable[name].embedding for name in names]).astype(np.float32)
            if names else np.zeros((0, self.dim), dtype=np.float32)
        )
        now = time.time()
        with self._lock:
            # Catalog and graph metadata remain available even for pages whose
            # fresh vector is temporarily omitted during provider degradation.
            self.pages = dict(pages)
            self.names = names
            self.matrix = matrix
            self.backlinks = self._compute_backlinks(pages)
            self._last_indexed = now
            self._last_attempt = now
            if successful:
                self._last_success = now
                self._cached_pages = dict(usable)
            self._ready = bool(names)
            self._degraded = degraded
            self._status = status if self._ready else "error"
            self._last_error = _safe_error_summary(error) if error else None
            self._omitted_pages = omitted_pages
            self._scanned_pages = scanned_pages

    def record_failure(
        self,
        error: BaseException,
        *,
        scanned_pages: int | None = None,
        omitted_pages: int | None = None,
    ) -> None:
        """Record bootstrap/git failures without discarding a usable index."""
        with self._lock:
            self._last_attempt = time.time()
            self._last_error = _safe_error_summary(error)
            if scanned_pages is not None:
                self._scanned_pages = scanned_pages
            if omitted_pages is not None:
                self._omitted_pages = omitted_pages
            if self._ready:
                self._degraded = True
                self._status = "degraded"
            else:
                self._status = "error"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "ok": self._ready,
                "ready": self._ready,
                "status": self._status,
                "degraded": self._degraded,
                "pages_indexed": len(self.names),
                "pages_cataloged": len(self.pages),
                "pages_scanned": self._scanned_pages,
                "pages_omitted": self._omitted_pages,
                "provider": self.embedder.provider,
                "model": self.model_name,
                "dim": self.dim,
                "last_indexed_unix": self._last_indexed,
                "last_success_unix": self._last_success,
                "last_attempt_unix": self._last_attempt,
                "last_error": self._last_error,
                "cache_dir": str(self.cache_dir) if self.cache_dir else None,
                "cache_loaded": self._cache_loaded,
            }

    def reindex(self) -> dict:
        """Walk the wiki and atomically publish full or cached-degraded state."""
        with self._reindex_lock:
            t0 = time.time()
            self._last_attempt = time.time()
            scanned = self.scan()
            if not scanned:
                error = RuntimeError("wiki scan found no pages")
                self.record_failure(error, scanned_pages=0, omitted_pages=0)
                snap = self.snapshot()
                return {
                    "ok": False,
                    "ready": snap["ready"],
                    "degraded": snap["degraded"],
                    "retry_needed": True,
                    "pages": snap["pages_indexed"],
                    "error": snap["last_error"],
                }
            with self._lock:
                old = dict(self._cached_pages)
                old.update(self.pages)

            to_embed: list[tuple[str, str]] = []
            unchanged = 0
            migrated_legacy = 0
            for name, page in scanned.items():
                embed_text = self._embed_text_for_page(page)
                page.embedding_hash = self._embedding_hash(embed_text)
                page.cache_hash_format = EMBED_HASH_FORMAT
                prev = old.get(name)
                exact_hit = (
                    prev is not None
                    and prev.cache_hash_format == EMBED_HASH_FORMAT
                    and prev.embedding_hash == page.embedding_hash
                    and prev.embedding is not None
                )
                legacy_hit = (
                    prev is not None
                    and prev.cache_hash_format == LEGACY_HASH_FORMAT
                    and prev.body_hash == page.body_hash
                    and prev.embedding is not None
                )
                if exact_hit or legacy_hit:
                    page.embedding = prev.embedding
                    unchanged += 1
                    if legacy_hit:
                        # One-time compatibility acceptance. Saving this
                        # successful snapshot rewrites the entry to exact-input.
                        migrated_legacy += 1
                else:
                    to_embed.append((name, embed_text))

            removed = [name for name in old if name not in scanned]
            cached_baseline = {
                name: page for name, page in scanned.items() if page.embedding is not None
            }

            # Publish current metadata + unchanged cached vectors immediately.
            # A slow/dead provider cannot prevent readiness when a valid baseline exists.
            if to_embed and cached_baseline:
                self._publish(
                    scanned,
                    status="degraded",
                    degraded=True,
                    error=None,
                    omitted_pages=len(to_embed),
                    scanned_pages=len(scanned),
                )

            if to_embed:
                try:
                    vectors = self.embedder.embed([text for _, text in to_embed])
                    if len(vectors) != len(to_embed):
                        raise RuntimeError(
                            f"embedding provider returned {len(vectors)} vectors for {len(to_embed)} pages"
                        )
                    for (name, _), vector in zip(to_embed, vectors):
                        scanned[name].embedding = vector
                except Exception as error:
                    if cached_baseline:
                        self._publish(
                            scanned,
                            status="degraded",
                            degraded=True,
                            error=error,
                            omitted_pages=len(to_embed),
                            scanned_pages=len(scanned),
                        )
                    else:
                        # Publish the full metadata catalog even with zero
                        # usable vectors. get/backlinks remain available while
                        # semantic search correctly reports unready.
                        self._publish(
                            scanned,
                            status="degraded",
                            degraded=True,
                            error=error,
                            omitted_pages=len(to_embed),
                            scanned_pages=len(scanned),
                        )
                    elapsed = time.time() - t0
                    snap = self.snapshot()
                    log.warning(
                        "reindex degraded in %.1fs: usable=%d scanned=%d omitted=%d error=%s",
                        elapsed, snap["pages_indexed"], len(scanned), len(to_embed), snap["last_error"],
                    )
                    return {
                        "ok": False,
                        "ready": snap["ready"],
                        "degraded": snap["degraded"],
                        "retry_needed": True,
                        "elapsed_sec": round(elapsed, 2),
                        "pages": snap["pages_indexed"],
                        "scanned": len(scanned),
                        "unchanged": unchanged,
                        "embedded": 0,
                        "omitted": len(to_embed),
                        "removed": len(removed),
                        "error": snap["last_error"],
                    }

            self._publish(
                scanned,
                status="ready",
                degraded=False,
                error=None,
                omitted_pages=0,
                scanned_pages=len(scanned),
                successful=True,
            )

            if to_embed or removed or migrated_legacy:
                self._save_cache(scanned)

            elapsed = time.time() - t0
            log.info(
                "reindex done in %.1fs: %d pages (%d unchanged, %d new/changed, "
                "%d legacy-migrated, %d removed)",
                elapsed, len(scanned), unchanged, len(to_embed), migrated_legacy, len(removed),
            )
            return {
                "ok": True,
                "ready": True,
                "degraded": False,
                "retry_needed": False,
                "elapsed_sec": round(elapsed, 2),
                "pages": len(scanned),
                "scanned": len(scanned),
                "unchanged": unchanged,
                "embedded": len(to_embed),
                "legacy_migrated": migrated_legacy,
                "omitted": 0,
                "removed": len(removed),
            }

    @staticmethod
    def _compute_backlinks(pages: dict[str, Page]) -> dict[str, set[str]]:
        bl: dict[str, set[str]] = defaultdict(set)
        for src_name, p in pages.items():
            for tgt in p.wikilinks_out:
                if tgt in pages:
                    bl[tgt].add(src_name)
        return bl

    # -- Queries --

    @staticmethod
    def _result(page: Page, score: float, search_mode: str) -> dict:
        return {
            "name": page.name,
            "score": float(score),
            "type": page.type,
            "category": page.category,
            "status": page.status,
            "tags": page.tags,
            "description": page.description,
            "path": str(page.path),
            "search_mode": search_mode,
        }

    def _lexical_search(self, query: str, k: int) -> list[dict]:
        """Provider-independent degraded search over the coherent baseline."""
        tokens = set(re.findall(r"[a-z0-9][a-z0-9_.-]+", query.lower()))
        if not tokens:
            return []
        phrase = query.lower().strip()
        with self._lock:
            pages = list(self.pages.values())
        ranked: list[tuple[float, Page]] = []
        for page in pages:
            name = page.name.lower()
            description = page.description.lower()
            tags = " ".join(page.tags).lower()
            body = page.body.lower()
            name_hits = sum(token in name for token in tokens)
            desc_hits = sum(token in description for token in tokens)
            body_hits = sum(token in body for token in tokens)
            score = (3.0 * name_hits + 2.0 * desc_hits + body_hits) / max(1, len(tokens))
            if phrase and phrase in name:
                score += 3.0
            elif phrase and phrase in description:
                score += 2.0
            if tags:
                score += sum(token in tags for token in tokens) / max(1, len(tokens))
            if score > 0:
                ranked.append((score, page))
        ranked.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [self._result(page, score, "lexical-degraded") for score, page in ranked[:k]]

    def search(self, query: str, k: int = 5) -> list[dict]:
        # Embed outside the lock — calling OpenAI under lock would serialize
        # all queries through one network round-trip.
        if not query.strip():
            return []
        with self._lock:
            if not self._ready or self.matrix.shape[0] == 0:
                raise IndexNotReadyError("wiki index is not ready")
        try:
            q = self.embedder.embed([query])[0]
        except Exception as error:
            self.record_failure(error)
            log.warning(
                "semantic query embedding unavailable; using lexical degraded search error=%s",
                _safe_error_summary(error),
            )
            return self._lexical_search(query, k)
        with self._lock:
            scores = self.matrix @ q  # cosine since both normalized
            top_idx = np.argsort(-scores)[:k]
            return [
                self._result(self.pages[self.names[i]], float(scores[i]), "semantic")
                for i in top_idx
            ]

    def get(self, name: str) -> dict | None:
        with self._lock:
            p = self.pages.get(name)
            if p is None:
                return None
            return {
                "name": name,
                "type": p.type,
                "category": p.category,
                "status": p.status,
                "tags": p.tags,
                "description": p.description,
                "path": str(p.path),
                "body": p.body,
                "wikilinks_out": sorted(p.wikilinks_out),
                "wikilinks_in": sorted(self.backlinks.get(name, ())),
            }

    def backlinks_of(self, name: str) -> list[str] | None:
        with self._lock:
            if name not in self.pages:
                return None
            return sorted(self.backlinks.get(name, ()))


# --- Supervised bootstrap + background reindex loop ---


def _refresh_repo_for_index(index: Index) -> None:
    if not WIKI_REPO_URL:
        return
    with _repo_lock:
        clone_path = _clone_or_pull_wiki()
    if clone_path is not None:
        index.root = clone_path


def _reindex_once(index: Index) -> dict:
    try:
        _refresh_repo_for_index(index)
        return index.reindex()
    except Exception as error:
        index.record_failure(error)
        log.warning("reindex attempt failed before publish error=%s", _safe_error_summary(error))
        snap = index.snapshot()
        return {
            "ok": False,
            "ready": snap["ready"],
            "degraded": snap["degraded"],
            "retry_needed": True,
            "pages": snap["pages_indexed"],
            "error": snap["last_error"],
        }


def _background_reindex(index: Index, stop_event: threading.Event) -> None:
    retry_sec = max(1, REINDEX_RETRY_INITIAL_SEC)
    delay = 0
    log.info(
        "supervised reindex loop starting (interval=%ds retry=%ds..%ds)",
        REINDEX_INTERVAL_SEC, REINDEX_RETRY_INITIAL_SEC, REINDEX_RETRY_MAX_SEC,
    )
    while not stop_event.wait(delay):
        stats = _reindex_once(index)
        if stats.get("ok"):
            retry_sec = max(1, REINDEX_RETRY_INITIAL_SEC)
            delay = max(1, REINDEX_INTERVAL_SEC)
        else:
            delay = retry_sec
            retry_sec = min(max(retry_sec * 2, 1), max(1, REINDEX_RETRY_MAX_SEC))
            log.warning(
                "reindex retry scheduled in %ds ready=%s degraded=%s error=%s",
                delay, stats.get("ready"), stats.get("degraded"), stats.get("error"),
            )


# --- MCP tools (registered on the shared core.mcp instance) ---


embedder = Embedder(EMBED_PROVIDER)
idx = Index(WIKI_ROOT, embedder, cache_dir=CACHE_DIR)
log.info("index bootstrap deferred to supervised background thread")


def _wiki_search_impl(query: str, k: int) -> list[dict]:
    """Blocking implementation of wiki_search. Runs in a worker thread because
    embedding the query is a network round-trip that must not stall the event
    loop (and with it the liveness probe)."""
    kk = max(1, min(int(k), 25))
    results = idx.search(query, kk)
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "search",
        "query": (query or "")[:200], "k": kk, "n": len(results),
        "top": [{"name": r["name"], "score": round(r["score"], 4)} for r in results[:3]],
    })
    return results


@mcp.tool()
async def wiki_search(query: str, k: int = 5) -> list[dict]:
    """Search the operator's wiki for pages relevant to `query`. Returns up to k pages
    with name, score (higher = more relevant), search_mode, type, status, tags,
    and a one-line description. Normal mode is semantic cosine search; provider
    outages use explicit lexical-degraded search over the cached baseline. Use this
    before answering questions about the operator's deploys, services, decisions,
    conventions, or past learnings."""
    return await anyio.to_thread.run_sync(functools.partial(_wiki_search_impl, query, k))


@mcp.tool()
def wiki_get(name: str) -> dict | None:
    """Fetch the full body of a wiki page by name (without the .md extension).
    Returns frontmatter fields + the markdown body + outbound and inbound wikilinks.
    Returns None if no page with that name exists."""
    page = idx.get(name)
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "get",
        "name": name, "hit": page is not None,
    })
    return page


@mcp.tool()
def wiki_backlinks(name: str) -> list[str] | None:
    """List the names of wiki pages that link to `name`. Useful for finding all
    pages that reference an entity. Returns None if the page doesn't exist, [] if
    it has no inbound links."""
    bl = idx.backlinks_of(name)
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "backlinks",
        "name": name, "hit": bl is not None, "n": len(bl) if bl else 0,
    })
    return bl


def _wiki_note_drop_impl(
    slug: str,
    note_md: str,
    attachments: dict[str, str] | None = None,
    binary_attachments: dict[str, str] | None = None,
) -> dict:
    """Blocking implementation of wiki_note_drop. Runs in a worker thread —
    never on the event loop — because git subprocess calls can stall long
    enough to starve the liveness probe and get the pod killed."""
    if not WIKI_REPO_URL:
        return {"ok": False, "error": "wiki_note_drop disabled: WIKI_REPO_URL not set on the server"}
    if not WIKI_REPO_TOKEN:
        return {"ok": False, "error": "wiki_note_drop disabled: WIKI_REPO_TOKEN not set on the server"}
    if not (Path(WIKI_ROOT) / ".git").exists():
        return {"ok": False, "error": "wiki_note_drop temporarily unavailable: repository is still initializing"}

    # --- Validate inputs ---
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        return {"ok": False, "error": f"slug must match {SLUG_RE.pattern!r} (lowercase, hyphens); got {slug!r}"}
    if not isinstance(note_md, str) or not note_md.strip():
        return {"ok": False, "error": "note_md must be a non-empty string"}
    input_body_bytes = note_md.encode("utf-8")
    if len(input_body_bytes) > NOTE_BODY_MAX:
        return {"ok": False, "error": f"note_md is {len(input_body_bytes)} bytes; max {NOTE_BODY_MAX}"}

    text_atts = attachments or {}
    bin_atts = binary_attachments or {}
    if not isinstance(text_atts, dict) or not isinstance(bin_atts, dict):
        return {"ok": False, "error": "attachments / binary_attachments must be dicts of {filename: content}"}

    all_names = set(text_atts.keys()) | set(bin_atts.keys())
    if len(all_names) != len(text_atts) + len(bin_atts):
        return {"ok": False, "error": "attachment filename collision between text and binary maps"}
    if len(all_names) > ATTACHMENT_COUNT_MAX:
        return {"ok": False, "error": f"{len(all_names)} attachments; max {ATTACHMENT_COUNT_MAX}"}
    if "NOTE.md" in all_names:
        return {"ok": False, "error": "filename 'NOTE.md' is reserved; pass that content via the note_md arg"}
    for fn in all_names:
        if not FILENAME_RE.match(fn):
            return {"ok": False, "error": f"attachment filename {fn!r} must match {FILENAME_RE.pattern!r}"}

    decoded_text: dict[str, bytes] = {}
    for fn, content in text_atts.items():
        if not isinstance(content, str):
            return {"ok": False, "error": f"text attachment {fn!r} must be a string"}
        b = content.encode("utf-8")
        if len(b) > ATTACHMENT_TEXT_MAX:
            return {"ok": False, "error": f"text attachment {fn!r} is {len(b)} bytes; max {ATTACHMENT_TEXT_MAX}"}
        decoded_text[fn] = b

    decoded_bin: dict[str, bytes] = {}
    for fn, content in bin_atts.items():
        if not isinstance(content, str):
            return {"ok": False, "error": f"binary attachment {fn!r} must be a base64 string"}
        try:
            raw = base64.b64decode(content, validate=True)
        except Exception as e:
            return {"ok": False, "error": f"binary attachment {fn!r}: base64 decode failed: {e}"}
        if len(raw) > ATTACHMENT_BINARY_MAX:
            return {"ok": False, "error": f"binary attachment {fn!r} is {len(raw)} bytes (decoded); max {ATTACHMENT_BINARY_MAX}"}
        decoded_bin[fn] = raw

    capture_hash = note_drop_payload_hash(note_md, decoded_text, decoded_bin)
    started = time.monotonic()

    # --- Single-flight: serialize git ops on the working tree ---
    with _repo_lock:
        # A previous call (or a container kill mid-commit) may have left a
        # stale .git/*.lock behind; nothing else runs git while we hold
        # _repo_lock, so any lock file present now is guaranteed stale.
        _clear_stale_git_locks()

        existing = find_existing_note_drop(slug, capture_hash)
        if existing:
            log.info(
                "wiki_note_drop idempotent hit before sync slug=%s folder=%s duration=%.2fs",
                slug, existing.folder, time.monotonic() - started,
            )
            return note_drop_result(existing, already_exists=True)

        # Sync to remote first — discards any stale local state.
        try:
            _run_git("fetch", "origin", cwd=WIKI_ROOT)
            _run_git("reset", "--hard", f"origin/{WIKI_BRANCH}", cwd=WIKI_ROOT)
            _run_git("clean", "-fd", cwd=WIKI_ROOT)
        except Exception as e:
            return {"ok": False, "error": f"failed to sync repo to origin/{WIKI_BRANCH}: {e}"}

        existing = find_existing_note_drop(slug, capture_hash, force_refresh=True)
        if existing:
            log.info(
                "wiki_note_drop idempotent hit after sync slug=%s folder=%s duration=%.2fs",
                slug, existing.folder, time.monotonic() - started,
            )
            return note_drop_result(existing, already_exists=True)

        ts = time.strftime("%Y-%m-%d-%H%M%S", time.gmtime())
        folder_name = f"{ts}-{slug}"
        folder = Path(WIKI_ROOT) / "notes" / folder_name
        if folder.exists():
            return {"ok": False, "error": f"folder {folder_name!r} already exists; retry with a different slug"}

        source_filename = f"{folder_name}.md"
        source_body = source_note_markdown(folder_name, note_md, capture_hash)
        body_bytes = source_body.encode("utf-8")
        if len(body_bytes) > NOTE_BODY_MAX + 8192:
            return {"ok": False, "error": f"source note is {len(body_bytes)} bytes after frontmatter normalization"}

        # Write
        try:
            folder.mkdir(parents=True, exist_ok=False)
            (folder / source_filename).write_bytes(body_bytes)
            for fn, b in decoded_text.items():
                (folder / fn).write_bytes(b)
            for fn, b in decoded_bin.items():
                (folder / fn).write_bytes(b)
        except Exception as e:
            shutil.rmtree(folder, ignore_errors=True)
            return {"ok": False, "error": f"failed to write note files: {e}"}

        # Stage + commit
        rel_path = f"notes/{folder_name}/"
        try:
            _run_git("add", "--", rel_path, cwd=WIKI_ROOT)
            _run_git("commit", "-m", f"note: {folder_name}", cwd=WIKI_ROOT)
        except Exception as e:
            shutil.rmtree(folder, ignore_errors=True)
            return {"ok": False, "error": f"commit failed: {e}"}

        try:
            sha = _run_git("rev-parse", "HEAD", cwd=WIKI_ROOT).stdout.strip()
        except Exception:
            sha = ""

        # Push — roll back the commit on failure so we don't accumulate
        # unpushable local state across calls.
        try:
            _run_git("push", "origin", WIKI_BRANCH, cwd=WIKI_ROOT, timeout=60)
        except Exception as e:
            try:
                _run_git("reset", "--hard", f"origin/{WIKI_BRANCH}", cwd=WIKI_ROOT)
            except Exception:
                pass
            return {"ok": False, "error": f"push failed: {e}"}

        log.info(
            "wiki_note_drop pushed slug=%s folder=%s sha=%s duration=%.2fs",
            slug, folder_name, sha[:12], time.monotonic() - started,
        )
        return note_drop_result(folder_name, sha)


def _wiki_note_drop_recorded(
    slug: str,
    note_md: str,
    attachments: dict[str, str] | None,
    binary_attachments: dict[str, str] | None,
) -> dict:
    started = time.monotonic()
    result = _wiki_note_drop_impl(slug, note_md, attachments, binary_attachments)
    outcome = (
        "already_exists" if result.get("ok") and result.get("already_exists")
        else "ok" if result.get("ok")
        else "error"
    )
    _record_query({
        "ts": time.time(),
        "at": _now_iso(),
        "source": "mcp",
        "tool": "wiki_note_drop",
        "outcome": outcome,
        "duration_ms": int((time.monotonic() - started) * 1000),
    })
    return result


@mcp.tool()
async def wiki_note_drop(
    slug: str,
    note_md: str,
    attachments: dict[str, str] | None = None,
    binary_attachments: dict[str, str] | None = None,
) -> dict:
    """Drop a note into the wiki's inbox so the next curation pass can promote it
    into wiki pages. Use this whenever you learn something non-obvious that a
    future session would want to look up — the server creates the folder,
    commits, and pushes to the wiki repo on your behalf.

    The folder is created at `notes/<YYYY-MM-DD-HHMMSS-slug>/` (UTC timestamp from
    the server) and contains `<YYYY-MM-DD-HHMMSS-slug>.md` plus any attachments
    you pass. The curator later moves that folder to
    `wiki/sources/notes/YYYY/MM/DD/<YYYY-MM-DD-HHMMSS-slug>/` so the source note
    is directly wikilinkable.

    Args:
        slug: short kebab-case slug, e.g. "claude-mac-cleanup". Must match
            `^[a-z0-9][a-z0-9-]{0,63}$`.
        note_md: full source-note markdown body. Should include YAML frontmatter
            with `captured: <ISO 8601 datetime>`, `session: <one-line context>`,
            and `topic: <1-5 word topic>`. `wiki_note_drop` wraps that capture
            metadata in wiki-compatible source-page frontmatter when needed.
        attachments: optional text attachments as {filename: content}. Use for
            `.log`, `.txt`, `.yaml`, `.json`, `.conf`, etc. Each ≤ 256 KB.
        binary_attachments: optional binary attachments as {filename: base64-content}.
            Use for screenshots, PDFs, etc. Each ≤ 2 MB decoded.

    Returns a dict with `ok: true|false`. On success: `folder`, `commit`, `url`.
    On failure: `error` with a brief reason.

    NEVER include credentials, tokens, or passwords in note bodies — notes are
    git-tracked. Reference your secret manager's path instead (e.g. "token in
    the vault at /services/foo/API_TOKEN").
    """
    return await anyio.to_thread.run_sync(
        functools.partial(
            _wiki_note_drop_recorded,
            slug,
            note_md,
            attachments,
            binary_attachments,
        )
    )


# --- Plain HTTP routes (for non-MCP clients) ---


def live(req: Request) -> Response:
    """Process liveness only: never depends on git, cache, or embeddings."""
    return JSONResponse({"ok": True, "live": True, "status": idx.snapshot()["status"]})


def health(req: Request) -> Response:
    """Readiness: 200 for usable full/degraded indexes, 503 while unready."""
    snapshot = idx.snapshot()
    return JSONResponse(snapshot, status_code=200 if snapshot["ready"] else 503)


def _http_search_impl(q: str, k: int) -> list[dict]:
    """Blocking HTTP search implementation (embedding + metrics file write)."""
    results = idx.search(q, k)
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "http", "tool": "search",
        "query": q[:200], "k": k, "n": len(results),
        "top": [{"name": r["name"], "score": round(r["score"], 4)} for r in results[:3]],
    })
    return results


async def http_search(req: Request) -> Response:
    q = req.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"error": "missing q"}, status_code=400)
    k = max(1, min(int(req.query_params.get("k", "5")), 25))
    try:
        results = await anyio.to_thread.run_sync(
            functools.partial(_http_search_impl, q, k)
        )
    except IndexNotReadyError:
        return JSONResponse({"error": "wiki index is not ready", **idx.snapshot()}, status_code=503)
    return JSONResponse({"query": q, "results": results})


def http_get_page(req: Request) -> Response:
    name = req.path_params["name"]
    p = idx.get(name)
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "http", "tool": "get",
        "name": name, "hit": p is not None,
    })
    if p is None:
        return JSONResponse({"error": f"no page named {name!r}"}, status_code=404)
    return JSONResponse(p)


def http_backlinks(req: Request) -> Response:
    name = req.path_params["name"]
    bl = idx.backlinks_of(name)
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "http", "tool": "backlinks",
        "name": name, "hit": bl is not None, "n": len(bl) if bl else 0,
    })
    if bl is None:
        return JSONResponse({"error": f"no page named {name!r}"}, status_code=404)
    return JSONResponse({"name": name, "backlinks": bl})


async def http_reindex(req: Request) -> Response:
    stats = await anyio.to_thread.run_sync(functools.partial(_reindex_once, idx))
    return JSONResponse(stats, status_code=200 if stats.get("ready") else 503)


# --- HTTP routes + background loop (assembled by server.py) ---


# Plain HTTP under /api/ to leave MCP its own /mcp path.
http_routes = [
    Route("/live", live),
    Route("/health", health),
    Route("/api/search", http_search),
    Route("/api/page/{name:path}", http_get_page),
    Route("/api/backlinks/{name:path}", http_backlinks),
    Route("/api/reindex", http_reindex, methods=["POST"]),
]

_reindex_stop = threading.Event()
_reindex_thread: threading.Thread | None = None


def start_background_reindex() -> None:
    """Start the supervised reindex thread (called from server.py's lifespan)."""
    global _reindex_thread
    if BACKGROUND_REINDEX_ENABLED and (_reindex_thread is None or not _reindex_thread.is_alive()):
        _reindex_stop.clear()
        _reindex_thread = threading.Thread(
            target=_background_reindex,
            args=(idx, _reindex_stop),
            daemon=True,
            name="wiki-reindex",
        )
        _reindex_thread.start()


def stop_background_reindex() -> None:
    _reindex_stop.set()

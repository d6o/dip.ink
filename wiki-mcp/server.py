"""wiki-mcp — semantic search MCP server over a markdown wiki.

Indexes wiki/*.md (excluding frozen note folders + READMEs) using OpenAI
embeddings (default) or a local fastembed model. Exposes:

  - MCP tools (HTTP transport at /mcp): wiki_search, wiki_get, wiki_backlinks,
    wiki_note_drop
  - Plain HTTP at /api/search, /api/page/<name>, /api/backlinks/<name>, /live, /health

Designed to run as a single container/pod, registered as an MCP server in any
agent session that wants wiki context. The wiki repo is mounted (or
git-cloned) at /wiki inside the container.

Index strategy: bind HTTP first, then bootstrap in a supervised background
thread. Cached unchanged vectors are published with freshly scanned metadata
before changed pages are embedded; provider failures leave a ready degraded
baseline when possible, or a live/unready process when no valid cache exists.
Retries use bounded backoff. Normal refreshes hash each page and only re-embed
changed content. POST /reindex triggers an immediate refresh.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route

# --- Config ---

WIKI_ROOT = Path(os.environ.get("WIKI_ROOT", "wiki"))
# When WIKI_REPO_URL is set, the pod clones (or refreshes) the wiki repo into
# WIKI_CLONE_PATH at startup, overrides WIKI_ROOT to that path, and uses the
# same clone for git push from the `wiki_note_drop` tool. When unset (local
# dev), the static WIKI_ROOT is used as-is and the write tool is disabled.
WIKI_REPO_URL = os.environ.get("WIKI_REPO_URL", "")
WIKI_BRANCH = os.environ.get("WIKI_BRANCH", "main")
WIKI_CLONE_PATH = Path(os.environ.get("WIKI_CLONE_PATH", "/var/lib/wiki-mcp/wiki"))
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
# Persistence: a Longhorn PVC mounted at this path lets the pod survive restarts
# without paying the OpenAI re-embed cost ($0.004/cold-start) and 2.3s cold-boot
# latency. Cache stores {name: (body_hash, embedding)} keyed by the active model;
# if the model changes (provider swap), the cache is ignored and rebuilt.
CACHE_DIR = Path(os.environ.get("WIKI_MCP_CACHE_DIR", "")) if os.environ.get("WIKI_MCP_CACHE_DIR") else None
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")

# Query instrumentation. We have never been able to answer "do agents actually
# query the wiki?" — the repo's manual `query` log entries are zero and tool
# calls were unlogged. Record every search/get/backlinks call to stdout, and
# when a metrics path is available append a JSONL event for durable analysis.
# Defaults to <CACHE_DIR>/queries.jsonl when the PVC cache is configured (so the
# data survives pod restarts); set WIKI_MCP_METRICS_PATH to a path to override,
# or to off/none/0 to disable.
_metrics_env = os.environ.get("WIKI_MCP_METRICS_PATH", "").strip()
if _metrics_env.lower() in {"", "auto"}:
    METRICS_PATH = (CACHE_DIR / "queries.jsonl") if CACHE_DIR else None
elif _metrics_env.lower() in {"off", "none", "0", "disabled"}:
    METRICS_PATH = None
else:
    METRICS_PATH = Path(_metrics_env)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wiki-mcp")


# --- Note-drop validation limits ---

# Used by the wiki_note_drop MCP tool. Matches the notes/ inbox protocol
# documented in the wiki repo's CLAUDE.md.
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


def _git_pull() -> None:
    """Fetch + hard-reset to origin. No-op when WIKI_REPO_URL is unset.
    Tolerant of transient network failures — logs and continues.
    Caller is responsible for holding _repo_lock if serialization matters."""
    if not WIKI_REPO_URL:
        return
    try:
        _run_git("fetch", "origin", cwd=WIKI_ROOT)
        _run_git("reset", "--hard", f"origin/{WIKI_BRANCH}", cwd=WIKI_ROOT)
    except Exception as e:
        log.warning("git_pull skipped: %s", e)


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


def find_existing_note_drop(slug: str, capture_hash: str) -> str | None:
    """Return an existing inbox folder for a retried note-drop request."""
    notes_root = Path(WIKI_ROOT) / "notes"
    if not notes_root.exists():
        return None
    for folder in sorted(notes_root.glob(f"*-{slug}")):
        if not folder.is_dir():
            continue
        source_file = folder / f"{folder.name}.md"
        if not source_file.exists():
            continue
        try:
            fm, _body = read_frontmatter_and_body(source_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if fm.get("capture-hash") == capture_hash:
            return folder.name
    return None


def note_drop_result(folder_name: str, sha: str = "", already_exists: bool = False) -> dict:
    result = {
        "ok": True,
        "folder": folder_name,
        "source_file": f"{folder_name}.md",
        "commit": sha,
        "pushed": True,
        "already_exists": already_exists,
    }
    # Best-effort web URL for the pushed folder (host-specific path shape).
    repo_web = re.sub(r"\.git$", "", WIKI_REPO_URL)
    if "github.com" in WIKI_REPO_URL or "gitlab" in WIKI_REPO_URL:
        result["url"] = f"{repo_web}/tree/{WIKI_BRANCH}/notes/{folder_name}"
    elif repo_web.startswith("http"):
        result["url"] = f"{repo_web}/src/branch/{WIKI_BRANCH}/notes/{folder_name}"  # gitea/forgejo
    return result


# --- In-memory index ---


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
    body_hash: str = ""
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
        # Fold the embed window into the cache identity. The on-disk cache reuses
        # an embedding whenever a page's body_hash is unchanged — but widening
        # max_embed_chars changes the embedding *input* without changing the body,
        # so the window must be part of the key or a window change silently reuses
        # the old (truncated) vectors. Changing it forces a one-time full re-embed.
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
            vectors = data["vectors"].astype(np.float32)
            if vectors.shape != (len(names), self.dim):
                log.warning("cache vector shape mismatch %s != (%d,%d); ignoring",
                            vectors.shape, len(names), self.dim)
                return {}
            out: dict[str, Page] = {}
            for n, h, v in zip(names, hashes, vectors):
                out[n] = Page(
                    name=n, path=Path(""), type=None, category=None, status=None,
                    body_hash=h, embedding=v,
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
            names = sorted(pages.keys())
            if not names:
                return
            hashes = [pages[n].body_hash for n in names]
            vectors = np.stack([pages[n].embedding for n in names]).astype(np.float32)
            tmp = path.with_suffix(".tmp.npz")
            np.savez(
                tmp,
                model=self.model_name,
                dim=self.dim,
                names=np.array(names),
                hashes=np.array(hashes),
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
            self.pages = usable
            self.names = names
            self.matrix = matrix
            self.backlinks = self._compute_backlinks(usable)
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
                "pages_indexed": len(self.pages),
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
            for name, page in scanned.items():
                prev = old.get(name)
                if prev is not None and prev.body_hash == page.body_hash and prev.embedding is not None:
                    page.embedding = prev.embedding
                    unchanged += 1
                else:
                    to_embed.append((name, self._embed_text_for_page(page)))

            removed = [name for name in old if name not in scanned]
            cached_baseline = {
                name: page for name, page in scanned.items() if page.embedding is not None
            }

            # Publish current metadata + unchanged cached vectors immediately.
            # A slow/dead provider cannot prevent readiness when a valid baseline exists.
            if to_embed and cached_baseline:
                self._publish(
                    cached_baseline,
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
                            cached_baseline,
                            status="degraded",
                            degraded=True,
                            error=error,
                            omitted_pages=len(to_embed),
                            scanned_pages=len(scanned),
                        )
                    else:
                        self.record_failure(
                            error,
                            scanned_pages=len(scanned),
                            omitted_pages=len(to_embed),
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

            if to_embed or removed:
                self._save_cache(scanned)

            elapsed = time.time() - t0
            log.info(
                "reindex done in %.1fs: %d pages (%d unchanged, %d new/changed, %d removed)",
                elapsed, len(scanned), unchanged, len(to_embed), len(removed),
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


# --- Query instrumentation ---


_metrics_lock = threading.Lock()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _record_query(event: dict) -> None:
    """Record a tool-call event: always to stdout (visible in `kubectl logs`),
    and to METRICS_PATH as JSONL when configured. Best-effort — instrumentation
    must never break a query."""
    try:
        payload = json.dumps(event, ensure_ascii=False, default=str)
    except Exception:
        return
    log.info("query %s", payload)
    if METRICS_PATH is None:
        return
    try:
        with _metrics_lock:
            METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with METRICS_PATH.open("a", encoding="utf-8") as fh:
                fh.write(payload + "\n")
    except Exception as e:
        log.warning("failed to record query metric to %s: %s", METRICS_PATH, e)


# --- MCP setup ---


embedder = Embedder(EMBED_PROVIDER)
idx = Index(WIKI_ROOT, embedder, cache_dir=CACHE_DIR)
log.info("index bootstrap deferred to supervised background thread")

# DNS-rebinding protection: FastMCP auto-locks to 127.0.0.1/localhost when host
# is unset. If you expose this server behind an ingress / reverse proxy, add
# its hostname via WIKI_MCP_ALLOWED_HOSTS (comma-separated).
_default_hosts = "localhost,127.0.0.1,wiki-mcp"
_allowed = [h.strip() for h in os.environ.get("WIKI_MCP_ALLOWED_HOSTS", _default_hosts).split(",") if h.strip()]
_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_allowed + [f"{h}:*" for h in _allowed],
    allowed_origins=[f"https://{h}" for h in _allowed] + [f"http://{h}" for h in _allowed],
)
mcp = FastMCP("wiki-mcp", stateless_http=True, host="0.0.0.0", port=PORT, transport_security=_security)


@mcp.tool()
def wiki_search(query: str, k: int = 5) -> list[dict]:
    """Search the operator's wiki for pages relevant to `query`. Returns up to k pages
    with name, score (higher = more relevant), search_mode, type, status, tags,
    and a one-line description. Normal mode is semantic cosine search; provider
    outages use explicit lexical-degraded search over the cached baseline. Use this
    before answering questions about the operator's deploys, services, decisions,
    conventions, or past learnings."""
    kk = max(1, min(int(k), 25))
    results = idx.search(query, kk)
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "mcp", "tool": "search",
        "query": (query or "")[:200], "k": kk, "n": len(results),
        "top": [{"name": r["name"], "score": round(r["score"], 4)} for r in results[:3]],
    })
    return results


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


@mcp.tool()
def wiki_note_drop(
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
        existing = find_existing_note_drop(slug, capture_hash)
        if existing:
            log.info(
                "wiki_note_drop idempotent hit before sync slug=%s folder=%s duration=%.2fs",
                slug, existing, time.monotonic() - started,
            )
            return note_drop_result(existing, already_exists=True)

        # Sync to remote first — discards any stale local state.
        try:
            _run_git("fetch", "origin", cwd=WIKI_ROOT)
            _run_git("reset", "--hard", f"origin/{WIKI_BRANCH}", cwd=WIKI_ROOT)
            _run_git("clean", "-fd", cwd=WIKI_ROOT)
        except Exception as e:
            return {"ok": False, "error": f"failed to sync repo to origin/{WIKI_BRANCH}: {e}"}

        existing = find_existing_note_drop(slug, capture_hash)
        if existing:
            log.info(
                "wiki_note_drop idempotent hit after sync slug=%s folder=%s duration=%.2fs",
                slug, existing, time.monotonic() - started,
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


# --- Plain HTTP routes (for non-MCP clients) ---


def live(req: Request) -> Response:
    """Process liveness only: never depends on git, cache, or embeddings."""
    return JSONResponse({"ok": True, "live": True, "status": idx.snapshot()["status"]})


def health(req: Request) -> Response:
    """Readiness: 200 for usable full/degraded indexes, 503 while unready."""
    snapshot = idx.snapshot()
    return JSONResponse(snapshot, status_code=200 if snapshot["ready"] else 503)


def http_search(req: Request) -> Response:
    q = req.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"error": "missing q"}, status_code=400)
    k = max(1, min(int(req.query_params.get("k", "5")), 25))
    try:
        results = idx.search(q, k)
    except IndexNotReadyError:
        return JSONResponse({"error": "wiki index is not ready", **idx.snapshot()}, status_code=503)
    _record_query({
        "ts": time.time(), "at": _now_iso(), "source": "http", "tool": "search",
        "query": q[:200], "k": k, "n": len(results),
        "top": [{"name": r["name"], "score": round(r["score"], 4)} for r in results[:3]],
    })
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


def http_reindex(req: Request) -> Response:
    stats = _reindex_once(idx)
    return JSONResponse(stats, status_code=200 if stats.get("ready") else 503)


def http_metrics(req: Request) -> Response:
    """Tail of the query-instrumentation log (for the weekly memory-gaps miner).
    ?days=7 filters by event ts. Read-only; returns at most the last 5000 events."""
    days = float(req.query_params.get("days", "7"))
    cutoff = time.time() - days * 86400
    events: list[dict] = []
    if METRICS_PATH is not None and METRICS_PATH.exists():
        try:
            with METRICS_PATH.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                        if e.get("ts", 0) >= cutoff:
                            events.append(e)
                    except Exception:
                        continue
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"events": events[-5000:], "days": days})


# --- Compose Starlette app: plain routes + mounted MCP ---


# Plain HTTP under /api/ to leave MCP its own /mcp path.
http_routes = [
    Route("/live", live),
    Route("/health", health),
    Route("/api/search", http_search),
    Route("/api/metrics", http_metrics),
    Route("/api/page/{name:path}", http_get_page),
    Route("/api/backlinks/{name:path}", http_backlinks),
    Route("/api/reindex", http_reindex, methods=["POST"]),
]

# FastMCP exposes a Starlette app via `streamable_http_app()` with /mcp routed internally.
mcp_app = mcp.streamable_http_app()
_reindex_stop = threading.Event()
_reindex_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(_app):
    """Start FastMCP immediately, then supervise indexing in the background."""
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
    async with mcp_app.router.lifespan_context(mcp_app):
        try:
            yield
        finally:
            _reindex_stop.set()


# Mount mcp_app at root so its internal /mcp route lands at /mcp.
app = Starlette(
    routes=http_routes + [Mount("/", app=mcp_app)],
    lifespan=lifespan,
)


def main():
    import uvicorn

    log.info("starting wiki-mcp on %s:%d (index bootstrap is asynchronous)", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()

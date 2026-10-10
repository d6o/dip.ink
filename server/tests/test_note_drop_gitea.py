"""wiki_note_drop writes one note with one Gitea ChangeFiles request.

The fake server records every request. Tests check the payload, the retry
map, collision mapping, and the read that follows a timeout.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ["WIKI_MCP_EMBED_PROVIDER"] = "openai"
os.environ["WIKI_MCP_BACKGROUND_REINDEX"] = "0"
os.environ["WIKI_ROOT"] = "/tmp/wiki-mcp-test-global-root"
os.environ["WIKI_WRITE_MODE"] = "git"

import wiki  # noqa: E402


NOTE = "---\ncaptured: 2026-10-10T12:00:00Z\nsession: gitea drop test\ntopic: gitea drop\n---\nbody\n"
SECRET_TOKEN = "super-secret-token-value"


class FakeGitea:
    """A small Gitea stand-in. One handler thread serves each request."""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.gets: list[str] = []
        self.version = {"version": "1.25.4"}
        self.post_status = 201
        self.post_error = "file already exists [path]"
        self.hold_post = threading.Event()
        self.release_post = threading.Event()
        self.release_post.set()
        self.files: dict[str, bytes] = {}
        self.commit_sha = "a" * 40
        self._lock = threading.Lock()
        handler = self._handler_class()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.release_post.set()
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()

    def _handler_class(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:
                return

            def _read_json(self) -> dict:
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length) if length else b""
                return json.loads(raw.decode("utf-8") or "{}")

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                with owner._lock:
                    owner.gets.append(parsed.path)
                if parsed.path == "/api/v1/version":
                    body = json.dumps(owner.version).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                prefix = "/api/v1/repos/ai-agent/mykg/contents/"
                if parsed.path.startswith(prefix):
                    rel = parsed.path[len(prefix):]
                    stored = owner.files.get(rel)
                    if stored is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    payload = {
                        "type": "file",
                        "path": rel,
                        "encoding": "base64",
                        "content": base64.b64encode(stored).decode("ascii"),
                        "sha": "b" * 40,
                    }
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                body = self._read_json()
                owner.hold_post.set()
                owner.release_post.wait(timeout=5)
                with owner._lock:
                    owner.posts.append({
                        "path": parsed.path,
                        "query": parse_qs(parsed.query),
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    })
                if owner.post_status in (200, 201):
                    for item in body.get("files") or []:
                        content = item.get("content") or ""
                        owner.files[item["path"]] = base64.b64decode(content)
                    payload = {"commit": {"sha": owner.commit_sha}, "files": []}
                    raw = json.dumps(payload).encode("utf-8")
                    self.send_response(owner.post_status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                raw = json.dumps({"message": owner.post_error}).encode("utf-8")
                self.send_response(owner.post_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        return Handler


class GiteaNoteDropTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitea()
        self.root = Path("/tmp/wiki-mcp-gitea-drop-empty")
        self.root.mkdir(exist_ok=True)
        (self.root / ".git").mkdir(exist_ok=True)
        wiki._api_drop_cache.clear()
        wiki._gitea_detected = None
        wiki._reindex_wake.clear()
        self.patches = [
            mock.patch.object(wiki, "WIKI_REPO_URL", f"{self.fake.origin}/ai-agent/mykg.git"),
            mock.patch.object(wiki, "WIKI_REPO_TOKEN", SECRET_TOKEN),
            mock.patch.object(wiki, "WIKI_REPO_USER", "token"),
            mock.patch.object(wiki, "WIKI_BRANCH", "main"),
            mock.patch.object(wiki, "WIKI_ROOT", self.root),
            mock.patch.object(wiki, "WIKI_WRITE_MODE", "gitea-api"),
            mock.patch.object(wiki, "GIT_USER_NAME", "wiki-mcp"),
            mock.patch.object(wiki, "GIT_USER_EMAIL", "wiki-mcp@localhost"),
            mock.patch.object(wiki, "find_existing_note_drop", return_value=None),
            mock.patch.object(wiki, "_run_git") ,
        ]
        for patch in self.patches:
            patch.start()
        self.run_git = self.patches[-1]

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.fake.close()
        wiki._api_drop_cache.clear()
        wiki._gitea_detected = None

    def _drop(self, slug: str = "gitea-drop", **kwargs):
        return wiki._wiki_note_drop_impl(slug, kwargs.pop("note_md", NOTE), **kwargs)

    def test_one_post_carries_text_and_binary_files(self) -> None:
        png = base64.b64encode(b"\x89PNG\r\nfake").decode("ascii")
        result = self._drop(
            attachments={"trace.log": "line one\n"},
            binary_attachments={"shot.png": png},
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(self.fake.posts), 1)
        posted = self.fake.posts[0]
        self.assertEqual(posted["path"], "/api/v1/repos/ai-agent/mykg/contents")
        self.assertEqual(posted["authorization"], f"token {SECRET_TOKEN}")
        body = posted["body"]
        self.assertEqual(body["branch"], "main")
        self.assertTrue(body["message"].startswith("note: "))
        folder = result["folder"]
        self.assertEqual(body["message"], f"note: {folder}")
        self.assertRegex(folder, r"^\d{4}-\d{2}-\d{2}-\d{6}-gitea-drop$")
        paths = {item["path"]: item for item in body["files"]}
        self.assertEqual(set(paths), {
            f"notes/{folder}/{folder}.md",
            f"notes/{folder}/trace.log",
            f"notes/{folder}/shot.png",
        })
        for item in body["files"]:
            self.assertEqual(item["operation"], "create")
        source = base64.b64decode(paths[f"notes/{folder}/{folder}.md"]["content"])
        self.assertIn(b"capture-hash:", source)
        self.assertEqual(
            base64.b64decode(paths[f"notes/{folder}/trace.log"]["content"]),
            b"line one\n",
        )
        self.assertEqual(
            base64.b64decode(paths[f"notes/{folder}/shot.png"]["content"]),
            b"\x89PNG\r\nfake",
        )
        self.assertEqual(result["commit"], self.fake.commit_sha)
        self.assertEqual(result["source_file"], f"{folder}.md")
        self.assertEqual(result["path"], f"notes/{folder}")
        self.assertFalse(result["archived"])
        self.assertFalse(result["already_exists"])
        self.assertTrue(result["pushed"])
        self.assertIn(f"/src/branch/main/notes/{folder}", result["url"])
        self.assertNotIn(SECRET_TOKEN, result["url"])
        # The patch replaces _run_git; the mock records every call.
        self.assertEqual(wiki._run_git.call_count, 0)
        self.assertTrue(wiki._reindex_wake.is_set())

    def test_retry_returns_already_exists_without_a_second_post(self) -> None:
        first = self._drop()
        second = self._drop()

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertFalse(first["already_exists"])
        self.assertTrue(second["already_exists"])
        self.assertEqual(first["folder"], second["folder"])
        self.assertEqual(first["commit"], second["commit"])
        self.assertEqual(len(self.fake.posts), 1)

    def test_422_and_409_map_to_folder_collision(self) -> None:
        for status in (422, 409):
            with self.subTest(status=status):
                self.fake.post_status = status
                before = len(self.fake.posts)
                result = self._drop(slug=f"collide-{status}")
                self.assertFalse(result["ok"])
                self.assertIn("already exists", result["error"])
                self.assertLessEqual(len(result["error"]), 300)
                self.assertNotIn(SECRET_TOKEN, result["error"])
                self.assertEqual(len(self.fake.posts), before + 1)

    def test_timeout_then_get_returns_success(self) -> None:
        real_request = wiki._gitea_request
        calls = {"post": 0}

        # Store the file as the timed-out POST would have done, then fail the POST.
        def store_then_timeout(method, url, body=None, timeout=None):
            if method == "POST":
                calls["post"] += 1
                for item in (body or {}).get("files") or []:
                    self.fake.files[item["path"]] = base64.b64decode(item["content"])
                raise TimeoutError("timed out")
            return real_request(method, url, body=body, timeout=timeout)

        with mock.patch.object(wiki, "_gitea_request", side_effect=store_then_timeout):
            result = self._drop()

        self.assertTrue(result["ok"], result)
        self.assertEqual(calls["post"], 1)
        self.assertTrue(any("/contents/" in path for path in self.fake.gets))
        self.assertTrue(result["folder"].endswith("-gitea-drop"))

    def test_timeout_then_different_hash_is_a_collision(self) -> None:
        real_request = wiki._gitea_request

        def store_other_hash(method, url, body=None, timeout=None):
            if method == "POST":
                for item in (body or {}).get("files") or []:
                    raw = base64.b64decode(item["content"])
                    if item["path"].endswith(".md") and b"capture-hash:" in raw:
                        raw = raw.replace(
                            b"capture-hash: ",
                            b"capture-hash: ",
                        )
                        text = raw.decode("utf-8")
                        text = text.replace(
                            wiki.note_drop_payload_hash(NOTE, {}, {}),
                            "f" * 64,
                            1,
                        )
                        raw = text.encode("utf-8")
                    self.fake.files[item["path"]] = raw
                raise TimeoutError("timed out")
            return real_request(method, url, body=body, timeout=timeout)

        with mock.patch.object(wiki, "_gitea_request", side_effect=store_other_hash):
            result = self._drop()

        self.assertFalse(result["ok"])
        self.assertIn("already exists", result["error"])
        self.assertNotIn(SECRET_TOKEN, json.dumps(result))

    def test_timeout_without_the_note_returns_a_clear_error(self) -> None:
        real_request = wiki._gitea_request

        def fail_post(method, url, body=None, timeout=None):
            if method == "POST":
                raise TimeoutError(f"timed out token={SECRET_TOKEN}")
            return real_request(method, url, body=body, timeout=timeout)

        with mock.patch.object(wiki, "_gitea_request", side_effect=fail_post):
            result = self._drop()

        self.assertFalse(result["ok"])
        self.assertIn("did not create", result["error"])
        self.assertNotIn(SECRET_TOKEN, json.dumps(result))
        self.assertLessEqual(len(result["error"]), 300)

    def test_timeout_then_failed_read_is_ambiguous(self) -> None:
        def fail_all(method, url, body=None, timeout=None):
            raise TimeoutError("timed out")

        with mock.patch.object(wiki, "_gitea_request", side_effect=fail_all):
            result = self._drop()

        self.assertFalse(result["ok"])
        self.assertIn("ambiguous", result["error"])
        self.assertNotIn(SECRET_TOKEN, result["error"])
        self.assertNotIn("body", result["error"])
        self.assertLessEqual(len(result["error"]), 300)

    def test_error_never_contains_the_token_or_the_note(self) -> None:
        self.fake.post_status = 500
        self.fake.post_error = f"boom {SECRET_TOKEN} body line"
        result = self._drop()

        self.assertFalse(result["ok"])
        encoded = json.dumps(result)
        self.assertNotIn(SECRET_TOKEN, encoded)
        self.assertNotIn("gitea drop test", encoded)
        self.assertLessEqual(len(result["error"]), 300)

    def test_local_clone_hit_skips_the_post(self) -> None:
        existing = wiki.ExistingNoteDrop(
            folder="2026-10-10-120000-gitea-drop",
            relative_dir="notes/2026-10-10-120000-gitea-drop",
            archived=False,
        )
        with mock.patch.object(wiki, "find_existing_note_drop", return_value=existing):
            result = self._drop()

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["already_exists"])
        self.assertEqual(result["folder"], existing.folder)
        self.assertEqual(self.fake.posts, [])

    def test_auto_detects_gitea_and_falls_back_to_git(self) -> None:
        with mock.patch.object(wiki, "WIKI_WRITE_MODE", "auto"):
            wiki._gitea_detected = None
            self.assertTrue(wiki.note_drop_uses_gitea_api())
            # The probe runs once.
            self.assertEqual(self.fake.gets.count("/api/v1/version"), 1)
            self.assertTrue(wiki.note_drop_uses_gitea_api())
            self.assertEqual(self.fake.gets.count("/api/v1/version"), 1)

        self.fake.version = {"error": "no"}
        wiki._gitea_detected = None
        with mock.patch.object(wiki, "WIKI_WRITE_MODE", "auto"):
            self.assertFalse(wiki.note_drop_uses_gitea_api())

        wiki._gitea_detected = None
        with mock.patch.object(wiki, "WIKI_WRITE_MODE", "git"):
            self.assertFalse(wiki.note_drop_uses_gitea_api())

    def test_auto_fallback_uses_git_when_version_endpoint_fails(self) -> None:
        wiki._gitea_detected = None

        def refuse(method, url, body=None, timeout=None):
            raise TimeoutError("version probe failed")

        with mock.patch.object(wiki, "WIKI_WRITE_MODE", "auto"), \
             mock.patch.object(wiki, "_gitea_request", side_effect=refuse):
            self.assertFalse(wiki.note_drop_uses_gitea_api())


if __name__ == "__main__":
    unittest.main()

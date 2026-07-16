from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ["WIKI_MCP_EMBED_PROVIDER"] = "openai"
os.environ["WIKI_MCP_BACKGROUND_REINDEX"] = "0"
os.environ["WIKI_ROOT"] = "/tmp/wiki-mcp-test-global-root"

import wiki as server  # noqa: E402  (the wiki-side module of the combined server)


class FakeProviderError(RuntimeError):
    status_code = 429
    code = "insufficient_quota"


class FakeEmbedder:
    provider = "fake"
    model_name = "fake-model"
    dim = 3
    max_embed_chars = 100
    batch = 10

    def __init__(self, fail: bool = True):
        self.fail = fail
        self.calls = 0

    def embed(self, texts: list[str]):
        self.calls += 1
        if self.fail:
            raise FakeProviderError("embedding unavailable")
        out = []
        for index, _text in enumerate(texts):
            vec = np.array([1.0, float(index + 1), 0.5], dtype=np.float32)
            vec /= np.linalg.norm(vec)
            out.append(vec)
        return out


def write_page(root: Path, name: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(body, encoding="utf-8")


def write_cache(cache_dir: Path, pages: dict[str, tuple[str, np.ndarray]]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    names = sorted(pages)
    np.savez(
        cache_dir / "index.npz",
        model="fake/fake-model@100",
        dim=3,
        names=np.array(names),
        hashes=np.array([pages[name][0] for name in names]),
        vectors=np.stack([pages[name][1] for name in names]).astype(np.float32),
    )


class DegradedStartupTests(unittest.TestCase):
    def setUp(self):
        self.old_idx = server.idx
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "wiki"
        self.cache = self.base / "cache"

    def tearDown(self):
        server.idx = self.old_idx
        self.tmp.cleanup()

    def test_module_import_does_not_synchronously_build_index(self):
        snapshot = self.old_idx.snapshot()
        self.assertFalse(snapshot["ready"])
        self.assertEqual(snapshot["status"], "initializing")
        self.assertEqual(snapshot["last_attempt_unix"], 0.0)

    def test_cached_429_serves_ready_degraded_baseline(self):
        stable_body = "Alpha deployment reference"
        write_page(self.root, "Alpha", stable_body)
        write_page(self.root, "Beta", "Beta changed page")
        write_cache(
            self.cache,
            {"Alpha": (server.hash_body(stable_body), np.array([1.0, 0.0, 0.0], dtype=np.float32))},
        )
        index = server.Index(self.root, FakeEmbedder(fail=True), self.cache)

        stats = index.reindex()

        self.assertFalse(stats["ok"])
        self.assertTrue(stats["ready"])
        self.assertTrue(stats["degraded"])
        self.assertEqual(stats["omitted"], 1)
        self.assertEqual(index.get("Alpha")["body"], stable_body)
        self.assertIsNone(index.get("Beta"))
        results = index.search("Alpha deployment", 3)
        self.assertEqual(results[0]["name"], "Alpha")
        self.assertEqual(results[0]["search_mode"], "lexical-degraded")

        server.idx = index
        response = server.health(None)
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["pages_indexed"], 1)
        self.assertEqual(payload["pages_omitted"], 1)
        self.assertIn("status=429", payload["last_error"])
        cache = np.load(self.cache / "index.npz", allow_pickle=False)
        self.assertEqual([str(name) for name in cache["names"]], ["Alpha"])

    def test_no_cache_429_stays_live_but_unready(self):
        write_page(self.root, "Only", "No cached vector")
        index = server.Index(self.root, FakeEmbedder(fail=True), self.cache)

        stats = index.reindex()

        self.assertFalse(stats["ready"])
        self.assertFalse(index.snapshot()["ready"])
        with self.assertRaises(server.IndexNotReadyError):
            index.search("Only", 3)

        server.idx = index
        live = server.live(None)
        health = server.health(None)
        self.assertEqual(live.status_code, 200)
        self.assertTrue(json.loads(live.body)["live"])
        self.assertEqual(health.status_code, 503)
        self.assertEqual(json.loads(health.body)["status"], "error")

    def test_later_success_atomically_publishes_full_index_and_clears_error(self):
        stable_body = "Alpha deployment reference"
        write_page(self.root, "Alpha", stable_body)
        write_page(self.root, "Beta", "Beta new page")
        write_cache(
            self.cache,
            {"Alpha": (server.hash_body(stable_body), np.array([1.0, 0.0, 0.0], dtype=np.float32))},
        )
        embedder = FakeEmbedder(fail=True)
        index = server.Index(self.root, embedder, self.cache)
        first = index.reindex()
        self.assertTrue(first["degraded"])

        embedder.fail = False
        second = index.reindex()

        self.assertTrue(second["ok"])
        self.assertFalse(second["degraded"])
        self.assertEqual(set(index.pages), {"Alpha", "Beta"})
        snapshot = index.snapshot()
        self.assertEqual(snapshot["status"], "ready")
        self.assertIsNone(snapshot["last_error"])
        self.assertEqual(snapshot["pages_omitted"], 0)
        data = np.load(self.cache / "index.npz", allow_pickle=False)
        self.assertEqual(set(str(name) for name in data["names"]), {"Alpha", "Beta"})


if __name__ == "__main__":
    unittest.main()

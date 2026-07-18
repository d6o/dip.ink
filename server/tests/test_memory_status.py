from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_EMBED_PROVIDER", "openai")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
os.environ.setdefault("WIKI_ROOT", "/tmp/wiki-mcp-test-global-root")

import server  # noqa: E402
import wiki  # noqa: E402


class MemoryStatusTests(unittest.TestCase):
    def setUp(self):
        server.invalidate_status_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "wiki"
        (self.root / ".git").mkdir(parents=True)
        self.patch_root = mock.patch.object(wiki, "WIKI_ROOT", self.root)
        self.patch_repo = mock.patch.object(wiki, "WIKI_REPO_URL", "https://git.example/wiki.git")
        self.patch_root.start()
        self.patch_repo.start()

    def tearDown(self):
        self.patch_repo.stop()
        self.patch_root.stop()
        self.tmp.cleanup()

    def _folder(self, relative: str, slug: str) -> Path:
        folder = self.root / relative / slug
        folder.mkdir(parents=True)
        (folder / f"{slug}.md").write_text("private note body", encoding="utf-8")
        return folder

    def _repo_fixture(self) -> None:
        self._folder("notes", "2026-07-18-100000-inbox")
        self._folder("notes/.deferred", "2026-07-18-090000-deferred")
        for index, reason in enumerate(("malformed frontmatter", "needs operator", "third reason")):
            slug = f"2026-07-18-08000{index}-blocked-{index}"
            folder = self._folder("notes/.blocked", slug)
            (folder / "BLOCKED.md").write_text(
                f"---\nreason: {reason}\n---\nraw private details must not escape",
                encoding="utf-8",
            )
        self._folder(
            "wiki/sources/notes/2026/07/18",
            "2026-07-18-110000-archived",
        )
        queue = self.root / "wiki" / "Curator review queue.md"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text(
            "# Queue\n\n## Queue\n\n- first open item\n- second open item\n",
            encoding="utf-8",
        )

    def test_status_schema_is_bounded_and_non_secret(self):
        self._repo_fixture()
        wiki_snapshot = {
            "ready": True,
            "degraded": True,
            "last_error": "RateLimitError(status=429)",
            "pages_indexed": 10,
            "pages_cataloged": 12,
            "pages_scanned": 12,
            "pages_omitted": 2,
            "last_success_unix": time.time() - 60,
            "status": "degraded",
        }
        graph_result = (
            {"ready": True, "error": None},
            {
                "total": 20,
                "done": 18,
                "pending": 2,
                "partial": 1,
                "changed": 1,
                "lag_seconds": 7200.0,
                "oldest_pending_at": "2026-07-18T08:00:00+00:00",
                "newest_episode": {"slug": "bounded-slug", "completed_at": "now"},
                "watermark": "now",
                "error": None,
            },
            {"count": 3, "age_seconds": 60.0, "newest_at": "now", "error": None},
        )
        usage = {
            "total": 4,
            "errors": 1,
            "cache_hits": 2,
            "by_tool": {"graph_answer": 4},
            "graph_answer_confidence": {
                "high": 2, "medium": 0, "low": 0, "not_found": 1, "error": 1
            },
        }

        async def run():
            with mock.patch.object(wiki.idx, "snapshot", return_value=wiki_snapshot), \
                 mock.patch.object(server, "_graph_status", new=mock.AsyncMock(return_value=graph_result)), \
                 mock.patch.object(server, "_collect_usage_status", return_value=usage), \
                 mock.patch.object(server, "STATUS_BLOCKED_LIMIT", 2):
                return await server.get_status(force=True)

        snapshot = asyncio.run(run())

        self.assertTrue(snapshot["ok"])
        self.assertEqual(set(snapshot["components"]), {"wiki", "graph", "git_clone"})
        self.assertEqual(snapshot["index"]["pages_indexed"], 10)
        self.assertEqual(snapshot["queues"]["inbox"]["count"], 1)
        self.assertEqual(snapshot["queues"]["deferred"]["count"], 1)
        self.assertEqual(snapshot["queues"]["blocked"]["count"], 3)
        self.assertEqual(len(snapshot["queues"]["blocked"]["items"]), 2)
        self.assertTrue(snapshot["queues"]["blocked"]["items_truncated"])
        self.assertEqual(snapshot["queues"]["review_queue_open"], 2)
        self.assertEqual(snapshot["ingest"]["pending"], 2)
        self.assertEqual(snapshot["communities"]["count"], 3)
        self.assertEqual(snapshot["build"]["version"], server.DIPINK_VERSION)
        encoded = json.dumps(snapshot)
        self.assertNotIn("private note body", encoded)
        self.assertNotIn("raw private details", encoded)
        self.assertNotIn("first open item", encoded)

    def test_component_failures_degrade_without_hiding_other_sections(self):
        repo = {
            "component": {"ready": True, "mode": "clone", "error": None},
            "queues": {
                "inbox": {"count": 1, "oldest_age_seconds": 10.0},
                "deferred": {"count": 0, "oldest_age_seconds": None},
                "blocked": {
                    "count": 0, "oldest_age_seconds": None,
                    "items": [], "items_truncated": False,
                },
                "review_queue_open": 0,
            },
            "newest_note": None,
        }
        graph_result = (
            {"ready": False, "error": "ServiceUnavailable"},
            {
                "total": 0, "done": 0, "pending": 0, "partial": 0, "changed": 0,
                "lag_seconds": 0.0, "oldest_pending_at": None,
                "newest_episode": None, "watermark": None, "error": "graph_unavailable",
            },
            {"count": 0, "age_seconds": None, "newest_at": None, "error": "graph_unavailable"},
        )

        async def run():
            with mock.patch.object(server, "_wiki_status", side_effect=RuntimeError("down")), \
                 mock.patch.object(server, "_collect_repo_status", return_value=repo), \
                 mock.patch.object(server, "_graph_status", new=mock.AsyncMock(return_value=graph_result)), \
                 mock.patch.object(server, "_collect_usage_status", return_value={
                     "total": 0, "errors": 0, "cache_hits": 0, "by_tool": {},
                     "graph_answer_confidence": {
                         "high": 0, "medium": 0, "low": 0, "not_found": 0, "error": 0
                     },
                 }):
                return await server.collect_status()

        snapshot = asyncio.run(run())
        self.assertFalse(snapshot["ok"])
        self.assertFalse(snapshot["components"]["wiki"]["ready"])
        self.assertFalse(snapshot["components"]["graph"]["ready"])
        self.assertTrue(snapshot["components"]["git_clone"]["ready"])
        self.assertEqual(snapshot["queues"]["inbox"]["count"], 1)
        self.assertIn("usage_24h", snapshot)
        self.assertIn("build", snapshot)

    def test_mcp_and_http_return_identical_schema(self):
        snapshot = {
            "ok": True,
            "generated_at": "2026-07-18T12:00:00Z",
            "components": {},
            "index": {},
            "queues": {},
            "notes": {},
            "ingest": {},
            "communities": {},
            "usage_24h": {},
            "build": {},
        }

        async def run():
            with mock.patch.object(server, "get_status", new=mock.AsyncMock(return_value=snapshot)), \
                 mock.patch.object(server.core, "record_query"):
                tool_result = await server.memory_status()
                response = await server.http_status(None)
            return tool_result, json.loads(response.body)

        tool_result, http_result = asyncio.run(run())
        self.assertEqual(tool_result, snapshot)
        self.assertEqual(http_result, snapshot)

    def test_status_cache_returns_defensive_copies(self):
        snapshot = {"ok": True, "nested": {"value": 1}}

        async def run():
            with mock.patch.object(server, "collect_status", new=mock.AsyncMock(return_value=snapshot)) as collect:
                first = await server.get_status(force=True)
                first["nested"]["value"] = 999
                second = await server.get_status()
            return second, collect.await_count

        second, count = asyncio.run(run())
        self.assertEqual(second["nested"]["value"], 1)
        self.assertEqual(count, 1)

    def test_memory_status_route_and_pi_registration_exist(self):
        paths = {getattr(route, "path", "") for route in server.app.routes}
        self.assertIn("/api/status", paths)
        extension = (
            Path(__file__).parents[2]
            / "agent-setup" / "pi" / "extensions" / "memory" / "index.ts"
        ).read_text(encoding="utf-8")
        self.assertEqual(extension.count('name: "memory_status"'), 1)
        self.assertIn("parameters: Type.Object({})", extension)


if __name__ == "__main__":
    unittest.main()

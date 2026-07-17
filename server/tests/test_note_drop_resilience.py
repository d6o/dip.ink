"""Regression tests for the 2026-07-17 liveness-kill incident: git ops must
run off the event loop, and stale .git/*.lock files must be cleared so a
container killed mid-commit doesn't wedge every later wiki_note_drop."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ["WIKI_MCP_EMBED_PROVIDER"] = "openai"
os.environ["WIKI_MCP_BACKGROUND_REINDEX"] = "0"
os.environ["WIKI_ROOT"] = "/tmp/wiki-mcp-test-global-root"

import wiki  # noqa: E402


class StaleLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clone = Path(self.tmp.name) / "wiki"
        (self.clone / ".git" / "refs").mkdir(parents=True)
        self._patch = mock.patch.object(wiki, "WIKI_CLONE_PATH", self.clone)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def test_clears_index_lock_and_nested_locks(self):
        index_lock = self.clone / ".git" / "index.lock"
        ref_lock = self.clone / ".git" / "refs" / "heads.lock"
        index_lock.touch()
        ref_lock.touch()

        wiki._clear_stale_git_locks()

        self.assertFalse(index_lock.exists())
        self.assertFalse(ref_lock.exists())

    def test_noop_without_git_dir(self):
        with mock.patch.object(wiki, "WIKI_CLONE_PATH", Path(self.tmp.name) / "absent"):
            wiki._clear_stale_git_locks()  # must not raise

    def test_note_drop_clears_stale_lock_before_git_sync(self):
        """A stale index.lock left by a killed process must be gone by the time
        wiki_note_drop runs its first git command."""
        (self.clone / ".git" / "index.lock").touch()
        seen: list[bool] = []

        def fake_run_git(*args, **kwargs):
            seen.append((self.clone / ".git" / "index.lock").exists())
            raise RuntimeError("stop here")

        with mock.patch.object(wiki, "WIKI_REPO_URL", "https://git.example/wiki.git"), \
             mock.patch.object(wiki, "WIKI_REPO_TOKEN", "t"), \
             mock.patch.object(wiki, "WIKI_ROOT", self.clone), \
             mock.patch.object(wiki, "_run_git", fake_run_git):
            res = wiki._wiki_note_drop_impl("test-slug", "---\ntopic: t\n---\nbody")

        self.assertFalse(res["ok"])
        self.assertTrue(seen, "expected a git command to be attempted")
        self.assertFalse(seen[0], "index.lock still present when git first ran")


class EventLoopSafetyTests(unittest.TestCase):
    def test_note_drop_tool_runs_impl_off_the_event_loop(self):
        """The async MCP tool must delegate to a worker thread so a slow git op
        can't starve the liveness probe."""
        import threading

        loop_thread = threading.current_thread()
        impl_thread: list[threading.Thread] = []

        def fake_impl(slug, note_md, attachments, binary_attachments):
            impl_thread.append(threading.current_thread())
            return {"ok": True}

        async def run():
            with mock.patch.object(wiki, "_wiki_note_drop_impl", fake_impl):
                return await wiki.wiki_note_drop("s", "n")

        res = asyncio.run(run())
        self.assertEqual(res, {"ok": True})
        self.assertNotEqual(impl_thread[0], loop_thread)

    def test_search_tool_runs_impl_off_the_event_loop(self):
        import threading

        loop_thread = threading.current_thread()
        impl_thread: list[threading.Thread] = []

        def fake_impl(query, k):
            impl_thread.append(threading.current_thread())
            return []

        async def run():
            with mock.patch.object(wiki, "_wiki_search_impl", fake_impl):
                return await wiki.wiki_search("q")

        res = asyncio.run(run())
        self.assertEqual(res, [])
        self.assertNotEqual(impl_thread[0], loop_thread)


if __name__ == "__main__":
    unittest.main()

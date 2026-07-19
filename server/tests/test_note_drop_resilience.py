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


class ArchiveAwareIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "wiki"
        self.root.mkdir(parents=True)
        self.patch_root = mock.patch.object(wiki, "WIKI_ROOT", self.root)
        self.patch_root.start()
        wiki._capture_hash_root = None
        wiki._capture_hash_revision_value = None
        wiki._capture_hash_index = {}

    def tearDown(self):
        self.patch_root.stop()
        self.tmp.cleanup()

    def _write_source(self, relative_dir: str, folder: str, capture_hash: str) -> None:
        target = self.root / relative_dir / folder
        target.mkdir(parents=True)
        (target / f"{folder}.md").write_text(
            f"---\ncapture-hash: {capture_hash}\n---\n\n# {folder}\n",
            encoding="utf-8",
        )

    def test_retry_before_curation_finds_live_inbox(self):
        folder = "2026-07-18-120000-retry-me"
        self._write_source("notes", folder, "hash-live")

        existing = wiki.find_existing_note_drop("retry-me", "hash-live")

        self.assertIsNotNone(existing)
        self.assertEqual(existing.folder, folder)
        self.assertFalse(existing.archived)
        result = wiki.note_drop_result(existing, already_exists=True)
        self.assertTrue(result["already_exists"])
        self.assertEqual(result["path"], f"notes/{folder}")

    def test_retry_after_curation_finds_canonical_archive(self):
        folder = "2026-07-18-120000-retry-me"
        archive = "wiki/sources/notes/2026/07/18"
        self._write_source(archive, folder, "hash-archived")

        existing = wiki.find_existing_note_drop("retry-me", "hash-archived")

        self.assertIsNotNone(existing)
        self.assertEqual(existing.folder, folder)
        self.assertTrue(existing.archived)
        result = wiki.note_drop_result(existing, already_exists=True)
        self.assertTrue(result["archived"])
        self.assertEqual(result["path"], f"{archive}/{folder}")

    def test_note_drop_impl_returns_archived_idempotent_result_without_git_write(self):
        folder = "2026-07-18-120000-retry-me"
        note_md = "---\ntopic: retry\n---\nbody"
        capture_hash = wiki.note_drop_payload_hash(note_md, {}, {})
        self._write_source("wiki/sources/notes/2026/07/18", folder, capture_hash)
        (self.root / ".git").mkdir()

        with mock.patch.object(wiki, "WIKI_REPO_URL", "https://git.example/wiki.git"), \
             mock.patch.object(wiki, "WIKI_REPO_TOKEN", "configured"), \
             mock.patch.object(wiki, "_run_git") as run_git:
            result = wiki._wiki_note_drop_impl("retry-me", note_md)

        self.assertTrue(result["ok"])
        self.assertTrue(result["already_exists"])
        self.assertTrue(result["archived"])
        run_git.assert_not_called()

    def test_different_payload_with_same_slug_is_not_deduplicated(self):
        folder = "2026-07-18-120000-retry-me"
        self._write_source("notes", folder, "original-hash")

        self.assertIsNone(wiki.find_existing_note_drop("retry-me", "different-hash"))

    def test_capture_hash_scan_is_cached_for_same_git_revision(self):
        folder = "2026-07-18-120000-retry-me"
        self._write_source("notes", folder, "hash-live")
        git_dir = self.root / ".git" / "refs" / "heads"
        git_dir.mkdir(parents=True)
        (self.root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (git_dir / "main").write_text("abc123\n", encoding="utf-8")

        with mock.patch.object(
            wiki, "read_frontmatter_and_body", wraps=wiki.read_frontmatter_and_body
        ) as parse:
            self.assertIsNotNone(wiki.find_existing_note_drop("retry-me", "hash-live"))
            first_count = parse.call_count
            self.assertIsNotNone(wiki.find_existing_note_drop("retry-me", "hash-live"))
        self.assertGreater(first_count, 0)
        self.assertEqual(parse.call_count, first_count)


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


class SourceNoteMarkdownBackfillTests(unittest.TestCase):
    """The curator terminally quarantines notes whose frontmatter is missing a
    non-empty captured/session/topic. wiki_note_drop is the last writer that
    can guarantee those fields, so it must backfill them deterministically."""

    FOLDER = "2026-07-18-230139-thunderstormwatch-domain-registered"

    def parse(self, rendered: str) -> dict:
        fm, _body = wiki.read_frontmatter_and_body(rendered)
        return fm

    def assert_batchable(self, fm: dict):
        for key in ("captured", "session", "topic"):
            self.assertIn(key, fm)
            self.assertTrue(str(fm[key]).strip(), f"empty {key}")

    def test_note_without_frontmatter_gets_all_three_backfilled(self):
        fm = self.parse(wiki.source_note_markdown(self.FOLDER, "# Title\n\nbody\n"))
        self.assert_batchable(fm)
        self.assertEqual(fm["captured"], "2026-07-18T23:01:39Z")
        self.assertEqual(fm["topic"], "thunderstormwatch domain registered")

    def test_capture_alias_keys_are_promoted(self):
        note = (
            "---\n"
            "capture-session: contentmachine daily content run\n"
            "capture-topic: kotlin comparacoes gap map\n"
            "---\n\nbody\n"
        )
        fm = self.parse(wiki.source_note_markdown(self.FOLDER, note))
        self.assert_batchable(fm)
        self.assertEqual(fm["session"], "contentmachine daily content run")
        self.assertEqual(fm["topic"], "kotlin comparacoes gap map")
        self.assertNotIn("capture-session", fm)
        self.assertNotIn("capture-topic", fm)

    def test_missing_captured_only_is_backfilled_and_rest_preserved(self):
        note = (
            "---\n"
            "session: contentmachine daily growth run\n"
            "topic: repousocuidador guia-escala refresh\n"
            "---\n\nbody\n"
        )
        fm = self.parse(wiki.source_note_markdown(self.FOLDER, note))
        self.assert_batchable(fm)
        self.assertEqual(fm["session"], "contentmachine daily growth run")
        self.assertEqual(fm["topic"], "repousocuidador guia-escala refresh")
        self.assertEqual(fm["captured"], "2026-07-18T23:01:39Z")

    def test_complete_frontmatter_passes_through_unchanged(self):
        note = (
            "---\n"
            "captured: 2026-07-18 20:00:00-03:00\n"
            "session: session A\n"
            "topic: some topic\n"
            "extra: kept\n"
            "---\n\nbody\n"
        )
        fm = self.parse(wiki.source_note_markdown(self.FOLDER, note))
        self.assert_batchable(fm)
        self.assertEqual(str(fm["captured"]), "2026-07-18 20:00:00-03:00")
        self.assertEqual(fm["session"], "session A")
        self.assertEqual(fm["topic"], "some topic")
        self.assertEqual(fm["extra"], "kept")


if __name__ == "__main__":
    unittest.main()

"""Regression tests for the 2026-07-17 liveness-kill incident: git ops must
run off the event loop, and stale .git/*.lock files must be cleared so a
container killed mid-commit doesn't wedge every later wiki_note_drop."""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ["WIKI_MCP_EMBED_PROVIDER"] = "openai"
os.environ["WIKI_MCP_BACKGROUND_REINDEX"] = "0"
os.environ["WIKI_ROOT"] = "/tmp/wiki-mcp-test-global-root"
# Existing tests exercise the git fallback. The Gitea path has its own file.
# Assign the attribute too: an earlier test module may already have imported wiki.
os.environ["WIKI_WRITE_MODE"] = "git"

import wiki  # noqa: E402

wiki.WIKI_WRITE_MODE = "git"


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


class GitAutoMaintenanceTests(unittest.TestCase):
    """Regression tests for the 2026-10-10 HEAD.lock incident: a detached
    `gc --auto` held .git/HEAD.lock outside _repo_lock, and the next
    note-drop `reset --hard` failed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        env_git = ["-c", "user.email=t@example.com", "-c", "user.name=t"]
        wiki._run_git("init", "-q", "-b", "main", cwd=self.repo)
        # The repository asks for gc after every new loose object, detached.
        wiki._run_git("config", "gc.auto", "1", cwd=self.repo)
        wiki._run_git("config", "gc.autoDetach", "true", cwd=self.repo)
        self.env_git = env_git

    def tearDown(self):
        self.tmp.cleanup()

    def _commit_files(self, count: int) -> None:
        for i in range(count):
            (self.repo / f"f{i}.txt").write_text(f"content {i}\n")
        wiki._run_git("add", "-A", cwd=self.repo)
        wiki._run_git(*self.env_git, "commit", "-q", "-m", "c", cwd=self.repo)

    def _loose_count(self) -> int:
        out = subprocess.run(
            ["git", "-C", str(self.repo), "count-objects", "-v"],
            capture_output=True, text=True, check=True,
        ).stdout
        fields = dict(line.split(": ", 1) for line in out.strip().splitlines())
        return int(fields["count"])

    def test_every_git_command_disables_auto_maintenance(self):
        effective = wiki._run_git("config", "--get", "gc.auto", cwd=self.repo).stdout.strip()
        self.assertEqual(effective, "0")
        detach = wiki._run_git("config", "--get", "gc.autoDetach", cwd=self.repo).stdout.strip()
        self.assertEqual(detach, "false")
        maint = wiki._run_git("config", "--get", "maintenance.auto", cwd=self.repo).stdout.strip()
        self.assertEqual(maint, "false")

    def test_commit_does_not_start_gc(self):
        self._commit_files(5)
        self.assertFalse((self.repo / ".git" / "gc.pid").exists())
        self.assertGreater(self._loose_count(), 0, "commit unexpectedly packed objects")

    def test_foreground_maintenance_packs_before_return(self):
        # `gc --auto` estimates loose objects from .git/objects/17 only, so
        # make enough objects that the sample is not empty.
        self._commit_files(3000)
        self.assertGreater(self._loose_count(), 0)
        with mock.patch.object(wiki, "GIT_GC_AUTO_THRESHOLD", 1):
            wiki._run_foreground_maintenance(self.repo)
        # Synchronous: the objects are packed when the call returns.
        self.assertEqual(self._loose_count(), 0)
        self.assertEqual(list((self.repo / ".git").glob("*.lock")), [])

    def test_foreground_maintenance_disabled_by_zero_threshold(self):
        self._commit_files(3)
        before = self._loose_count()
        with mock.patch.object(wiki, "GIT_GC_AUTO_THRESHOLD", 0):
            wiki._run_foreground_maintenance(self.repo)
        self.assertEqual(self._loose_count(), before)


class NoteDropFailureRecordTests(unittest.TestCase):
    def test_failed_drop_records_bounded_reason(self):
        recorded: list[dict] = []
        failure = {"ok": False, "error": "failed to sync repo to origin/main: " + "x" * 1000}
        with mock.patch.object(wiki, "_wiki_note_drop_impl", return_value=failure), \
             mock.patch.object(wiki, "_record_query", recorded.append):
            res = wiki._wiki_note_drop_recorded("s", "body", None, None)
        self.assertIs(res, failure)
        self.assertEqual(recorded[0]["outcome"], "error")
        self.assertTrue(recorded[0]["error"].startswith("failed to sync repo"))
        self.assertLessEqual(len(recorded[0]["error"]), 300)

    def test_successful_drop_records_no_reason(self):
        recorded: list[dict] = []
        with mock.patch.object(wiki, "_wiki_note_drop_impl", return_value={"ok": True}), \
             mock.patch.object(wiki, "_record_query", recorded.append):
            wiki._wiki_note_drop_recorded("s", "body", None, None)
        self.assertEqual(recorded[0]["outcome"], "ok")
        self.assertNotIn("error", recorded[0])


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


class NoteFrontmatterValidationTests(unittest.TestCase):
    def assert_rejected_without_changes(self, note_md: str, error_text: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_dir = root / ".git"
            git_dir.mkdir()
            lock = git_dir / "index.lock"
            lock.write_text("keep", encoding="utf-8")
            before = sorted(p.relative_to(root) for p in root.rglob("*"))
            with mock.patch.object(wiki, "WIKI_REPO_URL", "https://git.example/wiki.git"), \
                 mock.patch.object(wiki, "WIKI_REPO_TOKEN", "configured"), \
                 mock.patch.object(wiki, "WIKI_ROOT", root), \
                 mock.patch.object(wiki, "_run_git") as run_git, \
                 mock.patch.object(wiki, "_clear_stale_git_locks") as clear_locks, \
                 mock.patch.object(wiki, "find_existing_note_drop") as find_existing, \
                 mock.patch.object(wiki, "_repo_lock") as repo_lock:
                result = wiki._wiki_note_drop_impl("invalid-frontmatter", note_md)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "invalid_frontmatter")
            self.assertIn(error_text, result["error"])
            self.assertLessEqual(len(result["error"]), 300)
            run_git.assert_not_called()
            clear_locks.assert_not_called()
            find_existing.assert_not_called()
            repo_lock.__enter__.assert_not_called()
            self.assertEqual(before, sorted(p.relative_to(root) for p in root.rglob("*")))
            self.assertEqual(lock.read_text(encoding="utf-8"), "keep")

    def test_indented_topic_is_rejected_before_side_effects(self):
        note = "---\ncaptured: 2026-10-09T04:17:59Z\nsession: batomon release\n topic: sync diagnosis\n---\nbody"
        self.assert_rejected_without_changes(note, "note line 4, column 7")

    def test_unquoted_colon_has_actionable_error(self):
        note = "---\nsession: fix: the thing\n---\nbody"
        self.assert_rejected_without_changes(note, "quote values")

    def test_non_mapping_frontmatter_is_rejected(self):
        for raw in ("- captured\n- session", "text", "null", "false", "42", "!!set {a: null}"):
            with self.subTest(raw=raw):
                self.assert_rejected_without_changes(f"---\n{raw}\n---\nbody", "must be a YAML mapping")

    def test_unclosed_frontmatter_is_rejected(self):
        for note in ("---\nsession: s\nbody", "---"):
            with self.subTest(note=note):
                self.assert_rejected_without_changes(note, "closing '---' line")

    def test_crlf_and_bom_do_not_bypass_validation(self):
        note = "---\nsession: s\n topic: t\n---\nbody"
        self.assert_rejected_without_changes("\ufeff" + note.replace("\n", "\r\n"), "Invalid YAML")

    def test_large_invalid_value_does_not_echo_the_note(self):
        note = "---\nsession: " + "sensitive-content " * 1000 + ": invalid\n---\nbody"
        self.assert_rejected_without_changes(note, "Invalid YAML")
        with self.assertRaisesRegex(ValueError, "Invalid YAML") as error:
            wiki.source_note_markdown("2026-10-09-120000-example", note)
        self.assertNotIn("sensitive-content", str(error.exception))

    def test_excessive_nesting_returns_static_error_without_changes(self):
        notes = (
            "---\nsession: " + "[" * 1000 + "x\n---\nbody",
            "---\nsession: " + "[" * 1000 + "private-payload-marker" + "]" * 1000 + "\n---\nbody",
            "---\nsession: " + "{nested: " * 1000 + "private-payload-marker" + "}" * 1000 + "\n---\nbody",
        )
        self.assertEqual(len(notes[0].encode("utf-8")), 1023)
        for note in notes:
            with self.subTest(length=len(note)):
                self.assert_rejected_without_changes(note, "Reduce nested lists or mappings")
                with self.assertRaises(ValueError) as error:
                    wiki.read_frontmatter_and_body(note, strict=True)
                self.assertNotIn("private-payload-marker", str(error.exception))
                fm, body = wiki.read_frontmatter_and_body(note)
                self.assertEqual(fm, {})
                self.assertEqual(body, "body")

    def test_yaml_errors_do_not_include_tag_or_alias_names(self):
        cases = (
            ("topic: !private-payload-marker value", "standard YAML types"),
            ("topic: *private-payload-marker", "define each alias"),
            ("topic: [value, private-payload-marker: : invalid]", "quote values"),
        )
        for raw, advice in cases:
            note = f"---\n{raw}\n---\nbody"
            with self.subTest(raw=raw):
                self.assert_rejected_without_changes(note, advice)
                with self.assertRaises(ValueError) as error:
                    wiki.read_frontmatter_and_body(note, strict=True)
                self.assertIn("note line 2", str(error.exception))
                self.assertNotIn("private-payload-marker", str(error.exception))
                self.assertNotIn(raw, str(error.exception))

    def test_valid_capture_inputs_reach_commit_and_preserve_metadata(self):
        inputs = (
            ("body", {}),
            ("---\n{}\n---\nbody", {}),
            ("---\ntopic: provided topic\n---\nbody", {"topic": "provided topic"}),
            ("---\ncapture-session: legacy session\ncapture-topic: legacy topic\n---\nbody",
             {"session": "legacy session", "topic": "legacy topic"}),
            ("---\ncaptured: '2026-10-09T12:00:00Z'\nsession: provided session\ntopic: provided topic\n---\nbody",
             {"captured": "2026-10-09T12:00:00Z", "session": "provided session", "topic": "provided topic"}),
        )
        for note, expected in inputs:
            with self.subTest(note=note), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".git").mkdir()
                with mock.patch.object(wiki, "WIKI_REPO_URL", "https://git.example/wiki.git"), \
                     mock.patch.object(wiki, "WIKI_REPO_TOKEN", "configured"), \
                     mock.patch.object(wiki, "WIKI_ROOT", root), \
                     mock.patch.object(wiki, "_run_git", return_value=mock.Mock(stdout="abc123")) as run_git, \
                     mock.patch.object(wiki, "_clear_stale_git_locks"), \
                     mock.patch.object(wiki, "find_existing_note_drop", return_value=None):
                    result = wiki._wiki_note_drop_impl("valid-frontmatter", note)
                self.assertTrue(result["ok"], result)
                calls = [call.args[0] for call in run_git.call_args_list]
                self.assertIn("commit", calls)
                self.assertIn("push", calls)
                stored = root / result["path"] / result["source_file"]
                fm, body = wiki.read_frontmatter_and_body(stored.read_text(encoding="utf-8"))
                for key in ("captured", "session", "topic"):
                    self.assertTrue(str(fm[key]).strip())
                for key, value in expected.items():
                    self.assertEqual(fm[key], value)
                self.assertIn("body", body)

    def test_index_reads_tolerate_damaged_frontmatter(self):
        for raw in ("- a", "null", "session: s\n topic: t"):
            with self.subTest(raw=raw):
                fm, body = wiki.read_frontmatter_and_body(f"---\n{raw}\n---\nbody")
                self.assertEqual(fm, {})
                self.assertEqual(body, "body")


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

    def test_empty_frontmatter_retains_backfill(self):
        for raw in ("", "# capture metadata omitted\n", "{}\n"):
            with self.subTest(raw=raw):
                fm = self.parse(wiki.source_note_markdown(self.FOLDER, f"---\n{raw}---\nbody"))
                self.assert_batchable(fm)

    def test_crlf_frontmatter_preserves_provided_metadata(self):
        note = "---\ncaptured: 2026-10-09T12:00:00Z\nsession: a session\ntopic: a topic\n---\nbody"
        fm = self.parse(wiki.source_note_markdown(self.FOLDER, "\ufeff" + note.replace("\n", "\r\n")))
        self.assertEqual(fm["session"], "a session")
        self.assertEqual(fm["topic"], "a topic")
        self.assertEqual(str(fm["captured"]), "2026-10-09 12:00:00+00:00")

    def test_capture_alias_keys_are_promoted(self):
        note = (
            "---\n"
            "capture-captured: '2026-07-18T20:00:00Z'\n"
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
        self.assertNotIn("capture-captured", fm)
        self.assertEqual(fm["captured"], "2026-07-18T20:00:00Z")

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

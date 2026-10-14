#!/usr/bin/env python3
"""Do local regression tests for the prepared batch boundary."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
GUARD = SCRIPT_DIR / "processnotes-batch-guard.py"
PREPARE = SCRIPT_DIR / "processnotes-prepare-inbox.sh"
PAYLOAD = "SYNTHETIC-PRIVATE-PAYLOAD-DO-NOT-LOG"


class PreparedBoundaryTests(unittest.TestCase):
    def setUp(self):
        if not GUARD.is_file():
            self.fail(f"guard interface is absent: {GUARD}")
        self.temp = tempfile.TemporaryDirectory(prefix="prepared-boundary-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo with spaces ü"
        self.root.mkdir()
        self.state = Path(self.temp.name) / "private-state.json"
        self.command("git", "init", "-q", "-b", "main")
        self.command("git", "config", "user.name", "fixture")
        self.command("git", "config", "user.email", "fixture@example.invalid")
        (self.root / "notes/.deferred").mkdir(parents=True)
        (self.root / "notes/.blocked").mkdir()
        (self.root / "wiki").mkdir()
        self.names = [f"2026-01-01-0000{i:02d}-fixture" for i in range(1, 14)]
        for i, name in enumerate(self.names):
            # The oldest note starts deferred. Twelve notes start live.
            self.note((".deferred/" if i == 0 else "") + name)
        self.blocked_name = "2025-01-01-000000-protected"
        self.note(".blocked/" + self.blocked_name)
        self.command("git", "add", ".")
        self.command("git", "commit", "-q", "-m", "Add synthetic notes")

    def command(self, *args, env=None):
        return subprocess.run(args, cwd=self.root, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True)

    def note(self, relative):
        path = self.root / "notes" / relative
        path.mkdir(parents=True)
        (path / "NOTE.md").write_text(
            "---\ncaptured: 2026-01-01T00:00:00Z\n"
            "session: disposable fixture\ntopic: boundary fixture\n---\n\n"
            f"# Fixture\n\n{PAYLOAD}\n", encoding="utf-8")
        (path / "attachment ü.bin").write_bytes(b"\x00\x01synthetic attachment\xff")
        return path

    def prepare(self, cap=4, success=True):
        env = dict(os.environ, NOTES_DIR="notes", INBOX_BATCH_SIZE=str(cap),
                   BLOCK_NOTE_BIN=str(SCRIPT_DIR / "processnotes-block-note.sh"))
        result = subprocess.run(["bash", str(PREPARE)], cwd=self.root, env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertNotIn(PAYLOAD, result.stdout + result.stderr)
        return result

    def guard(self, *args, success=True):
        result = subprocess.run([sys.executable, str(GUARD), *map(str, args)],
                                cwd=self.root, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertNotIn(PAYLOAD, result.stdout + result.stderr)
        return result

    def capture(self):
        # Each disposable subcase gets a new immutable state file.
        self.state = Path(self.temp.name) / ("private-state-" + str(__import__('uuid').uuid4()) + ".json")
        self.prepare()
        result = self.guard("capture", "--notes-dir", "notes", "--cap", "4",
                            "--state", self.state)
        metadata = json.loads(result.stdout)
        # The caller receives a digest, not the private inventory.
        digest = metadata.get("sha256", metadata.get("state_sha256"))
        self.assertIsInstance(digest, str, metadata)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, hashlib.sha256(self.state.read_bytes()).hexdigest())
        for name in self.names + [self.blocked_name]:
            self.assertNotIn(name, result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)
        self.digest = digest
        return metadata

    def verify(self, success=True):
        return self.guard("verify", "--state", self.state, "--sha256", self.digest,
                          "--repo", self.root, success=success)

    def inventory(self):
        # Include file types, modes, and bytes. Do not follow links.
        result = {}
        for path in sorted((self.root / "notes").rglob("*")):
            info = path.lstat()
            key = str(path.relative_to(self.root))
            value = (stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode))
            if path.is_symlink():
                value += (os.readlink(path),)
            elif path.is_file():
                value += (hashlib.sha256(path.read_bytes()).hexdigest(),)
            result[key] = value
        return result

    def canonical(self, index=0, copy=False):
        name = self.names[index]
        source = self.root / "notes" / name
        destination = self.root / "wiki/sources/notes/2026/01/01" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if copy:
            shutil.copytree(source, destination)
        else:
            shutil.move(source, destination)
        (destination / "NOTE.md").rename(destination / f"{name}.md")
        source_file = destination / f"{name}.md"
        text = source_file.read_text()
        source_file.write_text(text.replace("---\n", "---\ntype: source\n", 1).replace("# Fixture", "# " + name, 1))
        return destination

    def test_cap_four_from_thirteen_with_existing_deferred(self):
        self.capture()
        live = [p.name for p in (self.root / "notes").iterdir()
                if p.is_dir() and not p.name.startswith(".")]
        held = [p.name for p in (self.root / "notes/.deferred").iterdir()]
        self.assertEqual(sorted(live), self.names[:4])
        self.assertEqual(sorted(held), self.names[4:])
        self.verify()

    def test_dual_roots_fail_before_any_mutation(self):
        # Include a bare note and malformed note to detect an early mutation.
        shutil.copytree(self.root / "notes" / self.names[1],
                        self.root / "notes/.deferred" / self.names[1])
        (self.root / "notes/bare.md").write_text(PAYLOAD, encoding="utf-8")
        self.note("0000-malformed")
        (self.root / "notes/0000-malformed/NOTE.md").write_text("bad", encoding="utf-8")
        before = self.inventory()
        self.prepare(success=False)
        self.assertEqual(before, self.inventory())
        self.assertFalse(self.state.exists())

    def test_capture_rejects_over_cap_without_mutation(self):
        before = self.inventory()
        self.guard("capture", "--notes-dir", "notes", "--cap", "4",
                   "--state", self.state, success=False)
        self.assertEqual(before, self.inventory())

    def test_restore_held_live_paths(self):
        self.capture()
        self.command("git", "restore", "--", *[f"notes/{n}" for n in self.names[4:]])
        self.verify(success=False)

    def test_restore_promoted_deferred_path(self):
        self.capture()
        self.command("git", "restore", "--", f"notes/.deferred/{self.names[0]}")
        self.verify(success=False)

    def test_protected_changes(self):
        for kind in ("deferred", "blocked"):
            for change in ("payload", "file_mode", "folder_mode", "file_type",
                           "folder_type", "extra_file", "removed_file", "removed_folder"):
                with self.subTest(kind=kind, change=change):
                    # Reset only the disposable fixture, then capture a fresh state.
                    self.command("git", "reset", "--hard", "-q", "HEAD")
                    self.command("git", "clean", "-fdq")
                    self.capture()
                    path = self.root / "notes" / (
                        f".deferred/{self.names[4]}" if kind == "deferred"
                        else f".blocked/{self.blocked_name}")
                    file = path / "attachment ü.bin"
                    if change == "payload":
                        file.write_bytes(b"changed synthetic attachment")
                    elif change == "file_mode":
                        file.chmod(0o755)
                    elif change == "folder_mode":
                        path.chmod(stat.S_IMODE(path.stat().st_mode) ^ 0o010)
                    elif change == "file_type":
                        file.unlink()
                        file.symlink_to("NOTE.md")
                    elif change == "folder_type":
                        shutil.rmtree(path)
                        path.symlink_to(self.root / "wiki", target_is_directory=True)
                    elif change == "extra_file":
                        (path / "new.bin").write_bytes(b"extra")
                    elif change == "removed_file":
                        file.unlink()
                    else:
                        shutil.rmtree(path)
                    self.verify(success=False)

    def test_new_live_identity_fails(self):
        self.capture()
        self.note("2026-01-02-000000-new")
        self.verify(success=False)

    def test_live_identity_replacement_fails_even_below_cap(self):
        self.capture()
        shutil.rmtree(self.root / "notes" / self.names[0])
        self.note("2026-01-02-000000-new")
        self.verify(success=False)

    def test_new_deferred_identity_fails(self):
        self.capture()
        self.note(".deferred/2026-01-02-000000-new")
        self.verify(success=False)

    def test_held_move_to_live_fails(self):
        self.capture()
        name = self.names[4]
        shutil.move(self.root / "notes/.deferred" / name, self.root / "notes" / name)
        self.verify(success=False)

    def test_selected_extra_path_fails(self):
        self.capture()
        (self.root / "notes" / self.names[0] / "new-attachment.bin").write_bytes(b"extra")
        self.verify(success=False)

    def test_selected_attachment_changes_fail(self):
        for change in ("payload", "mode", "type"):
            with self.subTest(change=change):
                self.command("git", "reset", "--hard", "-q", "HEAD")
                self.command("git", "clean", "-fdq")
                self.capture()
                path = self.root / "notes" / self.names[0] / "attachment ü.bin"
                if change == "payload":
                    path.write_bytes(b"changed")
                elif change == "mode":
                    path.chmod(0o755)
                else:
                    path.unlink()
                    path.symlink_to("NOTE.md")
                self.verify(success=False)

    def test_state_digest_tamper_fails_without_mutation(self):
        self.capture()
        self.state.write_bytes(self.state.read_bytes() + b" ")
        before = self.inventory()
        self.verify(success=False)
        self.assertEqual(before, self.inventory())

    def test_wrong_digest_fails(self):
        self.capture()
        self.digest = "0" * 64
        self.verify(success=False)

    def test_canonicalized_selected_still_live_fails(self):
        self.capture()
        self.canonical(copy=True)
        self.verify(success=False)

    def test_preexisting_canonical_copy_cannot_keep_selected_live(self):
        self.prepare()
        destination = self.canonical(copy=True)
        self.capture()
        self.guard("verify", "--state", self.state, "--sha256", self.digest, "--repo", self.root, "--prepared")
        self.verify(success=False)
        self.assertTrue(destination.is_dir())
        self.assertTrue((self.root / "notes" / self.names[0]).is_dir())

    def test_preexisting_canonical_copy_allows_preserved_block(self):
        self.prepare()
        destination = self.canonical(copy=True)
        self.capture()
        self.guard("verify", "--state", self.state, "--sha256", self.digest, "--repo", self.root, "--prepared")
        payload = {str(p.relative_to(destination)): p.read_bytes() for p in destination.iterdir() if p.is_file()}
        env = dict(os.environ, NOTES_DIR="notes", BLOCKED_AT="2026-01-02T03:04:05Z")
        self.command("bash", str(SCRIPT_DIR / "processnotes-block-note.sh"), self.names[0], "already-ingested", env=env)
        self.verify()
        self.assertEqual(payload, {str(p.relative_to(destination)): p.read_bytes() for p in destination.iterdir() if p.is_file()})

    def archived_copy(self, date="2025/12/31"):
        self.prepare()
        correct = self.canonical(copy=True)
        destination = self.root / "wiki/sources/notes" / date / self.names[0]
        destination.parent.mkdir(parents=True, exist_ok=True)
        correct.rename(destination)
        return destination

    def archive_payload(self, destination):
        return {str(path.relative_to(destination)): (
            stat.S_IMODE(path.lstat().st_mode), path.read_bytes())
            for path in destination.rglob("*") if path.is_file()}

    def test_wrong_date_archive_forbids_second_canonical_action(self):
        archive = self.archived_copy()
        original = self.archive_payload(archive)
        self.capture()
        self.canonical()
        self.verify(success=False)
        self.assertEqual(original, self.archive_payload(archive))

    def test_wrong_date_archive_forbids_post_pi_live_noop(self):
        archive = self.archived_copy()
        original = self.archive_payload(archive)
        self.capture()
        self.guard("verify", "--state", self.state, "--sha256", self.digest,
                   "--repo", self.root, "--prepared")
        self.verify(success=False)
        self.assertEqual(original, self.archive_payload(archive))
        self.assertTrue((self.root / "notes" / self.names[0]).is_dir())

    def test_wrong_date_archive_preserves_prepared_duplicate_block(self):
        archive = self.archived_copy()
        original = self.archive_payload(archive)
        self.capture()
        self.guard("verify", "--state", self.state, "--sha256", self.digest,
                   "--repo", self.root, "--prepared")
        env = dict(os.environ, NOTES_DIR="notes", BLOCKED_AT="2026-01-02T03:04:05Z")
        self.command("bash", str(SCRIPT_DIR / "processnotes-block-note.sh"),
                     self.names[0], "already-ingested", env=env)
        self.verify()
        self.assertEqual(original, self.archive_payload(archive))

    def test_wrong_date_archive_preserves_duplicate_drop(self):
        archive = self.archived_copy()
        original = self.archive_payload(archive)
        self.capture()
        shutil.rmtree(self.root / "notes" / self.names[0])
        self.verify()
        self.assertEqual(original, self.archive_payload(archive))

    def test_post_pi_duplicate_archived_slugs_fail(self):
        self.capture()
        canonical = self.canonical()
        duplicate = self.root / "wiki/sources/notes/2025/12/31" / self.names[0]
        duplicate.parent.mkdir(parents=True)
        shutil.copytree(canonical, duplicate)
        self.verify(success=False)
        self.assertTrue(canonical.is_dir())
        self.assertTrue(duplicate.is_dir())

    def test_prior_duplicate_archived_slugs_fail_before_mutation(self):
        archive = self.archived_copy()
        duplicate = self.root / "wiki/sources/notes/2024/01/01" / self.names[0]
        duplicate.parent.mkdir(parents=True)
        shutil.copytree(archive, duplicate)
        original = self.archive_payload(archive)
        before = self.inventory()
        self.prepare(success=False)
        self.assertEqual(before, self.inventory())
        self.assertEqual(original, self.archive_payload(archive))
        self.assertEqual(original, self.archive_payload(duplicate))

    def selected_attachment_link(self, target):
        # Install the link after the first promotion, then capture that state.
        self.prepare()
        link = self.root / "notes" / self.names[0] / "linked attachment ü.bin"
        link.symlink_to(target)
        return link

    def test_canonical_move_rejects_missing_link_referent(self):
        raw = self.root / "raw"
        raw.mkdir()
        (raw / "doc.txt").write_bytes(b"synthetic stable raw document")
        self.selected_attachment_link("../../raw/doc.txt")
        self.capture()
        destination = self.canonical()
        self.assertFalse((destination / "linked attachment ü.bin").exists())
        self.verify(success=False)
        self.assertEqual((raw / "doc.txt").read_bytes(), b"synthetic stable raw document")

    def test_canonical_move_rejects_different_link_referent(self):
        raw = self.root / "raw"
        raw.mkdir()
        (raw / "doc.txt").write_bytes(b"synthetic original target")
        other = self.root / "wiki/sources/notes/2026/01/raw"
        other.mkdir(parents=True)
        (other / "doc.txt").write_bytes(b"synthetic different target")
        self.selected_attachment_link("../../raw/doc.txt")
        self.capture()
        destination = self.canonical()
        self.assertEqual((destination / "linked attachment ü.bin").read_bytes(),
                         b"synthetic different target")
        self.verify(success=False)

    def test_canonical_move_rejects_same_bytes_at_different_referent(self):
        raw = self.root / "raw"
        raw.mkdir()
        (raw / "doc.txt").write_bytes(b"synthetic identical bytes")
        other = self.root / "wiki/sources/notes/2026/01/raw"
        other.mkdir(parents=True)
        (other / "doc.txt").write_bytes(b"synthetic identical bytes")
        self.selected_attachment_link("../../raw/doc.txt")
        self.capture()
        self.canonical()
        self.verify(success=False)

    def test_external_attachment_link_fails_before_mutation(self):
        outside = Path(self.temp.name) / "outside synthetic.bin"
        outside.write_bytes(b"synthetic outside document")
        self.selected_attachment_link(str(outside))
        before = self.inventory()
        self.prepare(success=False)
        self.assertEqual(before, self.inventory())

    def test_attachment_link_cycle_fails_before_mutation(self):
        first = self.selected_attachment_link("second link.bin")
        (first.parent / "second link.bin").symlink_to(first.name)
        before = self.inventory()
        self.prepare(success=False)
        self.assertEqual(before, self.inventory())

    def test_canonical_move_preserves_internal_attachment_link(self):
        self.selected_attachment_link("attachment ü.bin")
        self.capture()
        destination = self.canonical()
        self.verify()
        self.assertEqual((destination / "linked attachment ü.bin").read_bytes(),
                         b"\x00\x01synthetic attachment\xff")

    def test_canonical_move_preserves_internal_attachment_link_chain(self):
        first = self.selected_attachment_link("attachment ü.bin")
        (first.parent / "second link.bin").symlink_to(first.name)
        self.capture()
        destination = self.canonical()
        self.verify()
        self.assertEqual((destination / "second link.bin").read_bytes(),
                         b"\x00\x01synthetic attachment\xff")

    def test_complete_folder_moves_with_attachments_work(self):
        self.capture()
        for index in range(4):
            dest = self.canonical(index)
            self.assertTrue((dest / "attachment ü.bin").is_file())
            # Canonical frontmatter and H1 changes preserve the source body.
            self.assertIn("type: source", (dest / f"{self.names[index]}.md").read_text())
        self.verify()

    def test_canonical_attachment_change_fails(self):
        self.capture()
        destination = self.canonical()
        (destination / "attachment ü.bin").write_bytes(b"changed")
        self.verify(success=False)

    def test_incomplete_canonical_move_fails(self):
        self.capture()
        destination = self.canonical()
        (destination / "attachment ü.bin").unlink()
        self.verify(success=False)

    def test_deliberate_drop_works(self):
        self.capture()
        for name in self.names[:4]:
            shutil.rmtree(self.root / "notes" / name)
        self.verify()

    def test_blocked_move_with_receipt_works(self):
        self.capture()
        for name in self.names[:4]:
            self.command("bash", str(SCRIPT_DIR / "processnotes-block-note.sh"),
                         name, "corrupt-input", env=dict(os.environ, NOTES_DIR="notes",
                                                       BLOCKED_AT="2026-01-02T03:04:05Z"))
        self.verify()

    def test_blocked_move_without_receipt_fails(self):
        self.capture()
        name = self.names[0]
        shutil.move(self.root / "notes" / name, self.root / "notes/.blocked" / name)
        self.verify(success=False)

    def test_blocked_payload_change_fails(self):
        self.capture()
        name = self.names[0]
        self.command("bash", str(SCRIPT_DIR / "processnotes-block-note.sh"), name,
                     "corrupt-input", env=dict(os.environ, NOTES_DIR="notes"))
        (self.root / "notes/.blocked" / name / "NOTE.md").write_text("changed", encoding="utf-8")
        self.verify(success=False)

    def test_noop_selected_still_live_works(self):
        self.capture()
        before = self.inventory()
        self.verify()
        self.assertEqual(before, self.inventory())

    def test_symlink_folder_fails_before_preparation_mutation(self):
        self.note("symlink-target")
        (self.root / "notes/0000-link").symlink_to("symlink-target", target_is_directory=True)
        before = self.inventory()
        self.prepare(success=False)
        self.assertEqual(before, self.inventory())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--guard", type=Path, default=GUARD)
    parser.add_argument("--prepare", type=Path, default=PREPARE)
    options, remaining = parser.parse_known_args()
    GUARD = options.guard.resolve()
    PREPARE = options.prepare.resolve()
    unittest.main(argv=[sys.argv[0], *remaining])

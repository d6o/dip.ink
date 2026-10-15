#!/usr/bin/env python3
"""Check wikilint through its CLI with disposable wiki pages."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


WIKILINT = Path(__file__).resolve().with_name("wikilint.py")


class WikilintTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wikilint-targets-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo with spaces ü"
        self.root.mkdir()
        (self.root / "wiki").mkdir()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def page(self, relative, body="", frontmatter="type: concept\n", title=None):
        name = Path(relative).stem if title is None else title
        return self.write(relative, f"---\n{frontmatter}---\n# {name}\n\n{body}\n")

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(WIKILINT), *map(str, args)],
            cwd=self.root, text=True, capture_output=True, check=False,
        )

    def report(self, *paths, code=0, pages=1, issues=None):
        result = self.cli("--json", *paths)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(report["pages"], pages, report)
        if issues is not None:
            self.assertEqual(report["issues"], issues)
        return report

    def test_selected_page_resolves_unselected_target(self):
        self.page("wiki/Selected.md", "[[Existing]]")
        self.page("wiki/Existing.md")
        self.report("wiki/Selected.md", issues=[])

    def test_missing_target_is_an_error(self):
        self.page("wiki/Selected.md", "[[Missing]]")
        report = self.report("wiki/Selected.md", code=1)
        self.assertEqual(report["summary"], {"error": 1})
        self.assertEqual(report["issues"], [{
            "severity": "error", "file": "wiki/Selected.md",
            "msg": "broken wikilink: [[Missing]]",
        }])

    def test_case_mismatch_is_a_warning(self):
        self.page("wiki/Selected.md", "[[existing]]")
        self.page("wiki/Existing.md")
        report = self.report("wiki/Selected.md", code=2)
        self.assertEqual(report["summary"], {"warning": 1})
        self.assertEqual(report["issues"], [{
            "severity": "warning", "file": "wiki/Selected.md",
            "msg": "case mismatch: [[existing]] should be [[Existing]]",
        }])

    def test_case_collision_uses_full_scan_canonical_name(self):
        self.page("wiki/Existing.md", "[[EXISTING]]")
        self.page("wiki/nested/existing.md", "[[EXISTING]]")
        full = self.report(code=2, pages=2)
        for path in ("wiki/Existing.md", "wiki/nested/existing.md"):
            with self.subTest(path=path):
                selected = self.report(path, code=2)
                self.assertEqual(selected["issues"],
                                 [issue for issue in full["issues"] if issue["file"] == path])

    def test_unselected_invalid_page_is_not_linted(self):
        self.page("wiki/Selected.md", "[[Invalid]]")
        self.write("wiki/Invalid.md", "# Wrong title\n[[Missing]]\n")
        self.report("wiki/Selected.md", issues=[])

    def test_target_with_malformed_frontmatter_still_exists(self):
        self.page("wiki/Selected.md", "[[Malformed]]")
        self.page("wiki/Malformed.md", frontmatter="type: [\n")
        self.report("wiki/Selected.md", issues=[])
        report = self.report(code=1, pages=2)
        self.assertEqual(report["summary"], {"error": 1})
        self.assertEqual(report["issues"][0]["file"], "wiki/Malformed.md")
        self.assertIn("YAML parse error:", report["issues"][0]["msg"])

    def test_full_scan_resolves_all_pages(self):
        self.page("wiki/Selected.md", "[[Existing]]")
        self.page("wiki/Existing.md", "[[Selected]]")
        self.report(pages=2, issues=[])

    def test_full_scan_warning_keeps_exit_code_two(self):
        self.page("wiki/Selected.md", "[[existing]]")
        self.page("wiki/Existing.md")
        report = self.report(code=2, pages=2)
        self.assertEqual(report["summary"], {"warning": 1})
        self.assertEqual(report["issues"][0]["msg"],
                         "case mismatch: [[existing]] should be [[Existing]]")

    def test_unselected_read_error_is_not_reported(self):
        self.page("wiki/Selected.md")
        (self.root / "wiki/Unreadable.md").write_bytes(b"\xff")
        self.report("wiki/Selected.md", issues=[])
        report = self.report(code=1, pages=2)
        self.assertEqual(report["summary"], {"error": 1})
        self.assertEqual(report["issues"][0]["file"], "wiki/Unreadable.md")
        self.assertIn("cannot read:", report["issues"][0]["msg"])

    def test_full_scan_reports_each_page_and_severity(self):
        self.page("wiki/Selected.md", "[[existing]]\n[[Missing]]")
        self.page("wiki/Existing.md")
        self.write("wiki/Invalid.md", "# Invalid\n")
        report = self.report(code=1, pages=3)
        self.assertEqual(report["summary"], {"error": 2, "warning": 1})
        self.assertEqual(
            [(issue["file"], issue["severity"]) for issue in report["issues"]],
            [("wiki/Invalid.md", "error"), ("wiki/Selected.md", "warning"),
             ("wiki/Selected.md", "error")],
        )

    def test_selected_directory_resolves_targets_outside_directory(self):
        self.page("wiki/chosen/First.md", "[[Existing]]")
        self.page("wiki/chosen/Second.md", "[[First]]")
        self.page("wiki/Existing.md")
        self.write("wiki/Invalid.md", "# Invalid\n")
        self.report("wiki/chosen", pages=2, issues=[])

    def test_absolute_selected_path_resolves_wiki_targets(self):
        selected = self.page("wiki/Selected.md", "[[Existing]]")
        self.page("wiki/Existing.md")
        self.report(selected, issues=[])

    def test_frontmatter_sources_resolve_unselected_targets(self):
        self.page("wiki/Selected.md", frontmatter='type: concept\nsources: ["[[Existing]]"]\n')
        self.page("wiki/Existing.md")
        self.report("wiki/Selected.md", issues=[])
        self.page("wiki/Selected.md", frontmatter='type: concept\nsources: ["[[existing]]"]\n')
        report = self.report("wiki/Selected.md", code=2)
        self.assertEqual(report["summary"], {"warning": 1})
        self.assertEqual(report["issues"][0]["msg"],
                         "case mismatch in sources: [[existing]] should be [[Existing]]")
        self.page("wiki/Selected.md", frontmatter='type: concept\nsources: ["[[Missing]]"]\n')
        report = self.report("wiki/Selected.md", code=1)
        self.assertEqual(report["issues"][0]["msg"], "broken wikilink in sources: [[Missing]]")

    def test_archive_pages_remain_targets_and_keep_h1_exemption(self):
        self.page("wiki/Selected.md", "[[2026-W01]] [[2026-01]] [[2026-01-01]]")
        self.page("wiki/log/2026-W01.md", title="log 2026-W01", frontmatter="type: log\n")
        self.page("wiki/log/2026-01.md", title="log 2026-01", frontmatter="type: log\n")
        self.page("wiki/distill/2026-01-01.md", title="Wiki distill (2026-01-01)")
        self.report("wiki/Selected.md", issues=[])
        self.report(pages=4, issues=[])

    def test_skipped_pages_are_not_targets_or_linted(self):
        note_dir = "wiki/sources/notes/2026/01/01/2026-01-01-fixture"
        self.page("wiki/Selected.md", "[[README]] [[Attachment]] [[2026-01-01-fixture]]")
        self.write("wiki/README.md", "not a wiki page\n")
        self.write(f"{note_dir}/Attachment.md", "not a wiki page\n")
        self.page(f"{note_dir}/2026-01-01-fixture.md", "[[Missing]]", frontmatter="type: source\n")
        for paths, count in [(("wiki/Selected.md",), 1), ((), 2)]:
            with self.subTest(paths=paths):
                report = self.report(*paths, code=1, pages=count)
                self.assertEqual(report["summary"], {"error": 2})
                self.assertEqual([issue["msg"] for issue in report["issues"]], [
                    "broken wikilink: [[README]]", "broken wikilink: [[Attachment]]",
                ])

    def test_explicit_pages_outside_wiki_remain_targets(self):
        self.page("drafts/First.md", "[[Second]] [[Existing]]")
        self.page("drafts/Second.md", "[[First]]")
        self.page("wiki/Existing.md")
        self.report("drafts/First.md", "drafts/Second.md", pages=2, issues=[])

    def test_text_and_quiet_modes_count_only_selected_pages(self):
        self.page("wiki/Selected.md", "[[Existing]]")
        self.page("wiki/Existing.md")
        result = self.cli("wiki/Selected.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "\n1 pages scanned, 0 errors, 0 warnings, 0 info\n")
        result = self.cli("--quiet", "wiki/Selected.md")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_empty_full_scan_keeps_empty_result(self):
        result = self.cli("--json")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, "", "no pages to lint\n"))

    def test_no_selected_pages_keep_empty_result(self):
        self.page("wiki/Existing.md")
        for args in [("--json", "wiki/Absent.md"), ("wiki/README.md",)]:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (0, "", "no pages to lint\n"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--wikilint", type=Path, default=WIKILINT)
    options, remaining = parser.parse_known_args()
    WIKILINT = options.wikilint.resolve()
    unittest.main(argv=[sys.argv[0], *remaining])

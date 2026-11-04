#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("blocked_audit", Path(__file__).with_name("audit-blocked-notes.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QueueAuditTests(unittest.TestCase):
    def test_normalized_duplicate_divergent_and_missing_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocked = root / "notes" / ".blocked"
            source = root / "wiki" / "sources" / "notes" / "2026" / "01" / "01"
            blocked.mkdir(parents=True); source.mkdir(parents=True)
            for slug in ("duplicate", "divergent", "missing"):
                folder = blocked / slug; folder.mkdir()
                (folder / "NOTE.md").write_text("---\ntopic: fixture\n---\n# Original title\n\nPRIVATE_FIXTURE body\n")
                (folder / "BLOCKED.md").write_text("reason: already-ingested")
                (folder / "attachment.log").write_bytes(b"fixture")
                if slug == "missing":
                    continue
                folder = source / slug; folder.mkdir()
                (folder / (slug + ".md")).write_text("---\ntype: source\n---\n# " + slug + "\n\nPRIVATE_FIXTURE body  \n")
                (folder / "attachment.log").write_bytes(b"fixture" if slug == "duplicate" else b"changed")
            before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            rows = list(module.audit(root))
            self.assertEqual({r["slug"]: r["classification"] for r in rows},
                             {"duplicate": "exact_duplicate", "divergent": "divergent", "missing": "missing"})
            self.assertNotIn("PRIVATE_FIXTURE", json.dumps(rows))
            self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_missing_roots_fail_instead_of_reporting_an_empty_healthy_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                list(module.audit(Path(tmp)))


if __name__ == "__main__":
    unittest.main()

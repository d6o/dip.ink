from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import unittest
from unittest import mock

from loops import restore_false_invalidations as repair

def rows():
    return [{"uuid": uuid, "source_uuid": "source", "target_uuid": "target", "properties": {
        "uuid": uuid, "fact": fact, "episodes": [repair.SOURCE_EPISODE],
        "valid_at": datetime.fromisoformat("2026-10-13T10:51:14+00:00"),
        "invalid_at": datetime.fromisoformat("2026-10-14T00:28:59+00:00"),
        "expired_at": datetime.fromisoformat(expired),
        "fact_embedding": [0.1, 0.2],
    }} for uuid, (fact, expired) in repair.FACTS.items()]


class RestorationTests(unittest.TestCase):
    def test_only_exact_source_supported_states_match(self):
        repair.validate(rows())
        for field, value in (("fact", "different"), ("episodes", []), ("invalid_at", None),
                             ("expired_at", None), ("valid_at", None)):
            with self.subTest(field=field):
                changed = rows()
                changed[0]["properties"][field] = value
                with self.assertRaises(RuntimeError):
                    repair.validate(changed)
        with self.assertRaises(RuntimeError):
            repair.validate(rows()[:1])

    def test_backup_preserves_prior_state_with_private_modes(self):
        path = repair.backup(rows())
        try:
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)
            data = json.loads(path.read_text())
            self.assertEqual(len(data["rows"]), 2)
            self.assertEqual(data["rows"][0]["properties"]["fact_embedding"], [0.1, 0.2])
            self.assertIn("invalid_at", data["rows"][0]["properties"])
            self.assertEqual(data["sources"], repair.SOURCE_NAMES)
        finally:
            shutil.rmtree(path.parent)

    def test_atomic_apply_only_changes_invalidation_dates_and_keeps_audit(self):
        tx = mock.Mock()
        tx.run.side_effect = [mock.Mock(single=mock.Mock(return_value=r)) for r in rows()] + [mock.Mock(single=mock.Mock(return_value={"uuid": r["uuid"]})) for r in rows()]
        result = repair.apply(tx, rows(), datetime.now(timezone.utc))
        self.assertEqual(set(result), set(repair.FACTS))
        self.assertEqual(tx.run.call_count, 4)
        self.assertEqual([c.args[0] for c in tx.run.call_args_list[:2]], [repair.LOCK, repair.LOCK])
        for call in tx.run.call_args_list[2:]:
            self.assertEqual(call.args[0], repair.APPLY)
            self.assertEqual(call.kwargs["properties"]["episodes"], [repair.SOURCE_EPISODE])
            self.assertIn("invalid_at", json.loads(call.kwargs["prior_state_json"])["properties"])
        self.assertIn("r.invalid_at = null, r.expired_at = null", repair.APPLY)
        self.assertNotIn("r.fact =", repair.APPLY.split("SET", 1)[1])
        self.assertNotIn("DELETE", repair.APPLY)

    def test_compare_and_set_failure_raises_for_transaction_rollback(self):
        tx = mock.Mock()
        tx.run.side_effect = [mock.Mock(single=mock.Mock(return_value=r)) for r in rows()] + [mock.Mock(single=mock.Mock(return_value={"uuid": rows()[0]["uuid"]})),
                              mock.Mock(single=mock.Mock(return_value=None))]
        with self.assertRaises(RuntimeError):
            repair.apply(tx, rows(), datetime.now(timezone.utc))

    def test_endpoint_and_full_property_drift_reject_before_any_mutation(self):
        for field, value in (("source_uuid", "other"), ("target_uuid", "other"),
                             ("properties", {**rows()[0]["properties"], "concurrent": True})):
            with self.subTest(field=field):
                changed = {**rows()[0], field: value}
                tx = mock.Mock()
                tx.run.return_value.single.return_value = changed
                with self.assertRaises(RuntimeError):
                    repair.apply(tx, rows(), datetime.now(timezone.utc))
                self.assertEqual(tx.run.call_count, 1)
                self.assertEqual(tx.run.call_args.args[0], repair.LOCK)

    def test_no_generic_ids_or_additional_mutations(self):
        changed = rows()
        changed[1]["uuid"] = "unselected"
        tx = mock.Mock()
        with self.assertRaises(RuntimeError):
            repair.apply(tx, changed, datetime.now(timezone.utc))
        tx.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

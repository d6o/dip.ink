#!/usr/bin/env python3
"""Static group drift and janitor activation regression checks."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("k8s_check", Path(__file__).with_name("check-k8s.py"))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
ROOT = Path(__file__).resolve().parents[1]


class OperationsContractTests(unittest.TestCase):
    def resources(self):
        return [*check.load_documents(ROOT / "deploy/k8s/config.yaml"),
                *check.load_documents(ROOT / "deploy/k8s/memory.yaml"),
                *check.load_documents(ROOT / "deploy/k8s/ingest-cronjob.yaml"),
                *check.load_documents(ROOT / "deploy/k8s/memory-loops.yaml")]

    def test_public_shared_config_is_consistent(self):
        check.validate_rendered(self.resources())

    def test_healthcheck_missing_or_overridden_group_is_rejected(self):
        for override in (False, True):
            resources = self.resources()
            job = next(r for r in resources if r["metadata"]["name"] == "memory-healthcheck")
            container = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
            if override:
                container.setdefault("env", []).append({"name": "GROUP_ID", "value": "wrong-fixture"})
            else:
                container["envFrom"] = []
            with self.assertRaises(ValueError):
                check.validate_rendered(resources)

    def test_unsuspended_janitor_is_rejected(self):
        resources = self.resources()
        job = next(r for r in resources if r["metadata"]["name"] == "contradiction-janitor")
        job["spec"]["suspend"] = False
        with self.assertRaises(ValueError):
            check.validate_rendered(resources)


if __name__ == "__main__":
    unittest.main()

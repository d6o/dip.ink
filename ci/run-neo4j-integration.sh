#!/usr/bin/env bash
# Real Neo4j 5.26.2 integration gate for CI.
# Expects a live bolt endpoint and the Lane A opt-in env NEO4J_INTEGRATION=1.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

mapfile -t integration_files < <(
  find server/tests -maxdepth 1 -type f -name 'test_*integration*.py' -print | sort
)
if [[ ${#integration_files[@]} -eq 0 ]]; then
  echo "::error::No server/tests/test_*integration*.py module exists; refusing a zero-test or mock-only integration success." >&2
  exit 1
fi

: "${NEO4J_URI:?NEO4J_URI is required}"
: "${NEO4J_USER:?NEO4J_USER is required}"
: "${NEO4J_PASSWORD:?NEO4J_PASSWORD is required}"

export NEO4J_INTEGRATION="${NEO4J_INTEGRATION:-1}"
export RUN_NEO4J_INTEGRATION="${RUN_NEO4J_INTEGRATION:-1}"
export DIPINK_RUN_NEO4J_INTEGRATION="${DIPINK_RUN_NEO4J_INTEGRATION:-1}"

python3 - <<'PY'
import os
from neo4j import GraphDatabase

uri = os.environ["NEO4J_URI"]
auth = (os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
with GraphDatabase.driver(uri, auth=auth) as driver:
    driver.verify_connectivity()
    with driver.session(database="neo4j") as session:
        assert session.run("RETURN 1 AS ready").single()["ready"] == 1
print(f"verified real Neo4j connectivity at {uri}")
PY

cd server
python3 - <<'PY'
import os
import sys
import unittest

if os.environ.get("NEO4J_INTEGRATION", "").lower() not in {"1", "true", "yes"}:
    print("NEO4J_INTEGRATION must be enabled for this job", file=sys.stderr)
    raise SystemExit(1)

loader = unittest.TestLoader()
suite = loader.discover("tests", pattern="test_*integration*.py")
count = suite.countTestCases()
if count < 1:
    print("No unittest cases discovered in test_*integration*.py", file=sys.stderr)
    raise SystemExit(1)
print(f"discovered {count} real-container integration test case(s)")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY

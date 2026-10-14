#!/usr/bin/env bash
# Do boundary tests with synthetic notes and disposable local Git remotes.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ENTRYPOINT="${PREPARED_TEST_ENTRYPOINT:-$ROOT/pi-runner/src/entrypoint.sh}"
GUARD="${PREPARED_TEST_GUARD:-$ROOT/scripts/processnotes-batch-guard.py}"
PREPARE="${PREPARED_TEST_PREPARE:-$ROOT/scripts/processnotes-prepare-inbox.sh}"
BLOCK="$ROOT/scripts/processnotes-block-note.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
[[ -f "$GUARD" ]] || { echo 'The guard interface is absent.' >&2; exit 2; }
mkdir -p "$TMP/bin"

cat > "$TMP/bin/curl" <<'SH'
#!/usr/bin/env bash
printf 'unexpected backend call\n' >> "$BACKEND_CALL_LOG"
exit 98
SH
cat > "$TMP/fake-event-logger" <<'SH'
#!/usr/bin/env bash
cat
SH
cat > "$TMP/fake-pi" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
count=$(cat "$FAKE_PI_COUNT" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$FAKE_PI_COUNT"
# Do not emit prompts, source bytes, tool arguments, or private state.
case "$FAKE_MODE:$count" in
  initial-restore-held:1) git restore -- "notes/$HELD_SLUG" ;;
  initial-restore-promoted:1) git restore -- "notes/.deferred/$PROMOTED_SLUG" ;;
  initial-new-live:1) mkdir notes/2026-01-02-000000-new ;;
  initial-state-tamper:1) printf ' ' >> "$FIXTURE_STATE" ;;
  repair-restore-held:2) git restore -- "notes/$HELD_SLUG" ;;
  allowed:1)
    for number in 1 2 3 4; do
      slug=$(printf '2026-01-01-0000%02d-fixture' "$number")
      destination="wiki/sources/notes/2026/01/01/$slug"
      mkdir -p "$(dirname "$destination")"
      mv "notes/$slug" "$destination"
      mv "$destination/NOTE.md" "$destination/$slug.md"
      python3 - "$destination/$slug.md" "$slug" <<'PYFORMAT'
import sys
from pathlib import Path
p=Path(sys.argv[1]); text=p.read_text()
p.write_text(text.replace("---\n","---\ntype: source\n",1).replace("# Fixture","# "+sys.argv[2],1))
PYFORMAT
    done
    ;;
  mixed:1)
    slug=2026-01-01-000001-fixture
    destination="wiki/sources/notes/2026/01/01/$slug"
    mkdir -p "$(dirname "$destination")"
    mv "notes/$slug" "$destination"
    mv "$destination/NOTE.md" "$destination/$slug.md"
    python3 - "$destination/$slug.md" "$slug" <<'PYFORMAT'
import sys
from pathlib import Path
p=Path(sys.argv[1]); text=p.read_text()
p.write_text(text.replace("---\n","---\ntype: source\n",1).replace("# Fixture","# "+sys.argv[2],1))
PYFORMAT
    NOTES_DIR=notes BLOCKED_AT=2026-01-02T03:04:05Z \
      bash "$FIXTURE_BLOCK" 2026-01-01-000002-fixture corrupt-input >/dev/null
    rm -rf notes/2026-01-01-000003-fixture
    # The fourth selected folder remains live without a canonical copy.
    ;;
  preexisting-block:1)
    NOTES_DIR=notes BLOCKED_AT=2026-01-02T03:04:05Z \
      bash "$FIXTURE_BLOCK" 2026-01-01-000001-fixture already-ingested >/dev/null
    ;;
  canonical-copy:1)
    slug=2026-01-01-000001-fixture
    destination="wiki/sources/notes/2026/01/01/$slug"
    mkdir -p "$(dirname "$destination")"
    cp -a "notes/$slug" "$destination"
    ;;
esac
[[ "$FAKE_MODE" == noop ]] || printf 'changed\n' > wiki/page.md
printf '{}\n'
SH
cat > "$TMP/fake-validator" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
count=$(cat "$FAKE_VALIDATOR_COUNT" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$FAKE_VALIDATOR_COUNT"
if [[ "$FAKE_MODE" == repair-restore-held && "$count" -eq 1 ]]; then
  echo 'Synthetic validator rejection.' >&2
  exit 1
fi
if [[ "$FAKE_MODE" == validator-restore-held || "$FAKE_MODE" == validator-fail-restore-held ]]; then
  git restore -- "notes/$HELD_SLUG"
  [[ "$FAKE_MODE" != validator-fail-restore-held ]] || exit 1
fi
SH
chmod +x "$TMP/bin/curl" "$TMP/fake-event-logger" "$TMP/fake-pi" "$TMP/fake-validator"

make_repo() {
  local name=$1 repo="$TMP/$1" origin="$TMP/$1-origin"
  git init -q --bare "$origin"
  git clone -q "$origin" "$repo"
  git -C "$repo" switch -q -c main
  git -C "$repo" config user.name fixture
  git -C "$repo" config user.email fixture@example.invalid
  mkdir -p "$repo/notes/.deferred" "$repo/notes/.blocked" "$repo/wiki" "$TMP/$name-home"
  printf 'original\n' > "$repo/wiki/page.md"
  printf 'Process only the prepared synthetic batch.\n' > "$repo/task.md"
  local number slug path
  for number in $(seq 1 13); do
    slug=$(printf '2026-01-01-0000%02d-fixture' "$number")
    path="$repo/notes/$slug"
    [[ "$number" -ne 1 ]] || path="$repo/notes/.deferred/$slug"
    mkdir -p "$path"
    cat > "$path/NOTE.md" <<'EOF'
---
captured: 2026-01-01T00:00:00Z
session: disposable fixture
topic: boundary fixture
---

# Fixture

SYNTHETIC-PRIVATE-PAYLOAD-DO-NOT-LOG
EOF
    printf '\000\001synthetic attachment\377' > "$path/attachment ü.bin"
  done
  git -C "$repo" add .
  git -C "$repo" commit -q -m 'Add synthetic notes'
  git -C "$repo" push -q -u origin main
  git -C "$origin" symbolic-ref HEAD refs/heads/main
  (
    cd "$repo"
    NOTES_DIR=notes INBOX_BATCH_SIZE=4 BLOCK_NOTE_BIN="$BLOCK" bash "$PREPARE" >/dev/null
    if [[ "$name" == preexisting-block ]]; then
      slug=2026-01-01-000001-fixture
      destination="wiki/sources/notes/2026/01/01/$slug"
      mkdir -p "$(dirname "$destination")"
      cp -a "notes/$slug" "$destination"
      mv "$destination/NOTE.md" "$destination/$slug.md"
    fi
    python3 "$GUARD" capture --notes-dir notes --cap 4 --state "$TMP/$name-state.json"
  ) > "$TMP/$name-metadata.json"
}

run_case() {
  local name=$1 mode=$2 expected=$3 expected_pi=$4 expected_validator=$5
  local repo="$TMP/$name" state="$TMP/$name-state.json" digest status=0
  make_repo "$name"
  digest=$(python3 - "$TMP/$name-metadata.json" <<'PY'
import json
import sys
metadata = json.load(open(sys.argv[1], encoding="utf-8"))
print(metadata.get("sha256", metadata.get("state_sha256", "")))
PY
)
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
  if [[ "$mode" == before-pi-state-tamper ]]; then printf ' ' >> "$state"; fi
  (
    cd "$repo"
    env -u PI_CODING_AGENT_DIR \
      HOME="$TMP/$name-home" PATH="$TMP/bin:$PATH" \
      PROMPT_PATH="$repo/task.md" PI_API_KEY=synthetic-unused \
      PI_BIN="$TMP/fake-pi" PI_EVENT_LOGGER="$TMP/fake-event-logger" \
      VALIDATOR="$TMP/fake-validator" WIKI_REPO_TOKEN=synthetic-unused \
      COMMIT_MESSAGE='Record the synthetic batch' \
      INBOX_PREPARED_STATE="$state" INBOX_PREPARED_SHA256="$digest" \
      INBOX_GUARD_BIN="$GUARD" \
      FAKE_MODE="$mode" FAKE_PI_COUNT="$TMP/$name-pi-count" \
      FAKE_VALIDATOR_COUNT="$TMP/$name-validator-count" \
      FIXTURE_STATE="$state" FIXTURE_BLOCK="$BLOCK" \
      HELD_SLUG=2026-01-01-000005-fixture \
      PROMOTED_SLUG=2026-01-01-000001-fixture \
      BACKEND_CALL_LOG="$TMP/$name-backend-calls" \
      "$ENTRYPOINT"
  ) > "$TMP/$name-output" 2>&1 || status=$?
  if [[ "$expected" == failure ]]; then
    [[ "$status" -ne 0 ]] || { cat "$TMP/$name-output"; echo "$name unexpectedly passed" >&2; exit 1; }
    [[ $(git -C "$repo" rev-list --count HEAD) -eq 1 ]]
    [[ $(git --git-dir="$TMP/$name-origin" rev-list --count main) -eq 1 ]]
    grep -Eiq 'prepared|boundary|batch guard' "$TMP/$name-output"
  else
    [[ "$status" -eq 0 ]] || { cat "$TMP/$name-output"; exit 1; }
    [[ $(git -C "$repo" rev-list --count HEAD) -eq 2 ]]
    [[ $(git --git-dir="$TMP/$name-origin" rev-list --count main) -eq 2 ]]
  fi
  [[ $(cat "$TMP/$name-pi-count" 2>/dev/null || echo 0) -eq "$expected_pi" ]]
  [[ $(cat "$TMP/$name-validator-count" 2>/dev/null || echo 0) -eq "$expected_validator" ]]
  [[ ! -e "$TMP/$name-backend-calls" ]]
  ! grep -Fq 'SYNTHETIC-PRIVATE-PAYLOAD-DO-NOT-LOG' "$TMP/$name-output"
  echo "$name OK"
}

run_case before-pi before-pi-state-tamper failure 0 0
run_case initial-held initial-restore-held failure 1 0
run_case initial-promoted initial-restore-promoted failure 1 0
run_case initial-extra initial-new-live failure 1 0
run_case initial-state initial-state-tamper failure 1 0
run_case canonical-copy canonical-copy failure 1 0
run_case repair-held repair-restore-held failure 2 1
run_case validator-held validator-restore-held failure 1 1
run_case validator-failed-held validator-fail-restore-held failure 1 1
run_case complete-moves allowed success 1 1
run_case mixed-outcomes mixed success 1 1
run_case selected-noop noop success 1 1
run_case preexisting-block preexisting-block success 1 1

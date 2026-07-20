#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/fake-runner" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
count=$(cat "$FAKE_RUNNER_COUNT" 2>/dev/null || echo 0)
if [[ "$count" -ge "${FAKE_SUCCESS_LIMIT:-999}" ]]; then exit 0; fi
count=$((count + 1)); echo "$count" > "$FAKE_RUNNER_COUNT"
for path in notes/*; do
  [[ -d "$path" ]] || continue
  case "$(basename "$path")" in
    .deferred|.blocked) continue ;;
  esac
  slug=$(basename "$path")
  if "$IS_INGESTED_BIN" "$slug" >/dev/null; then
    NOTES_DIR=notes BLOCKED_AT="${FAKE_BLOCKED_AT:-2026-01-02T03:04:05Z}" \
      "$BLOCK_NOTE_BIN" "$slug" already-ingested >/dev/null
  else
    rm -rf "$path"
  fi
done
git add -A
git commit -m "fake batch $count" >/dev/null
SH

cat > "$TMP/fake-probe" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$CURATOR_PROBE_URL" == "https://llm.example.com/v1" ]]
count=$(cat "$FAKE_PROBE_COUNT" 2>/dev/null || echo 0)
echo $((count + 1)) > "$FAKE_PROBE_COUNT"
# Per-model availability: FAKE_PROBE_AVAILABLE_MODEL, when set, is the only
# model that probes as available. Otherwise FAKE_PROBE_MODE drives all models.
if [[ -n "${FAKE_PROBE_AVAILABLE_MODEL:-}" ]]; then
  [[ "$CURATOR_PROBE_MODEL" == "$FAKE_PROBE_AVAILABLE_MODEL" ]] || exit 2
  exit 0
fi
[[ "$CURATOR_PROBE_MODEL" == "test-model" ]]
[[ "${FAKE_PROBE_MODE:-available}" == "available" ]] || exit 2
SH

cat > "$TMP/fake-now" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
index=$(cat "$FAKE_NOW_INDEX" 2>/dev/null || echo 1)
value=$(sed -n "${index}p" "$FAKE_NOW_VALUES")
[[ -n "$value" ]] || value=$(tail -1 "$FAKE_NOW_VALUES")
echo $((index + 1)) > "$FAKE_NOW_INDEX"
echo "$value"
SH
chmod +x "$TMP/fake-runner" "$TMP/fake-probe" "$TMP/fake-now"

make_repo() {
  local repo=$1 notes=$2
  git init -q -b main "$repo"
  git -C "$repo" config user.name test
  git -C "$repo" config user.email test@example.com
  mkdir -p "$repo/notes/.deferred" "$repo/notes/.blocked" "$repo/wiki/log"
  cat > "$repo/wiki/log.md" <<'EOF'
---
type: log
---

# log
EOF
  for ((i=1; i<=notes; i++)); do
    folder=$(printf '2026-01-01-0000%02d-note' "$i")
    if [[ "$i" -le 4 ]]; then dest="$repo/notes/$folder"; else dest="$repo/notes/.deferred/$folder"; fi
    mkdir -p "$dest"
    cat > "$dest/NOTE.md" <<EOF
---
captured: 2026-01-01T00:00:00Z
session: supervisor fixture
topic: test fixture
---

# $folder
EOF
  done
  git -C "$repo" add .
  git -C "$repo" commit -q -m seed
}

run_supervisor() {
  local repo=$1 env_file=$2
  shift 2
  (
    cd "$repo"
    env \
      CURATOR_PREFLIGHT_OK=1 \
      CURATOR_LLM_BASE_URL=https://llm.example.com/v1 CURATOR_LLM_MODEL=test-model \
      GITHUB_ENV="$env_file" SUPERVISOR_START_EPOCH=0 \
      CURATOR_RUNNER_BIN="$TMP/fake-runner" \
      INBOX_PREPARE_BIN="$ROOT/scripts/processnotes-prepare-inbox.sh" \
      CURATOR_PROBE_BIN="$TMP/fake-probe" SUPERVISOR_NOW_BIN="$TMP/fake-now" \
      BLOCK_NOTE_BIN="$ROOT/scripts/processnotes-block-note.sh" \
      IS_INGESTED_BIN="$ROOT/scripts/processnotes-is-ingested.py" \
      FAKE_RUNNER_COUNT="$TMP/runner-count" FAKE_PROBE_COUNT="$TMP/probe-count" \
      FAKE_NOW_INDEX="$TMP/now-index" \
      "$@" "$ROOT/scripts/processnotes-supervisor.sh"
  )
}

# Empty inbox must exit before any provider/preflight call.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/empty"; ENV_FILE="$TMP/empty-env"; make_repo "$REPO" 0
printf '0\n' > "$TMP/empty-times"
run_supervisor "$REPO" "$ENV_FILE" FAKE_SUCCESS_LIMIT=9 FAKE_NOW_VALUES="$TMP/empty-times"
grep -q '^BATCHES_COMPLETED=0$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=empty_inbox$' "$ENV_FILE"
grep -q '^SUPERVISOR_PROBES_RUN=0$' "$ENV_FILE"
[[ ! -f "$TMP/probe-count" ]]
[[ ! -f "$TMP/runner-count" ]]
echo 'empty-inbox zero-probe test OK'

# Native non-OpenAI providers skip OpenAI-compatible preflight by default.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/provider-aware"; ENV_FILE="$TMP/provider-aware-env"; make_repo "$REPO" 4
printf '0\n600\n' > "$TMP/provider-aware-times"
(
  cd "$REPO"
  env \
    CURATOR_PREFLIGHT= \
    CURATOR_PREFLIGHT_OK=0 \
    CURATOR_LLM_BASE_URL= \
    CURATOR_LLM_MODEL=test-model \
    PI_PROVIDER=anthropic \
    GITHUB_ENV="$ENV_FILE" SUPERVISOR_START_EPOCH=0 \
    CURATOR_RUNNER_BIN="$TMP/fake-runner" \
    INBOX_PREPARE_BIN="$ROOT/scripts/processnotes-prepare-inbox.sh" \
    CURATOR_PROBE_BIN="$TMP/fake-probe" SUPERVISOR_NOW_BIN="$TMP/fake-now" \
    BLOCK_NOTE_BIN="$ROOT/scripts/processnotes-block-note.sh" \
    IS_INGESTED_BIN="$ROOT/scripts/processnotes-is-ingested.py" \
    FAKE_RUNNER_COUNT="$TMP/runner-count" FAKE_PROBE_COUNT="$TMP/probe-count" \
    FAKE_NOW_INDEX="$TMP/now-index" \
    FAKE_SUCCESS_LIMIT=9 FAKE_NOW_VALUES="$TMP/provider-aware-times" \
    "$ROOT/scripts/processnotes-supervisor.sh"
)
grep -q '^BATCHES_COMPLETED=1$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=empty_inbox$' "$ENV_FILE"
grep -q '^SUPERVISOR_PROBES_RUN=0$' "$ENV_FILE"
[[ ! -f "$TMP/probe-count" ]]
echo 'provider-aware preflight skip test OK'

# Explicit CURATOR_PREFLIGHT=0 disables probes even for openai + probe bin.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/preflight-off"; ENV_FILE="$TMP/preflight-off-env"; make_repo "$REPO" 4
printf '0\n600\n' > "$TMP/preflight-off-times"
run_supervisor "$REPO" "$ENV_FILE" \
  CURATOR_PREFLIGHT=0 CURATOR_PREFLIGHT_OK=0 \
  FAKE_SUCCESS_LIMIT=9 FAKE_NOW_VALUES="$TMP/preflight-off-times"
grep -q '^BATCHES_COMPLETED=1$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=empty_inbox$' "$ENV_FILE"
grep -q '^SUPERVISOR_PROBES_RUN=0$' "$ENV_FILE"
[[ ! -f "$TMP/probe-count" ]]
echo 'preflight-off zero-probe test OK'

# Blocking preserves all existing source/attachment bytes and writes a bounded receipt.
BLOCK="$TMP/block"
slug=2026-01-01-000000-corrupt-note
mkdir -p "$BLOCK/notes/$slug"
printf 'original note\nline two\n' > "$BLOCK/notes/$slug/NOTE.md"
printf '\000\001\002attachment\377' > "$BLOCK/notes/$slug/artifact.bin"
cp "$BLOCK/notes/$slug/NOTE.md" "$TMP/original-note"
cp "$BLOCK/notes/$slug/artifact.bin" "$TMP/original-attachment"
NOTES_DIR="$BLOCK/notes" BLOCKED_AT=2026-01-02T03:04:05Z \
  "$ROOT/scripts/processnotes-block-note.sh" "$slug" corrupt-input >/dev/null
[[ ! -e "$BLOCK/notes/$slug" ]]
[[ -d "$BLOCK/notes/.blocked/$slug" ]]
cmp "$TMP/original-note" "$BLOCK/notes/.blocked/$slug/NOTE.md"
cmp "$TMP/original-attachment" "$BLOCK/notes/.blocked/$slug/artifact.bin"
grep -Fqx 'schema-version: 1' "$BLOCK/notes/.blocked/$slug/BLOCKED.md"
grep -Fqx "slug: \"$slug\"" "$BLOCK/notes/.blocked/$slug/BLOCKED.md"
grep -Fqx 'reason: "corrupt-input"' "$BLOCK/notes/.blocked/$slug/BLOCKED.md"
grep -Fqx 'blocked-at: "2026-01-02T03:04:05Z"' "$BLOCK/notes/.blocked/$slug/BLOCKED.md"
# Idempotent retry is a no-op.
NOTES_DIR="$BLOCK/notes" BLOCKED_AT=2026-01-02T03:04:05Z \
  "$ROOT/scripts/processnotes-block-note.sh" "$slug" corrupt-input >/dev/null
echo 'blocked receipt and byte preservation test OK'

# Exact ingest-log matching ignores ordinary log entries and similarly prefixed slugs.
DEDUP="$TMP/dedup"
mkdir -p "$DEDUP/wiki/log"
cat > "$DEDUP/wiki/log.md" <<'EOF'
---
type: log
---

# log

## [2026-01-02] note | unrelated mention
Mentioned 2026-01-01-000001-note outside an ingest entry.

## [2026-01-02] ingest | notes batch (1 note)

Processed: `2026-01-01-000001-note/`.
EOF
cat > "$DEDUP/wiki/log/2026-W01.md" <<'EOF'
---
type: log
---

# log 2026-W01

## [2026-01-01 00:00 UTC] auto-ingest | notes batch — 1 processed, 0 blocked, 0 dropped

Processed: `2026-01-01-000002-note/`.
EOF
(
  cd "$DEDUP"
  "$ROOT/scripts/processnotes-is-ingested.py" 2026-01-01-000001-note >/dev/null
  "$ROOT/scripts/processnotes-is-ingested.py" 2026-01-01-000002-note >/dev/null
  ! "$ROOT/scripts/processnotes-is-ingested.py" 2026-01-01-000001-note-extra >/dev/null
  ! "$ROOT/scripts/processnotes-is-ingested.py" 2026-01-01-000003-note >/dev/null
)
echo 'exact ingest-log dedup test OK'

# Inbox preparation keeps the oldest four live, wraps bare notes, and excludes blocked entries.
PREP="$TMP/prepare"
mkdir -p "$PREP/notes/.blocked/2025-12-31-235959-blocked"
echo blocked > "$PREP/notes/.blocked/2025-12-31-235959-blocked/NOTE.md"
for i in 6 2 5 1 4; do
  mkdir -p "$PREP/notes/2026-note-$i"
  cat > "$PREP/notes/2026-note-$i/NOTE.md" <<EOF
---
captured: 2026-01-01T00:00:00Z
session: prepare fixture
topic: test fixture
---

# 2026-note-$i
EOF
done
cat > "$PREP/notes/2026-note-3.md" <<'EOF'
---
captured: 2026-01-01T00:00:00Z
session: bare prepare fixture
topic: test fixture
---

# 2026-note-3
EOF
prepare_output=$(NOTES_DIR="$PREP/notes" INBOX_BATCH_SIZE=4 "$ROOT/scripts/processnotes-prepare-inbox.sh")
[[ $(find "$PREP/notes" -mindepth 1 -maxdepth 1 -type d ! -name .deferred ! -name .blocked | wc -l | tr -d ' ') -eq 4 ]]
[[ -f "$PREP/notes/2026-note-3/2026-note-3.md" || -f "$PREP/notes/.deferred/2026-note-3/2026-note-3.md" ]]
[[ $(find "$PREP/notes/.deferred" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') -eq 2 ]]
[[ -f "$PREP/notes/.blocked/2025-12-31-235959-blocked/NOTE.md" ]]
grep -q 'pool=6 live=4 held=2 blocked=1 cap=4' <<<"$prepare_output"
echo 'prepare inbox blocked-exclusion test OK'

# Malformed YAML is quarantined before provider use and valid neighbors refill
# the oldest-first batch instead of being poisoned by the bad note.
MALFORMED="$TMP/malformed-prepare"
mkdir -p "$MALFORMED/notes/.deferred"
for i in 2 3 4 5 6; do
  folder="2026-01-01-00000${i}-valid"
  dest="$MALFORMED/notes/$folder"
  [[ "$i" -le 4 ]] || dest="$MALFORMED/notes/.deferred/$folder"
  mkdir -p "$dest"
  cat > "$dest/NOTE.md" <<EOF
---
captured: 2026-01-01T00:00:00Z
session: valid neighbor $i
topic: malformed queue test
---

# valid $i
EOF
done
bad=2026-01-01-000001-malformed
mkdir -p "$MALFORMED/notes/$bad"
cat > "$MALFORMED/notes/$bad/NOTE.md" <<'EOF'
---
captured: 2026-01-01T00:00:00Z
session: invalid: unquoted colon poisons YAML
topic: malformed queue test
---

# malformed
EOF
malformed_output=$(NOTES_DIR="$MALFORMED/notes" INBOX_BATCH_SIZE=4 BLOCKED_AT=2026-01-02T03:04:05Z \
  "$ROOT/scripts/processnotes-prepare-inbox.sh" 2>&1)
[[ -d "$MALFORMED/notes/.blocked/$bad" ]]
grep -Fqx 'reason: "malformed-note"' "$MALFORMED/notes/.blocked/$bad/BLOCKED.md"
[[ $(find "$MALFORMED/notes" -mindepth 1 -maxdepth 1 -type d ! -name .deferred ! -name .blocked | wc -l | tr -d ' ') -eq 4 ]]
[[ $(find "$MALFORMED/notes/.deferred" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') -eq 1 ]]
grep -q "blocking malformed note: $bad" <<<"$malformed_output"
grep -q 'pool=5 live=4 held=1 blocked=1 cap=4' <<<"$malformed_output"
echo 'malformed frontmatter quarantine and queue progress test OK'

# A logged oldest duplicate becomes terminal, while later notes continue in a fresh batch.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/duplicate-progress"; ENV_FILE="$TMP/duplicate-progress-env"; make_repo "$REPO" 5
duplicate_slug=2026-01-01-000001-note
cat >> "$REPO/wiki/log.md" <<EOF

## [2026-01-02] ingest | notes batch (1 note)

Processed: \`$duplicate_slug/\`.
EOF
git -C "$REPO" add wiki/log.md
git -C "$REPO" commit -q -m 'record prior ingest'
printf '0\n600\n1200\n' > "$TMP/duplicate-progress-times"
run_supervisor "$REPO" "$ENV_FILE" FAKE_SUCCESS_LIMIT=9 FAKE_NOW_VALUES="$TMP/duplicate-progress-times"
grep -q '^BATCHES_COMPLETED=2$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=empty_inbox$' "$ENV_FILE"
[[ $(cat "$TMP/runner-count") -eq 2 ]]
[[ -f "$REPO/notes/.blocked/$duplicate_slug/NOTE.md" ]]
grep -Fqx 'reason: "already-ingested"' "$REPO/notes/.blocked/$duplicate_slug/BLOCKED.md"
[[ $(find "$REPO/notes" -mindepth 1 -maxdepth 1 -type d ! -name .deferred ! -name .blocked | wc -l | tr -d ' ') -eq 0 ]]
[[ $(find "$REPO/notes/.deferred" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') -eq 0 ]]
echo 'duplicate terminal handling and later-note progress test OK'

# Three independent commits; later batches probe the LLM before starting.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/multi"; ENV_FILE="$TMP/multi-env"; make_repo "$REPO" 12
printf '0\n600\n1200\n1800\n' > "$TMP/multi-times"
run_supervisor "$REPO" "$ENV_FILE" FAKE_SUCCESS_LIMIT=3 FAKE_NOW_VALUES="$TMP/multi-times"
grep -q '^BATCHES_COMPLETED=3$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=empty_inbox$' "$ENV_FILE"
[[ $(git -C "$REPO" rev-list --count HEAD) -eq 4 ]]
# Empty-inbox check runs before preflight, so only later non-empty batches probe
# after the first workflow-preflight reuse: batches 2 and 3 → 2 probes.
[[ $(cat "$TMP/probe-count") -eq 2 ]]
grep -q '^SUPERVISOR_PROBES_RUN=2$' "$ENV_FILE"
echo 'multi-batch and LLM probe test OK'

# Four 12-minute batches fit; a fifth is blocked by the 20-minute floor.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/four"; ENV_FILE="$TMP/four-env"; make_repo "$REPO" 20
printf '0\n720\n1440\n2160\n2880\n' > "$TMP/four-times"
run_supervisor "$REPO" "$ENV_FILE" FAKE_SUCCESS_LIMIT=9 FAKE_NOW_VALUES="$TMP/four-times"
grep -q '^BATCHES_COMPLETED=4$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=time_budget$' "$ENV_FILE"
echo 'four-batch adaptive budget test OK'

# Stable live backlog: a runner no-op stops immediately.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/noop"; ENV_FILE="$TMP/noop-env"; make_repo "$REPO" 4
printf '0\n' > "$TMP/noop-times"
run_supervisor "$REPO" "$ENV_FILE" FAKE_SUCCESS_LIMIT=0 FAKE_NOW_VALUES="$TMP/noop-times"
grep -q '^BATCHES_COMPLETED=0$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=no_head_advance$' "$ENV_FILE"
echo 'no-op stop test OK'

# LLM probe errors fail loudly after preserving the first committed batch.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/probe-error"; ENV_FILE="$TMP/probe-error-env"; make_repo "$REPO" 8
printf '0\n600\n' > "$TMP/probe-error-times"
set +e
run_supervisor "$REPO" "$ENV_FILE" FAKE_SUCCESS_LIMIT=9 FAKE_PROBE_MODE=error FAKE_NOW_VALUES="$TMP/probe-error-times"
probe_status=$?
set -e
[[ "$probe_status" -eq 2 ]]
grep -q '^BATCHES_COMPLETED=1$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=probe_error$' "$ENV_FILE"
echo 'LLM probe error propagation test OK'

# Do not start batch two when less than the 20-minute safety floor remains.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/budget"; ENV_FILE="$TMP/budget-env"; make_repo "$REPO" 8
printf '0\n2300\n' > "$TMP/budget-times"
run_supervisor "$REPO" "$ENV_FILE" FAKE_SUCCESS_LIMIT=9 FAKE_NOW_VALUES="$TMP/budget-times"
grep -q '^BATCHES_COMPLETED=1$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=time_budget$' "$ENV_FILE"
echo 'time-budget stop test OK'

# Model fallback: primary probe fails, fallback probes available, batch runs
# with PI_MODEL switched to the fallback.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/fallback"; ENV_FILE="$TMP/fallback-env"; make_repo "$REPO" 4
printf '0\n600\n' > "$TMP/fallback-times"
cat > "$TMP/model-recorder" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "$PI_MODEL" >> "$MODEL_LOG"
exec "$REAL_RUNNER"
SH
chmod +x "$TMP/model-recorder"
rm -f "$TMP/model-log"
(
  cd "$REPO"
  env \
    CURATOR_PREFLIGHT=1 CURATOR_PREFLIGHT_OK=0 \
    CURATOR_LLM_BASE_URL=https://llm.example.com/v1 CURATOR_LLM_MODEL=test-model \
    CURATOR_MODEL_FALLBACKS="fallback-model" \
    FAKE_PROBE_AVAILABLE_MODEL=fallback-model \
    GITHUB_ENV="$ENV_FILE" SUPERVISOR_START_EPOCH=0 \
    CURATOR_RUNNER_BIN="$TMP/model-recorder" \
    REAL_RUNNER="$TMP/fake-runner" MODEL_LOG="$TMP/model-log" \
    INBOX_PREPARE_BIN="$ROOT/scripts/processnotes-prepare-inbox.sh" \
    CURATOR_PROBE_BIN="$TMP/fake-probe" SUPERVISOR_NOW_BIN="$TMP/fake-now" \
    BLOCK_NOTE_BIN="$ROOT/scripts/processnotes-block-note.sh" \
    IS_INGESTED_BIN="$ROOT/scripts/processnotes-is-ingested.py" \
    FAKE_RUNNER_COUNT="$TMP/runner-count" FAKE_PROBE_COUNT="$TMP/probe-count" \
    FAKE_NOW_INDEX="$TMP/now-index" \
    FAKE_SUCCESS_LIMIT=9 FAKE_NOW_VALUES="$TMP/fallback-times" \
    "$ROOT/scripts/processnotes-supervisor.sh"
)
grep -q '^BATCHES_COMPLETED=1$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=empty_inbox$' "$ENV_FILE"
grep -Fqx 'fallback-model' "$TMP/model-log"
# Primary + fallback probed on batch 1.
[[ "$(cat "$TMP/probe-count")" -ge 2 ]]
echo 'model fallback test OK'

# Model fallback exhausted: all candidates fail, run exits 2 with no batches.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/fallback-exhausted"; ENV_FILE="$TMP/fallback-exhausted-env"; make_repo "$REPO" 4
printf '0\n' > "$TMP/fallback-exhausted-times"
set +e
run_supervisor "$REPO" "$ENV_FILE" \
  CURATOR_PREFLIGHT=1 CURATOR_PREFLIGHT_OK=0 \
  CURATOR_MODEL_FALLBACKS="other-model" FAKE_PROBE_MODE=error \
  FAKE_SUCCESS_LIMIT=9 FAKE_NOW_VALUES="$TMP/fallback-exhausted-times"
exhausted_status=$?
set -e
[[ "$exhausted_status" -eq 2 ]]
# Batch-1 preflight failure exits before any runner invocation.
[[ ! -f "$TMP/runner-count" ]]
# Primary + fallback were both probed.
[[ "$(cat "$TMP/probe-count")" -eq 2 ]]
echo 'model fallback exhausted test OK'

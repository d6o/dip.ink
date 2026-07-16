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
  [[ "$(basename "$path")" == ".deferred" ]] && continue
  rm -rf "$path"
done
git add -A
git commit -m "fake batch $count" >/dev/null
SH

cat > "$TMP/fake-probe" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$CURATOR_PROBE_URL" == "https://llm.example.com/v1" ]]
[[ "$CURATOR_PROBE_MODEL" == "test-model" ]]
count=$(cat "$FAKE_PROBE_COUNT" 2>/dev/null || echo 0)
echo $((count + 1)) > "$FAKE_PROBE_COUNT"
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
  mkdir -p "$repo/notes/.deferred"
  for ((i=1; i<=notes; i++)); do
    folder=$(printf '2026-01-01-0000%02d-note' "$i")
    if [[ "$i" -le 4 ]]; then dest="$repo/notes/$folder"; else dest="$repo/notes/.deferred/$folder"; fi
    mkdir -p "$dest"; echo "$folder" > "$dest/NOTE.md"
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
      FAKE_RUNNER_COUNT="$TMP/runner-count" FAKE_PROBE_COUNT="$TMP/probe-count" \
      FAKE_NOW_INDEX="$TMP/now-index" \
      "$@" "$ROOT/scripts/processnotes-supervisor.sh"
  )
}

# Inbox preparation keeps the oldest four live and wraps bare notes.
PREP="$TMP/prepare"
mkdir -p "$PREP/notes"
for i in 6 2 5 1 4; do mkdir -p "$PREP/notes/2026-note-$i"; done
echo bare > "$PREP/notes/2026-note-3.md"
NOTES_DIR="$PREP/notes" INBOX_BATCH_SIZE=4 "$ROOT/scripts/processnotes-prepare-inbox.sh" >/dev/null
[[ $(find "$PREP/notes" -mindepth 1 -maxdepth 1 -type d ! -name .deferred | wc -l | tr -d ' ') -eq 4 ]]
[[ -f "$PREP/notes/2026-note-3/2026-note-3.md" ]]
[[ $(find "$PREP/notes/.deferred" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') -eq 2 ]]
echo 'prepare inbox test OK'

# Three independent commits; later batches probe the LLM before starting.
rm -f "$TMP/runner-count" "$TMP/probe-count" "$TMP/now-index"
REPO="$TMP/multi"; ENV_FILE="$TMP/multi-env"; make_repo "$REPO" 12
printf '0\n600\n1200\n1800\n' > "$TMP/multi-times"
run_supervisor "$REPO" "$ENV_FILE" FAKE_SUCCESS_LIMIT=3 FAKE_NOW_VALUES="$TMP/multi-times"
grep -q '^BATCHES_COMPLETED=3$' "$ENV_FILE"
grep -q '^SUPERVISOR_STOP_REASON=empty_inbox$' "$ENV_FILE"
[[ $(git -C "$REPO" rev-list --count HEAD) -eq 4 ]]
[[ $(cat "$TMP/probe-count") -eq 3 ]]
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

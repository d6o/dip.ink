#!/usr/bin/env bash
# Hourly curator supervisor: repeatedly exposes the oldest N inbox notes and
# launches an independent fresh agent session (pi-runner by default) for each
# batch. Each successful sub-batch validates, commits, rebases, and pushes
# before the next fresh session starts, so later failures cannot discard
# earlier progress. Stops on: time budget, empty inbox, a no-commit batch, or
# an LLM-endpoint probe failure.
set -euo pipefail

log() { echo "[curator-supervisor] $*" >&2; }

BUDGET_SECONDS="${SUPERVISOR_BUDGET_SECONDS:-3480}"
MIN_REMAINING="${SUPERVISOR_MIN_REMAINING_SECONDS:-1200}"
START_EPOCH="${SUPERVISOR_START_EPOCH:-$(date +%s)}"
RUNNER_BIN="${CURATOR_RUNNER_BIN:-/opt/pi-runner/entrypoint.sh}"
PREPARE_BIN="${INBOX_PREPARE_BIN:-scripts/processnotes-prepare-inbox.sh}"
PROBE_BIN="${CURATOR_PROBE_BIN:-}"
NOW_BIN="${SUPERVISOR_NOW_BIN:-}"
GITHUB_ENV_FILE="${GITHUB_ENV:-/dev/null}"
# OpenAI-compatible endpoint the curator's model is served from. Probed with a
# 1-token completion before every batch after the first so a dead/quota'd
# provider fails fast instead of burning the whole budget.
CURATOR_LLM_BASE_URL="${CURATOR_LLM_BASE_URL:-https://api.openai.com/v1}"
CURATOR_LLM_MODEL="${CURATOR_LLM_MODEL:-gpt-4.1-mini}"
CURATOR_LLM_API_KEY="${CURATOR_LLM_API_KEY:-}"

[[ "$BUDGET_SECONDS" =~ ^[0-9]+$ ]] || { log "invalid budget: $BUDGET_SECONDS"; exit 2; }
[[ "$MIN_REMAINING" =~ ^[0-9]+$ ]] || { log "invalid minimum remaining time: $MIN_REMAINING"; exit 2; }
[[ -x "$RUNNER_BIN" ]] || { log "runner is not executable: $RUNNER_BIN"; exit 2; }
[[ -x "$PREPARE_BIN" ]] || { log "inbox preparer is not executable: $PREPARE_BIN"; exit 2; }

git config --global --add safe.directory "$PWD"
git rev-parse --is-inside-work-tree >/dev/null

batches_completed=0
stop_reason="unknown"

write_summary() {
  {
    echo "BATCHES_COMPLETED=$batches_completed"
    echo "SUPERVISOR_STOP_REASON=$stop_reason"
  } >> "$GITHUB_ENV_FILE"
  log "summary batches_completed=$batches_completed stop_reason=$stop_reason"
}

now_epoch() {
  if [[ -n "$NOW_BIN" ]]; then "$NOW_BIN"; else date +%s; fi
}

probe_llm() {
  local status
  if [[ -n "$PROBE_BIN" ]]; then
    CURATOR_PROBE_URL="$CURATOR_LLM_BASE_URL" CURATOR_PROBE_MODEL="$CURATOR_LLM_MODEL" "$PROBE_BIN"
    return $?
  fi
  local auth_args=()
  if [[ -n "$CURATOR_LLM_API_KEY" ]]; then
    auth_args=(-H "Authorization: Bearer $CURATOR_LLM_API_KEY")
  fi
  if ! status=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 \
    "$CURATOR_LLM_BASE_URL/chat/completions" \
    -H "Content-Type: application/json" \
    "${auth_args[@]}" \
    -d "{\"model\":\"$CURATOR_LLM_MODEL\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\".\"}]}"); then
    log "error: LLM endpoint probe failed at the network/curl layer"
    return 2
  fi
  case "$status" in
    2[0-9][0-9]) log "LLM model=$CURATOR_LLM_MODEL available (HTTP $status)"; return 0 ;;
    *) log "error: LLM model=$CURATOR_LLM_MODEL probe returned HTTP $status"; return 2 ;;
  esac
}

live_inbox_count() {
  local count=0 path
  for path in notes/*; do
    [[ -d "$path" ]] || continue
    case "$(basename "$path")" in
      .deferred|.blocked) continue ;;
    esac
    count=$((count + 1))
  done
  echo "$count"
}

first_probe=1
while true; do
  now=$(now_epoch)
  elapsed=$((now - START_EPOCH))
  remaining=$((BUDGET_SECONDS - elapsed))
  if [[ "$remaining" -lt "$MIN_REMAINING" ]]; then
    stop_reason=time_budget
    write_summary
    exit 0
  fi

  if [[ "$first_probe" -eq 1 ]]; then
    first_probe=0
    if [[ "${CURATOR_PREFLIGHT_OK:-0}" != "1" ]]; then
      # No workflow-level preflight — probe here instead of trusting the env.
      if ! probe_llm; then
        log "error: LLM preflight probe failed"
        exit 2
      fi
    else
      log "batch 1 reuses workflow preflight"
    fi
  elif ! probe_llm; then
    stop_reason=probe_error
    write_summary
    exit 2
  fi

  batch_number=$((batches_completed + 1))
  log "preparing batch=$batch_number remaining=${remaining}s model=$CURATOR_LLM_MODEL"
  "$PREPARE_BIN"
  if [[ "$(live_inbox_count)" -eq 0 ]]; then
    stop_reason=empty_inbox
    write_summary
    exit 0
  fi

  before_sha=$(git rev-parse HEAD)

  set +e
  "$RUNNER_BIN"
  runner_result=$?
  set -e
  if [[ "$runner_result" -ne 0 ]]; then
    stop_reason=runner_error
    write_summary
    exit "$runner_result"
  fi

  after_sha=$(git rev-parse HEAD)
  if [[ "$before_sha" == "$after_sha" ]]; then
    stop_reason=no_head_advance
    write_summary
    exit 0
  fi

  batches_completed=$((batches_completed + 1))
  log "batch=$batch_number committed HEAD=$after_sha"
done

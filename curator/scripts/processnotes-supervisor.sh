#!/usr/bin/env bash
# Hourly curator supervisor: repeatedly exposes the oldest N inbox notes and
# launches an independent fresh agent session (pi-runner by default) for each
# batch. Each successful sub-batch validates, commits, rebases, and pushes
# before the next fresh session starts, so later failures cannot discard
# earlier progress. Stops on: time budget, empty inbox, a no-commit batch, or
# an LLM-endpoint probe failure.
#
# Preflight is optional and provider-aware:
# - Empty inbox runs exit before any provider call (zero LLM probes).
# - Native non-OpenAI Pi providers skip the OpenAI-compatible HTTP preflight
#   unless CURATOR_PREFLIGHT=1 or CURATOR_LLM_BASE_URL is explicitly set.
# - CURATOR_PREFLIGHT=0 disables preflight entirely.
set -euo pipefail

log() { echo "[curator-supervisor] $*" >&2; }

BUDGET_SECONDS="${SUPERVISOR_BUDGET_SECONDS:-3480}"
MIN_REMAINING="${SUPERVISOR_MIN_REMAINING_SECONDS:-1200}"
START_EPOCH="${SUPERVISOR_START_EPOCH:-$(date +%s)}"
RUNNER_BIN="${CURATOR_RUNNER_BIN:-/opt/pi-runner/entrypoint.sh}"
# Default to the sibling script so the supervisor works both baked into the
# pi-runner image (/opt/dip.ink/scripts) and from a dip.ink checkout.
SUPERVISOR_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PREPARE_BIN="${INBOX_PREPARE_BIN:-$SUPERVISOR_DIR/processnotes-prepare-inbox.sh}"
PROBE_BIN="${CURATOR_PROBE_BIN:-}"
NOW_BIN="${SUPERVISOR_NOW_BIN:-}"
GITHUB_ENV_FILE="${GITHUB_ENV:-/dev/null}"
# OpenAI-compatible endpoint used only when preflight is enabled. Empty means
# "no OpenAI-compatible endpoint configured".
CURATOR_LLM_BASE_URL="${CURATOR_LLM_BASE_URL:-}"
CURATOR_LLM_MODEL="${CURATOR_LLM_MODEL:-gpt-4.1-mini}"
CURATOR_LLM_API_KEY="${CURATOR_LLM_API_KEY:-}"
# Optional comma-separated fallback models probed in order when the primary
# model's preflight fails (e.g. quota exhaustion on one provider family).
# Every candidate must exist in the runner's model config (PI_MODELS_JSON).
# Only meaningful when preflight is enabled; with preflight off the primary
# model is used unconditionally.
CURATOR_MODEL_FALLBACKS="${CURATOR_MODEL_FALLBACKS:-}"
PI_PROVIDER="${PI_PROVIDER:-openai}"
# CURATOR_PREFLIGHT: unset = provider-aware default, 0/false/no = off, 1/true/yes = on.
CURATOR_PREFLIGHT="${CURATOR_PREFLIGHT:-}"

[[ "$BUDGET_SECONDS" =~ ^[0-9]+$ ]] || { log "invalid budget: $BUDGET_SECONDS"; exit 2; }
[[ "$MIN_REMAINING" =~ ^[0-9]+$ ]] || { log "invalid minimum remaining time: $MIN_REMAINING"; exit 2; }
[[ -x "$RUNNER_BIN" ]] || { log "runner is not executable: $RUNNER_BIN"; exit 2; }
[[ -x "$PREPARE_BIN" ]] || { log "inbox preparer is not executable: $PREPARE_BIN"; exit 2; }

git config --global --add safe.directory "$PWD"
git rev-parse --is-inside-work-tree >/dev/null

batches_completed=0
stop_reason="unknown"
probes_run=0
# The configured primary model; CURATOR_LLM_MODEL tracks the currently
# selected candidate and may point at a fallback after a failed probe.
PRIMARY_LLM_MODEL="$CURATOR_LLM_MODEL"

write_summary() {
  {
    echo "BATCHES_COMPLETED=$batches_completed"
    echo "SUPERVISOR_STOP_REASON=$stop_reason"
    echo "SUPERVISOR_PROBES_RUN=$probes_run"
  } >> "$GITHUB_ENV_FILE"
  log "summary batches_completed=$batches_completed stop_reason=$stop_reason probes_run=$probes_run"
}

now_epoch() {
  if [[ -n "$NOW_BIN" ]]; then "$NOW_BIN"; else date +%s; fi
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

# Decide whether an OpenAI-compatible HTTP preflight should run for this batch.
# Returns 0 when preflight is required, 1 when it should be skipped.
# $1 = batch_number (1-based).
preflight_enabled() {
  local batch_number=$1
  local flag
  flag=$(printf '%s' "${CURATOR_PREFLIGHT:-}" | tr '[:upper:]' '[:lower:]')
  case "$flag" in
    0|false|no|off) return 1 ;;
    1|true|yes|on) return 0 ;;
  esac
  # Workflow-level preflight already succeeded: skip only the first probe.
  if [[ "${CURATOR_PREFLIGHT_OK:-0}" == "1" && "$batch_number" -eq 1 ]]; then
    return 1
  fi
  # Explicit OpenAI-compatible base URL opts into preflight for any provider.
  if [[ -n "$CURATOR_LLM_BASE_URL" ]]; then
    return 0
  fi
  # Default: only openai (and empty/default) providers use the HTTP preflight.
  # Native non-OpenAI providers (anthropic, custom PI_MODELS_JSON, etc.) skip
  # unless CURATOR_PREFLIGHT=1 or CURATOR_LLM_BASE_URL is set. PROBE_BIN only
  # controls how probes run when preflight is enabled, not whether they run.
  case "$(printf '%s' "$PI_PROVIDER" | tr '[:upper:]' '[:lower:]')" in
    openai|"") return 0 ;;
    *) return 1 ;;
  esac
}

probe_one_model() {
  local model=$1 status base
  probes_run=$((probes_run + 1))
  if [[ -n "$PROBE_BIN" ]]; then
    CURATOR_PROBE_URL="${CURATOR_LLM_BASE_URL:-}" CURATOR_PROBE_MODEL="$model" "$PROBE_BIN"
    return $?
  fi
  base="${CURATOR_LLM_BASE_URL:-https://api.openai.com/v1}"
  local auth_args=()
  if [[ -n "$CURATOR_LLM_API_KEY" ]]; then
    auth_args=(-H "Authorization: Bearer $CURATOR_LLM_API_KEY")
  fi
  if ! status=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 \
    "$base/chat/completions" \
    -H "Content-Type: application/json" \
    "${auth_args[@]}" \
    -d "{\"model\":\"$model\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\".\"}]}"); then
    log "error: LLM model=$model endpoint probe failed at the network/curl layer"
    return 2
  fi
  case "$status" in
    2[0-9][0-9]) log "LLM model=$model available (HTTP $status)"; return 0 ;;
    *) log "error: LLM model=$model probe returned HTTP $status"; return 2 ;;
  esac
}

# Probe the primary model, then each fallback, in order. On success the
# selected model is exported as PI_MODEL + CURATOR_LLM_MODEL so the runner
# uses it for this and later batches (a recovered primary is re-preferred on
# the next batch because candidates always start from the configured primary).
probe_llm() {
  local candidate
  local candidates="$PRIMARY_LLM_MODEL"
  if [[ -n "$CURATOR_MODEL_FALLBACKS" ]]; then
    candidates="$candidates,$CURATOR_MODEL_FALLBACKS"
  fi
  IFS=',' read -ra candidate_list <<< "$candidates"
  for candidate in "${candidate_list[@]}"; do
    candidate=$(printf '%s' "$candidate" | tr -d '[:space:]')
    [[ -n "$candidate" ]] || continue
    if probe_one_model "$candidate"; then
      if [[ "$candidate" != "$CURATOR_LLM_MODEL" ]]; then
        log "switching curator model: $CURATOR_LLM_MODEL -> $candidate"
      fi
      CURATOR_LLM_MODEL="$candidate"
      export PI_MODEL="$candidate"
      return 0
    fi
  done
  return 2
}

maybe_probe() {
  local batch_number=$1
  if ! preflight_enabled "$batch_number"; then
    if [[ "${CURATOR_PREFLIGHT_OK:-0}" == "1" && "$batch_number" -eq 1 ]]; then
      log "batch 1 reuses workflow preflight"
    else
      log "batch $batch_number skips LLM preflight (provider=$PI_PROVIDER preflight=${CURATOR_PREFLIGHT:-auto})"
    fi
    return 0
  fi
  if ! probe_llm; then
    if [[ "$batch_number" -eq 1 && "$batches_completed" -eq 0 ]]; then
      log "error: LLM preflight probe failed"
      exit 2
    fi
    stop_reason=probe_error
    write_summary
    exit 2
  fi
}

while true; do
  now=$(now_epoch)
  elapsed=$((now - START_EPOCH))
  remaining=$((BUDGET_SECONDS - elapsed))
  if [[ "$remaining" -lt "$MIN_REMAINING" ]]; then
    stop_reason=time_budget
    write_summary
    exit 0
  fi

  batch_number=$((batches_completed + 1))
  log "preparing batch=$batch_number remaining=${remaining}s model=$CURATOR_LLM_MODEL provider=$PI_PROVIDER"

  # Always prepare/check the inbox BEFORE any provider call so empty runs
  # make zero LLM probes.
  "$PREPARE_BIN"
  if [[ "$(live_inbox_count)" -eq 0 ]]; then
    stop_reason=empty_inbox
    write_summary
    exit 0
  fi

  maybe_probe "$batch_number"

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

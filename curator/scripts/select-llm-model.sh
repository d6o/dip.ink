#!/usr/bin/env bash
# Print the first available model from a candidate list.
#
# Probes an OpenAI-compatible endpoint with a 1-token chat completion per
# candidate and prints the first model whose probe returns HTTP 2xx. Used by
# single-shot curator workflows (reviewqueue, synthesis) to pick a live model
# before invoking the runner; the batch supervisor has the same logic built in
# via CURATOR_MODEL_FALLBACKS.
#
# Env:
#   LLM_BASE_URL   required, e.g. https://api.openai.com/v1
#   LLM_MODELS     required, comma-separated candidates in preference order
#   LLM_API_KEY    optional bearer token
#
# Exit 0 with the selected model on stdout, exit 2 when no candidate is
# available. Diagnostics go to stderr.
set -euo pipefail

BASE_URL="${LLM_BASE_URL:?LLM_BASE_URL is required}"
MODELS="${LLM_MODELS:?LLM_MODELS is required (comma-separated, preference order)}"

auth_args=()
if [[ -n "${LLM_API_KEY:-}" ]]; then
  auth_args=(-H "Authorization: Bearer $LLM_API_KEY")
fi

IFS=',' read -ra candidates <<< "$MODELS"
for model in "${candidates[@]}"; do
  model="$(echo "$model" | tr -d '[:space:]')"
  [[ -n "$model" ]] || continue
  if ! status=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 \
    "$BASE_URL/chat/completions" \
    -H "Content-Type: application/json" \
    "${auth_args[@]}" \
    -d "{\"model\":\"$model\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\".\"}]}"); then
    echo "[select-llm-model] $model: probe failed at the network/curl layer" >&2
    continue
  fi
  case "$status" in
    2[0-9][0-9])
      echo "[select-llm-model] selected $model (HTTP $status)" >&2
      echo "$model"
      exit 0
      ;;
    *)
      echo "[select-llm-model] $model unavailable (HTTP $status)" >&2
      ;;
  esac
done

echo "[select-llm-model] no candidate model available: $MODELS" >&2
exit 2

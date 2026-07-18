#!/usr/bin/env bash
# Move one live inbox folder into the terminal blocked queue and add a bounded,
# machine-readable receipt. Existing note and attachment bytes are never edited.
set -euo pipefail

usage() {
  echo "usage: $0 <note-slug> <reason-code>" >&2
  echo "reason-code: already-ingested | corrupt-input | unparseable-input | malformed-note | needs-operator-review" >&2
}

NOTES_DIR="${NOTES_DIR:-notes}"
slug="${1:-}"
reason="${2:-}"

if [[ -z "$slug" || -z "$reason" || $# -ne 2 ]]; then
  usage
  exit 2
fi
if [[ ! "$slug" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$ ]]; then
  echo "invalid note slug: $slug" >&2
  exit 2
fi
case "$reason" in
  already-ingested|corrupt-input|unparseable-input|malformed-note|needs-operator-review) ;;
  *) echo "invalid blocked reason: $reason" >&2; usage; exit 2 ;;
esac

blocked_at="${BLOCKED_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
if [[ ! "$blocked_at" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
  echo "invalid BLOCKED_AT timestamp: $blocked_at" >&2
  exit 2
fi

source_dir="$NOTES_DIR/$slug"
blocked_root="$NOTES_DIR/.blocked"
destination="$blocked_root/$slug"
receipt_name="BLOCKED.md"

mkdir -p "$blocked_root"

# Idempotent retry after a successful move.
if [[ ! -d "$source_dir" && -d "$destination" ]]; then
  if [[ -f "$destination/$receipt_name" ]] \
    && grep -Fqx "slug: \"$slug\"" "$destination/$receipt_name" \
    && grep -Fqx "reason: \"$reason\"" "$destination/$receipt_name"; then
    echo "already blocked: $slug reason=$reason"
    exit 0
  fi
  echo "blocked destination already exists with a different or missing receipt: $destination" >&2
  exit 3
fi

if [[ ! -d "$source_dir" ]]; then
  echo "live note folder not found: $source_dir" >&2
  exit 3
fi
if [[ -e "$destination" ]]; then
  echo "blocked destination already exists: $destination" >&2
  exit 3
fi
if [[ -e "$source_dir/$receipt_name" ]]; then
  echo "live note already contains reserved receipt name: $source_dir/$receipt_name" >&2
  exit 3
fi

receipt_tmp=$(mktemp "$blocked_root/.blocked-receipt.XXXXXX")
cleanup() { rm -f "$receipt_tmp"; }
trap cleanup EXIT

python3 - "$receipt_tmp" "$slug" "$reason" "$blocked_at" <<'PY'
import json
import sys
from pathlib import Path

path, slug, reason, blocked_at = sys.argv[1:]
receipt = "\n".join(
    [
        "---",
        "schema-version: 1",
        f"slug: {json.dumps(slug)}",
        f"reason: {json.dumps(reason)}",
        f"blocked-at: {json.dumps(blocked_at)}",
        'source-path: ' + json.dumps(f"notes/{slug}"),
        "---",
        "",
        "# Blocked note receipt",
        "",
        "This note folder is terminal and excluded from normal and deferred curator batching.",
        "The receipt was added without modifying any pre-existing note or attachment file.",
        "",
    ]
)
Path(path).write_text(receipt, encoding="utf-8")
PY

mv -- "$source_dir" "$destination"
if ! mv -- "$receipt_tmp" "$destination/$receipt_name"; then
  mv -- "$destination" "$source_dir" || true
  echo "failed to install blocked receipt; restored live folder when possible" >&2
  exit 3
fi
trap - EXIT

echo "blocked: $slug reason=$reason"

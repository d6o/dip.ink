#!/usr/bin/env python3
"""PreCompact hook: block compaction until this session's learnings are saved.

Compaction is where memory dies — everything the session learned but didn't
write down is lost. This hook refuses the compact unless /recordnotes ran for
this working directory in the last 30 minutes (the skill writes an ack marker
after its review). Running /recordnotes with nothing to save still writes the
marker, so the cost of a false alarm is one short review.

Install (see INSTALL_FOR_AGENTS.md): copy to ~/.claude/hooks/ and register
under hooks.PreCompact in ~/.claude/settings.json.
"""
import hashlib
import json
import os
import pathlib
import sys
import time

TTL_SECONDS = 30 * 60

cwd = os.getcwd()
ack_dir = pathlib.Path.home() / '.claude' / 'hooks' / 'recordnotes-acks'
key = hashlib.sha256(cwd.encode('utf-8')).hexdigest()
ack_path = ack_dir / f'{key}.json'

fresh = False
reviewed_at = None
if ack_path.exists():
    try:
        data = json.loads(ack_path.read_text())
        ts = float(data.get('ts', 0))
        reviewed_at = data.get('reviewed_at')
        fresh = time.time() - ts <= TTL_SECONDS and data.get('cwd') == cwd
    except Exception:
        fresh = False

if fresh:
    print(json.dumps({
        'continue': True,
        'suppressOutput': True,
    }))
    sys.exit(0)

msg = (
    'Before compacting, run /recordnotes if this session learned anything worth keeping. '
    'It saves notes to the memory via wiki_note_drop and then acknowledges compact for 30 minutes '
    f'for this working directory{f" (last reviewed: {reviewed_at})" if reviewed_at else ""}.'
)

print(json.dumps({
    'continue': False,
    'stopReason': msg,
    'systemMessage': msg,
    'suppressOutput': False,
}))

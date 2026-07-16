// recordnotes — pre-compaction note-capture gate for the dip.ink memory.
//
// pi sibling of the Claude Code PreCompact hook
// (agent-setup/claude-code/hooks/precompact-recordnotes.py).
//
// Behavior: before auto-compaction (threshold) or a manual /compact, check the
// ack marker for the current cwd. If a /recordnotes review happened within the
// last TTL, allow compaction. Otherwise cancel this compaction and queue
// /recordnotes as a follow-up so learnings are flushed before they're
// summarized away. The /recordnotes prompt template (or skill) writes the ack.
//
// Safety: never block "overflow" compaction (the session can't proceed without
// summarizing) and use a short nudge-cooldown so a failed ack write can't
// deadlock compaction into an infinite cancel loop.

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const ACK_DIR = join(homedir(), ".pi", "agent", "recordnotes-acks");
const TTL_SECONDS = 30 * 60; // fresh window after a /recordnotes review
const NUDGE_COOLDOWN_SECONDS = 120; // anti-loop guard
const CMD = "/recordnotes";

interface AckData {
  cwd: string;
  ts: number; // review time (written by /recordnotes)
  reviewed_at?: string;
  nudge_ts?: number; // last time this extension auto-queued /recordnotes
}

function ackPathFor(cwd: string): string {
  const key = createHash("sha256").update(cwd, "utf8").digest("hex");
  return join(ACK_DIR, `${key}.json`);
}

function readAck(cwd: string): AckData | null {
  const p = ackPathFor(cwd);
  if (!existsSync(p)) return null;
  try {
    const data = JSON.parse(readFileSync(p, "utf8")) as AckData;
    if (data.cwd !== cwd) return null;
    return data;
  } catch {
    return null;
  }
}

function writeAckPatch(cwd: string, patch: Partial<AckData>): void {
  const p = ackPathFor(cwd);
  const base: AckData = readAck(cwd) ?? { cwd, ts: 0 };
  const next: AckData = { ...base, ...patch, cwd };
  mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, JSON.stringify(next, null, 2) + "\n", "utf8");
}

/** True if /recordnotes has reviewed this cwd within TTL (no flush needed). */
function ackFreshFor(cwd: string): boolean {
  const ack = readAck(cwd);
  if (!ack) return false;
  return Date.now() / 1000 - (ack.ts ?? 0) <= TTL_SECONDS;
}

export default function (pi: ExtensionAPI) {
  pi.on("session_before_compact", async (event, ctx) => {
    // Never block hard overflow — the session can't continue without
    // summarizing, and bricking it is worse than losing pre-compact capture.
    if (event.reason === "overflow") return;

    try {
      const cwd = ctx.cwd;
      const now = Date.now() / 1000;
      const ack = readAck(cwd);

      const fresh = !!ack && now - (ack.ts ?? 0) <= TTL_SECONDS;
      if (fresh) return; // allow compaction

      // Anti-loop: if we already auto-queued /recordnotes very recently,
      // allow compaction through so the session makes forward progress
      // (e.g. ack write failed or the model didn't run it).
      const recentlyNudged =
        !!ack?.nudge_ts && now - ack.nudge_ts <= NUDGE_COOLDOWN_SECONDS;
      if (recentlyNudged) {
        ctx.ui.notify(
          "recordnotes: allowing compact (recent nudge, no fresh ack yet)",
          "info",
        );
        return;
      }

      // Stale + not recently nudged → cancel this compaction and queue a
      // /recordnotes review as a follow-up. Mirrors the Claude Code PreCompact
      // hook that blocks compact until notes are flushed.
      writeAckPatch(cwd, { nudge_ts: now });
      ctx.ui.notify(
        "Before compacting: running /recordnotes to flush learnings first. " +
          "Compact will auto-retry once the ack is fresh, or re-run /compact after.",
        "info",
      );
      pi.sendUserMessage(CMD, { deliverAs: "followUp", triggerTurn: true });
      return { cancel: true };
    } catch (err) {
      // Never break compaction on an internal error.
      ctx.ui.notify(
        `recordnotes gate error: ${(err as Error).message}`,
        "error",
      );
      return;
    }
  });

  // --- Graceful exit: flush notes before quitting ---------------------------
  //
  // pi has NO cancellable pre-exit event: `session_shutdown` returns void and
  // fires after the TUI is already stopped, so it can neither block exit nor
  // run an LLM turn. The one reliable primitive is that `ctx.shutdown()` in
  // interactive mode is deferred until the agent is idle — including queued
  // follow-up messages. So we queue /recordnotes as a follow-up (only when the
  // ack is stale) and then call shutdown(); the flush turn runs to completion
  // before the process actually exits.
  //
  // This covers graceful typed exits (/exit, /bye, /done). It CANNOT catch
  // hard exits — Ctrl+D, Ctrl+C×2, SIGTERM, `kill` — which go straight to
  // process.exit(0) after firing the non-cancellable session_shutdown. Those
  // are an unconditional escape hatch by design; don't try to intercept them.
  //
  // /quit is a built-in (special-cased by the TUI) and is intentionally left
  // alone — it stays an immediate, flush-free exit.
  const exitHandler = async (_args: string, ctx: ExtensionContext) => {
    if (ackFreshFor(ctx.cwd)) {
      ctx.shutdown();
      return;
    }
    ctx.ui.notify(
      "/recordnotes: flushing learnings before exit…",
      "info",
    );
    pi.sendUserMessage(CMD, { deliverAs: "followUp", triggerTurn: true });
    // shutdown() is deferred until idle, so the follow-up turn above completes
    // first. If the turn hangs, Ctrl+C remains the unconditional escape hatch.
    ctx.shutdown();
  };

  for (const name of ["exit", "bye", "done"] as const) {
    pi.registerCommand(name, {
      description:
        "Flush notes via /recordnotes (if not reviewed recently), then quit.",
      handler: exitHandler,
    });
  }
}

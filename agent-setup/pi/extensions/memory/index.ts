/**
 * memory — pi extension bridging the operator's dip.ink memory server.
 *
 * Exposes all eleven tools of the single memory MCP server as native pi tools:
 *
 *   wiki_search / wiki_get / wiki_backlinks / wiki_note_drop   (wiki side)
 *   graph_answer / graph_search / graph_get_note / graph_entity /
 *   graph_current_facts / graph_changes                        (graph side)
 *   memory_status                                               (operations)
 *
 * The agent-facing usage rules live in ./skill/SKILL.md and are contributed to
 * pi via the `resources_discover` event.
 *
 * Config (env):
 *   MEMORY_MCP_URL   MCP endpoint (default http://localhost:8080/mcp)
 *
 * Transport: raw Streamable-HTTP JSON-RPC over fetch (no MCP SDK — the SDK's
 * bundled zod validation has misbehaved inside pi's jiti extension host; the
 * server is stateless and answers plain JSON-RPC, so no SDK is needed).
 *
 * Design notes:
 *   - Connection is lazy: nothing happens at pi startup, so boot never depends
 *     on the server being up. The MCP session is initialized on first tool
 *     call, cached, and rebuilt once if a call fails.
 *   - Tool schemas are declared here (not discovered at runtime) so the tools
 *     are always registered and introspectable even offline. The wire args are
 *     passed straight through to the server by name.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type, type TSchema } from "typebox";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SERVER_URL = process.env.MEMORY_MCP_URL || "http://localhost:8080/mcp";

// --- raw Streamable-HTTP MCP client --------------------------------------

function parseMcpMessage(body: string, id: string | number): any {
  const trimmed = body.trim();
  if (!trimmed) throw new Error("memory: empty response body");

  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      const hit = parsed.find((m) => m?.id === id);
      if (hit) return hit;
    } else if (parsed.id === id || id === undefined) {
      return parsed;
    }
  }

  // SSE: one JSON-RPC message per `event:` block, payload on `data:` lines.
  const blocks = trimmed.split(/\r?\n\r?\n/);
  for (const block of blocks) {
    const dataLines = block
      .split(/\r?\n/)
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trimStart());
    if (!dataLines.length) continue;
    try {
      const msg = JSON.parse(dataLines.join("\n"));
      if (msg?.id === id) return msg;
    } catch {
      /* non-JSON data block (e.g. keepalive), skip */
    }
  }
  for (let i = blocks.length - 1; i >= 0; i--) {
    const dataLines = blocks[i]
      .split(/\r?\n/)
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trimStart());
    if (dataLines.length) {
      try {
        return JSON.parse(dataLines.join("\n"));
      } catch {
        /* skip */
      }
    }
  }
  throw new Error(`memory: could not parse MCP response for id=${id}`);
}

class McpHttpClient {
  private initPromise: Promise<void> | null = null;
  private sessionId: string | null = null;

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
    };
    if (this.sessionId) h["mcp-session-id"] = this.sessionId;
    return h;
  }

  private async doInit(signal?: AbortSignal): Promise<void> {
    const initRes = await fetch(SERVER_URL, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: "init",
        method: "initialize",
        params: {
          protocolVersion: "2025-06-18",
          capabilities: {},
          clientInfo: { name: "pi-memory", version: "1.0.0" },
        },
      }),
      signal,
    });
    if (!initRes.ok) {
      throw new Error(`memory initialize HTTP ${initRes.status}`);
    }
    const sid = initRes.headers.get("mcp-session-id");
    if (sid) this.sessionId = sid;
    await initRes.text();
    await fetch(SERVER_URL, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
      signal,
    }).catch(() => {
      /* best-effort */
    });
  }

  private async ensureInit(signal?: AbortSignal): Promise<void> {
    if (this.initPromise) return this.initPromise;
    this.initPromise = this.doInit(signal).catch((e) => {
      this.initPromise = null;
      throw e;
    });
    return this.initPromise;
  }

  async callTool(
    toolName: string,
    args: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<any> {
    let lastErr: unknown;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await this.ensureInit(signal);
        const id = `c${Date.now()}-${attempt}`;
        const res = await fetch(SERVER_URL, {
          method: "POST",
          headers: this.headers(),
          body: JSON.stringify({
            jsonrpc: "2.0",
            id,
            method: "tools/call",
            params: { name: toolName, arguments: args },
          }),
          signal,
        });
        if (!res.ok) throw new Error(`memory tools/call HTTP ${res.status}`);
        const msg = parseMcpMessage(await res.text(), id);
        if (!msg) throw new Error("memory: no matching response message");
        if (msg.error) {
          const e = msg.error;
          throw new Error(`memory error ${e.code}: ${e.message ?? JSON.stringify(e)}`);
        }
        return msg.result;
      } catch (err) {
        lastErr = err;
        this.initPromise = null;
      }
    }
    throw lastErr;
  }
}

const mcp = new McpHttpClient();

function mapResult(mcpResult: any) {
  const rawContent = Array.isArray(mcpResult?.content) ? mcpResult.content : [];
  const content = rawContent.map((c: any) => {
    if (!c || typeof c !== "object") return { type: "text", text: String(c) };
    if (c.type === "text") return { type: "text", text: String(c.text ?? "") };
    if (c.type === "image" && typeof c.data === "string") {
      return {
        type: "image",
        source: {
          type: "base64",
          mediaType: c.mimeType || "image/png",
          data: c.data,
        },
      };
    }
    return { type: "text", text: JSON.stringify(c) };
  });
  return {
    content,
    details: { server: SERVER_URL, isError: mcpResult?.isError === true },
    isError: mcpResult?.isError === true,
  };
}

// --- Tool definitions (schemas declared locally; args passed through) ---

interface ToolSpec {
  name: string;
  label: string;
  description: string;
  promptSnippet: string;
  parameters: TSchema;
}

const TOOLS: ToolSpec[] = [
  {
    name: "memory_status",
    label: "Memory Status",
    description:
      "Return a bounded operational summary of memory component readiness, wiki indexing, " +
      "inbox/deferred/blocked/review queues, ingest lag/partials, communities, recent usage, " +
      "and build version. Components degrade independently and no raw note/query content is returned.",
    promptSnippet: "Get bounded operational status for the memory system",
    parameters: Type.Object({}),
  },
  {
    name: "graph_answer",
    label: "Memory Answer",
    description:
      "Ask the operator's memory a question and get a DIRECT ANSWER (not search results). " +
      "Returns {answer, confidence, sources, superseded_note?, escalate}. Use this FIRST " +
      "for any factual question about the operator's stack, deploys, services, decisions, " +
      "or conventions — it distills the retrieval packet server-side, so the reply is ~150 " +
      "tokens instead of ~1,800. If it returns confidence 'not_found'/'error' or " +
      "escalate=true, fall back to graph_search.",
    promptSnippet:
      "Ask the operator's memory a factual question, get a direct distilled answer (use FIRST)",
    parameters: Type.Object({
      question: Type.String({
        description: "The factual question to answer.",
      }),
    }),
  },
  {
    name: "graph_search",
    label: "Memory Graph Search",
    description:
      "Search the operator's knowledge graph. Returns a STRUCTURED PACKET: the top atomic " +
      "FACTS — each with its source-note slug and a current/superseded flag (`current=false` " +
      "= outdated) — a COMMUNITY summary, top ENTITIES, an excerpt of the top SOURCE NOTE, " +
      "plus semantic_notes (the wiki's embedding hits, fused in). Use for BREADTH — " +
      "exploring a topic, resuming a project — or when graph_answer escalates.",
    promptSnippet:
      "Search the operator's knowledge graph (temporal facts + provenance + communities)",
    parameters: Type.Object({
      query: Type.String({
        description: "What to look for — a topic, service, decision, how-to, incident, etc.",
      }),
      k: Type.Optional(
        Type.Integer({ description: "Result depth (default 5, capped at 25)." }),
      ),
    }),
  },
  {
    name: "graph_get_note",
    label: "Memory Get Note",
    description:
      "Fetch a source note's full content by its timestamp slug. Every fact in the graph " +
      "traces to exactly one source note — this is the provenance fetch. Returns " +
      "{slug, content, valid_at} or null if not ingested.",
    promptSnippet: "Fetch a full source note by timestamp slug (provenance)",
    parameters: Type.Object({
      slug: Type.String({ description: "Timestamp slug of the source note." }),
    }),
  },
  {
    name: "graph_entity",
    label: "Memory Entity",
    description:
      "Look up a known ENTITY by name and return its summary + its CURRENT facts (superseded " +
      "facts excluded). Use when you already know the thing (a service, tool, decision) and " +
      "want its current state.",
    promptSnippet: "Look up a known entity + its current facts",
    parameters: Type.Object({
      name: Type.String({ description: "Entity name (case-insensitive match)." }),
    }),
  },
  {
    name: "graph_changes",
    label: "Memory Changes",
    description:
      "What CHANGED about a subject recently — the temporal diff. Returns facts that became " +
      "true (new) and facts that were superseded within the window. Use when resuming work " +
      "on a project/service after time away (one call instead of five searches).",
    promptSnippet: "Temporal diff: what changed about a subject recently",
    parameters: Type.Object({
      subject: Type.String({
        description: "Project, service, or topic to diff (matched against entity names + fact text).",
      }),
      since_days: Type.Optional(
        Type.Integer({ description: "Lookback window in days (default 14, max 120)." }),
      ),
    }),
  },
  {
    name: "graph_current_facts",
    label: "Memory Current Facts",
    description:
      "Return the CURRENT atomic facts about a subject (free-text). Excludes " +
      "superseded/outdated facts. Use when you specifically need what's true NOW.",
    promptSnippet: "What's currently true about a subject (excludes superseded facts)",
    parameters: Type.Object({
      subject: Type.String({ description: "The subject to get current facts about." }),
    }),
  },
  {
    name: "wiki_search",
    label: "Memory Wiki Search",
    description:
      "Search the operator's curated wiki pages (semantic/cosine search). Returns up to k " +
      "pages with name, score, type, status, tags, and a one-line description. Use for " +
      "BREADTH over curated entity/concept/synthesis pages. For a FACTUAL question, call " +
      "graph_answer FIRST; come here when you need whole pages or graph_answer escalates.",
    promptSnippet: "Semantic search over the operator's curated wiki pages",
    parameters: Type.Object({
      query: Type.String({
        description: "What to look for — a topic, service name, decision, tool, gotcha, etc.",
      }),
      k: Type.Optional(
        Type.Integer({ description: "Max results to return (default 5, capped at 25)." }),
      ),
    }),
  },
  {
    name: "wiki_get",
    label: "Memory Wiki Get",
    description:
      "Fetch the full body of a wiki page by name (without the .md extension). Returns " +
      "frontmatter fields + the markdown body + outbound and inbound wikilinks.",
    promptSnippet: "Fetch a full wiki page by name",
    parameters: Type.Object({
      name: Type.String({
        description: "Wiki page name (filename without .md). Case- and space-sensitive.",
      }),
    }),
  },
  {
    name: "wiki_backlinks",
    label: "Memory Wiki Backlinks",
    description:
      "List the names of wiki pages that link to a target page. Use for 'what references X?' " +
      "questions. Returns null if the page doesn't exist, [] if it has no inbound links.",
    promptSnippet: "List pages that wikilink to a target",
    parameters: Type.Object({
      name: Type.String({ description: "Wiki page name (filename without .md)." }),
    }),
  },
  {
    name: "wiki_note_drop",
    label: "Memory Note Drop",
    description:
      "Drop a note into the memory's inbox (notes/<YYYY-MM-DD-HHMMSS-slug>/) so the curator " +
      "can promote it into wiki pages. Use whenever you learn something non-obvious that a " +
      "future session would want to look up. The server commits + pushes to the wiki repo. " +
      "NEVER include credentials/tokens/passwords in note_md or attachments — reference " +
      "vault paths instead. See the memory skill for the note format and capture bar.",
    promptSnippet: "Capture a learning into the operator's memory inbox",
    parameters: Type.Object({
      slug: Type.String({
        description:
          "Short kebab-case slug, e.g. 'claude-mac-cleanup'. Must match ^[a-z0-9][a-z0-9-]{0,63}$.",
      }),
      note_md: Type.String({
        description:
          "Full source-note markdown body, including YAML frontmatter with captured/session/topic. " +
          "No secrets — notes are git-tracked.",
      }),
      attachments: Type.Optional(
        Type.Record(
          Type.String(),
          Type.String({ description: "Text attachment content (.log, .yaml, .json, …), ≤256KB each." }),
        ),
      ),
      binary_attachments: Type.Optional(
        Type.Record(
          Type.String(),
          Type.String({ description: "Binary attachment as base64 (screenshots, pdfs), ≤2MB decoded." }),
        ),
      ),
    }),
  },
];

// --- Extension entry point ---

export default function memoryExtension(pi: ExtensionAPI) {
  for (const spec of TOOLS) {
    pi.registerTool({
      name: spec.name,
      label: spec.label,
      description: spec.description,
      promptSnippet: spec.promptSnippet,
      parameters: spec.parameters,
      async execute(_toolCallId, params, signal) {
        const result = await mcp.callTool(
          spec.name,
          params as Record<string, unknown>,
          signal,
        );
        return mapResult(result);
      },
    });
  }

  // Contribute the agent-facing skill (usage rules, note format, NEVER list).
  pi.on("resources_discover", async () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    return { skillPaths: [path.join(here, "skill")] };
  });

  // /memory — quick connection + health check against the plain HTTP API.
  pi.registerCommand("memory", {
    description: "memory: check server connection and index/graph health",
    handler: async (_args, ctx) => {
      const origin = SERVER_URL.replace(/\/mcp\/?$/, "");
      try {
        const res = await fetch(`${origin}/health`, { signal: ctx.signal });
        const data = (await res.json()) as Record<string, unknown>;
        const pages = data.pages_indexed ?? "?";
        const graph = data.graph_ready ? "graph ready" : "graph NOT ready";
        ctx.ui.notify(
          `memory ${res.ok ? "OK" : `HTTP ${res.status}`} — ${pages} pages indexed, ${graph} @ ${origin}`,
          res.ok ? "info" : "warning",
        );
      } catch (err) {
        ctx.ui.notify(
          `memory unreachable at ${origin}: ${(err as Error).message}`,
          "error",
        );
      }
    },
  });
}

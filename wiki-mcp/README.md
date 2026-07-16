# wiki-mcp

Semantic-search + note-drop MCP server over the markdown wiki. One container.

- **MCP tools** (HTTP transport at `/mcp`): `wiki_search`, `wiki_get`,
  `wiki_backlinks` (read) and `wiki_note_drop` (write — commits + pushes a note
  folder into the repo's `notes/` inbox).
- **Plain HTTP**: `/api/search`, `/api/page/<name>`, `/api/backlinks/<name>`,
  `/api/metrics`, `/api/reindex` (POST), `/live`, `/health`.

## Behavior

- **Startup**: binds HTTP immediately; a supervised background thread clones
  (or refreshes) the wiki repo, scans page metadata, and embeds changed pages.
  Cached unchanged vectors are served while new ones embed; provider outages
  leave a **degraded but searchable** index (explicit lexical fallback) rather
  than a dead server. Bounded retry/backoff, then a normal 5-min refresh loop.
- **Embeddings**: OpenAI `text-embedding-3-small` (1536-dim) by default; only
  changed pages (content-hash) are re-embedded. Vectors persist to
  `WIKI_MCP_CACHE_DIR` so restarts don't re-pay the embed cost. Set
  `WIKI_MCP_EMBED_PROVIDER=fastembed` for a $0 local alternative
  (`pip install fastembed`).
- **Note drops** are validated (slug regex, size caps, reserved names),
  idempotent (payload-hash dedup across retries), serialized against the git
  working tree, and rolled back if the push fails.
- **Instrumentation**: every tool call is appended to a JSONL log (default
  `<cache>/queries.jsonl`) — the weekly gaps miner reads it via `/api/metrics`.

## Key env

| Var | Default | |
|---|---|---|
| `WIKI_REPO_URL` | — | HTTPS repo URL; unset = serve a static `WIKI_ROOT`, writes disabled |
| `WIKI_REPO_TOKEN` / `WIKI_REPO_USER` | — / `token` | HTTPS basic-auth for pull+push |
| `OPENAI_API_KEY` | — | embeddings |
| `WIKI_MCP_CACHE_DIR` | — | persist embeddings + metrics across restarts |
| `WIKI_MCP_ALLOWED_HOSTS` | `localhost,127.0.0.1,wiki-mcp` | add your ingress hostname (DNS-rebinding protection) |
| `WIKI_MCP_REINDEX_SEC` | `300` | refresh interval |

## Run locally

```sh
pip install -r requirements.txt
WIKI_ROOT=/path/to/wiki OPENAI_API_KEY=sk-... python server.py
python -m unittest discover -s tests
```

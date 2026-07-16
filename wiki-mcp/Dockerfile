# syntax=docker/dockerfile:1.9
# wiki-mcp container — semantic search + note-drop MCP server over a markdown
# wiki. The wiki itself is NOT baked into the image: set WIKI_REPO_URL (+
# WIKI_REPO_TOKEN for private repos / note-drop pushes) and the server clones
# and refreshes it at runtime, or mount a checkout at /wiki and set
# WIKI_ROOT=/wiki.
FROM python:3.12-slim

# curl for healthcheck, git for the runtime clone + push done by the
# wiki_note_drop MCP tool (HTTPS token auth).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./
COPY tests/ ./tests/

RUN OPENAI_API_KEY=test-only \
    WIKI_MCP_EMBED_PROVIDER=openai \
    WIKI_MCP_BACKGROUND_REINDEX=0 \
    WIKI_ROOT=/tmp/wiki-mcp-build-test \
    python3 -m unittest discover -s tests

ENV PORT=8080 \
    HOST=0.0.0.0 \
    WIKI_MCP_REINDEX_SEC=300

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8080/live || exit 1

CMD ["python3", "-u", "server.py"]

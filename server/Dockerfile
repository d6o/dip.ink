# syntax=docker/dockerfile:1.9
# One image, three roles (selected by command):
#   - memory server  python server.py     (the single MCP: wiki_* + graph_* tools)
#   - ingest         python ingest.py     (INGEST_MODE=cron|status|ingest|query)
#   - memory loops   python loops/<job>.py
FROM python:3.12-slim

ARG DIPINK_VERSION=dev
ARG DIPINK_BUILD=unknown
ENV DIPINK_VERSION=$DIPINK_VERSION \
    DIPINK_BUILD=$DIPINK_BUILD

# curl for healthcheck; git for the wiki clone + note-drop push + ingest clones.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core.py wiki.py graph.py server.py ingest.py chat_fallback.py ./
COPY loops/ ./loops/
COPY tests/ ./tests/

# Wiki-side unit tests run at build time (no network, no keys).
RUN OPENAI_API_KEY=test-only \
    WIKI_MCP_EMBED_PROVIDER=openai \
    WIKI_MCP_BACKGROUND_REINDEX=0 \
    WIKI_ROOT=/tmp/build-test \
    python3 -m unittest discover -s tests

ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8080/live || exit 1

CMD ["python3", "-u", "server.py"]

# syntax=docker/dockerfile:1.9
# One image serves four roles (selected by command):
#   - ingest        python ingest.py           (INGEST_MODE=cron|status|ingest|query)
#   - MCP server    python mcp/server.py       (graph_answer / graph_search / ...)
#   - memory loops  python loops/<job>.py      (healthcheck, gaps, alerts, janitor,
#                                               build_communities)
FROM python:3.12-slim

# git for cloning the wiki repo when running ingest without an init step.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingest.py .
COPY mcp/ ./mcp/
COPY loops/ ./loops/

ENV PYTHONUNBUFFERED=1

# Default to the resumable cron batch; the MCP service overrides the command.
ENV INGEST_MODE=cron
CMD ["python", "ingest.py"]

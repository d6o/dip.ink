FROM node:24-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PI_OFFLINE=1 \
    PI_SKIP_VERSION_CHECK=1 \
    PI_TELEMETRY=0

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates jq xz-utils gosu \
      python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1001 runner \
    && useradd -m -u 1001 -g 1001 -s /bin/bash runner

# yq was previously downloaded architecture-specifically here but is unused by
# the runner or curator scripts (jq + python3-yaml cover YAML/JSON needs).

ARG PI_VERSION=0.80.6
RUN npm install -g --ignore-scripts "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && pi --version

# Build context is ./curator so the image carries the runner AND the shared
# curator toolchain (scripts + prompts). Wiki repos stay data-only: workflows
# reference /opt/dip.ink/{scripts,prompts}/... instead of vendoring copies.
COPY pi-runner/src/ /opt/pi-runner/
COPY scripts/ /opt/dip.ink/scripts/
COPY prompts/ /opt/dip.ink/prompts/
RUN chmod +x /opt/pi-runner/entrypoint.sh /opt/pi-runner/pi-event-log.py \
    /opt/dip.ink/scripts/*.sh /opt/dip.ink/scripts/*.py

WORKDIR /workspace
ENTRYPOINT ["/opt/pi-runner/entrypoint.sh"]

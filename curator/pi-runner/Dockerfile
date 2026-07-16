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

ARG YQ_VERSION=v4.45.1
RUN curl -fsSL "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" \
      -o /usr/local/bin/yq \
    && chmod +x /usr/local/bin/yq \
    && yq --version

ARG PI_VERSION=0.80.6
RUN npm install -g --ignore-scripts "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && pi --version

COPY src/ /opt/pi-runner/
RUN chmod +x /opt/pi-runner/entrypoint.sh /opt/pi-runner/pi-event-log.py

WORKDIR /workspace
ENTRYPOINT ["/opt/pi-runner/entrypoint.sh"]

FROM python:3.12-slim

ARG TARGETARCH
ARG KUBECTL_VERSION=v1.33.4
ARG HELM_VERSION=v3.18.6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates coreutils curl git gzip jq openssh-client \
       postgresql-client tar xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && case "${TARGETARCH:-amd64}" in \
         amd64) KARCH=amd64 ;; \
         arm64) KARCH=arm64 ;; \
         *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSLo /usr/local/bin/kubectl \
       "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${KARCH}/kubectl" \
    && chmod 0755 /usr/local/bin/kubectl \
    && curl -fsSLo /tmp/helm.tar.gz \
       "https://get.helm.sh/helm-${HELM_VERSION}-linux-${KARCH}.tar.gz" \
    && tar -xzf /tmp/helm.tar.gz -C /tmp \
    && mv "/tmp/linux-${KARCH}/helm" /usr/local/bin/helm \
    && chmod 0755 /usr/local/bin/helm \
    && rm -rf /tmp/helm.tar.gz "/tmp/linux-${KARCH}"

# Install the gateway and its complete test/lint dependency set in the toolbox.
# The source is copied before operational scripts so Docker invalidates only the
# layers affected by a given kind of change.
COPY pyproject.toml README.md /tmp/local-ai-hub/
COPY services/gateway/src /tmp/local-ai-hub/services/gateway/src
RUN python -m pip install --no-cache-dir \
      "/tmp/local-ai-hub[dev]" \
      "huggingface_hub>=0.34,<1" \
      "requests>=2.32,<3" \
    && rm -rf /tmp/local-ai-hub

COPY ops /opt/local-ai-hub/ops
COPY config /opt/local-ai-hub/config
COPY deploy/helm /opt/local-ai-hub/deploy/helm
RUN chmod +x /opt/local-ai-hub/ops/hubctl.py /opt/local-ai-hub/ops/*.sh 2>/dev/null || true

WORKDIR /workspace
ENTRYPOINT ["python", "/opt/local-ai-hub/ops/hubctl.py"]

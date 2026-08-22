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

COPY pyproject.toml README.md /tmp/agent-ui/
COPY services/gateway/src /tmp/agent-ui/services/gateway/src
RUN python -m pip install --no-cache-dir \
      "/tmp/agent-ui[dev]" \
      "huggingface_hub>=0.34,<1" \
      "requests>=2.32,<3" \
    && rm -rf /tmp/agent-ui

COPY ops /opt/agent-ui/ops
COPY config /opt/agent-ui/config
COPY deploy/helm /opt/agent-ui/deploy/helm
RUN chmod +x /opt/agent-ui/ops/hubctl.py /opt/agent-ui/ops/*.sh 2>/dev/null || true

WORKDIR /workspace
ENTRYPOINT ["python", "/opt/agent-ui/ops/hubctl.py"]

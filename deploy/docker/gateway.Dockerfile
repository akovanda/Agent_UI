FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=8000 \
    PYTHONPATH=/app/src:/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 hub \
    && useradd --system --uid 10001 --gid hub --home /app hub

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir . "uvicorn[standard]>=0.30,<1" \
    && chmod +x /app/deploy/docker/gateway-entrypoint.py \
    && mkdir -p /var/lib/agent-ui \
    && chown -R hub:hub /app /var/lib/agent-ui

USER hub
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/deploy/docker/gateway-entrypoint.py"]

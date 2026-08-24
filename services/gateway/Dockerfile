FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 gateway

COPY pyproject.toml README.md ./
COPY services/gateway/src ./services/gateway/src
COPY services/gateway/migrations ./services/gateway/migrations
COPY config/gateway ./config
COPY config/memory ./config/memory

RUN pip install .

USER gateway
EXPOSE 8000

CMD ["uvicorn", "local_ai_hub.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

#!/usr/bin/env bash
set -Eeuo pipefail

retry() {
  local attempts="$1"
  shift
  local count=1
  until "$@"; do
    if (( count >= attempts )); then
      return 1
    fi
    count=$((count + 1))
    sleep 2
  done
}

retry 30 curl -fsS http://gateway:8000/health >/dev/null
retry 30 curl -fsS http://llama:8080/health >/dev/null
retry 30 pg_isready -h postgres -p 5432 -U "${POSTGRES_USER:-local_ai_hub}" >/dev/null

curl -fsS \
  -H "Authorization: Bearer ${GATEWAY_API_KEY}" \
  http://gateway:8000/v1/models \
  | jq -e '.data | length >= 1' >/dev/null

printf 'Smoke checks passed for PostgreSQL, llama.cpp, gateway, and model discovery.\n'

#!/usr/bin/env bash
set -Eeuo pipefail

release="${1:?release is required}"
namespace="${2:?namespace is required}"
env_file="${3:?env file is required}"
model_values="${4:?generated model values are required}"
shift 4

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -

secret_name="${release}-secrets"
kubectl -n "$namespace" create secret generic "$secret_name" \
  --from-literal=postgres-user="$POSTGRES_USER" \
  --from-literal=postgres-password="$POSTGRES_PASSWORD" \
  --from-literal=llama-api-key="$LLAMA_API_KEY" \
  --from-literal=gateway-api-key="$GATEWAY_API_KEY" \
  --from-literal=webui-secret-key="$WEBUI_SECRET_KEY" \
  --from-literal=hermes-api-key="$HERMES_API_KEY" \
  --from-literal=hermes-dashboard-username="$HERMES_DASHBOARD_USERNAME" \
  --from-literal=hermes-dashboard-password="$HERMES_DASHBOARD_PASSWORD" \
  --from-literal=gateway-database-url="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${release}-postgres:5432/local_ai_hub" \
  --from-literal=openwebui-database-url="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${release}-postgres:5432/open_webui" \
  --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install "$release" /opt/local-ai-hub/deploy/helm/local-ai-hub \
  --namespace "$namespace" \
  --create-namespace \
  --set-string global.existingSecret="$secret_name" \
  --set-string fullnameOverride="$release" \
  -f "$model_values" \
  "$@"

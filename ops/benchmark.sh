#!/usr/bin/env bash
set -Eeuo pipefail

api="${GATEWAY_URL:-http://gateway:8000/v1}"
key="${GATEWAY_API_KEY:?GATEWAY_API_KEY is required}"
runs="${1:-3}"
[[ "$runs" =~ ^[1-9][0-9]*$ ]] || { echo "runs must be a positive integer" >&2; exit 2; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

profiles=(assistant storyteller)
printf 'profile,run,seconds,prompt_tokens,completion_tokens,total_tokens,characters\n'
for profile in "${profiles[@]}"; do
  for ((run = 1; run <= runs; run++)); do
    if [[ "$profile" == assistant ]]; then
      prompt='Explain how a Kubernetes controller reconciliation loop works, including idempotency and retry behavior.'
    else
      prompt='Write a vivid but concise scene in which a tired starfighter squadron returns to a damaged carrier after a difficult victory.'
    fi
    request="$tmp/request.json"
    response="$tmp/response.json"
    jq -n \
      --arg model "$profile" \
      --arg prompt "$prompt" \
      '{model:$model,messages:[{role:"user",content:$prompt}],stream:false,max_tokens:512}' \
      > "$request"
    seconds="$(curl -sS --fail-with-body --max-time 1800 \
      -o "$response" \
      -w '%{time_total}' \
      -H 'Content-Type: application/json' \
      -H "Authorization: Bearer $key" \
      --data-binary "@$request" \
      "$api/chat/completions")"
    content="$(jq -er '.choices[0].message.content' "$response")"
    prompt_tokens="$(jq -r '.usage.prompt_tokens // 0' "$response")"
    completion_tokens="$(jq -r '.usage.completion_tokens // 0' "$response")"
    total_tokens="$(jq -r '.usage.total_tokens // 0' "$response")"
    printf '%s,%s,%s,%s,%s,%s,%s\n' \
      "$profile" "$run" "$seconds" "$prompt_tokens" "$completion_tokens" \
      "$total_tokens" "${#content}"
  done
done

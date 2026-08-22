#!/usr/bin/env bash
set -Eeuo pipefail

iterations="${1:-25}"
[[ "$iterations" =~ ^[1-9][0-9]*$ ]] || { echo "iterations must be a positive integer" >&2; exit 2; }
api="${GATEWAY_URL:-http://gateway:8000/v1}"
key="${GATEWAY_API_KEY:?GATEWAY_API_KEY is required}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsS --max-time 30 \
  -H "Authorization: Bearer $key" \
  "$api/models" \
  | jq -e '.data | length >= 2' >/dev/null

printf 'iteration,requested_profile,returned_model,seconds,characters\n'
for ((i = 1; i <= iterations; i++)); do
  if (( i % 2 == 1 )); then
    profile=assistant
    prompt="Reply with exactly: ASSISTANT-$i"
  else
    profile=storyteller
    prompt="Reply with exactly: STORY-$i"
  fi
  request="$tmp/request.json"
  response="$tmp/response.json"
  jq -n \
    --arg model "$profile" \
    --arg prompt "$prompt" \
    '{model:$model,messages:[{role:"user",content:$prompt}],stream:false,max_tokens:64}' \
    > "$request"

  metrics="$(curl -sS --fail-with-body --max-time 1800 \
    -o "$response" \
    -w '%{http_code},%{time_total}' \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $key" \
    --data-binary "@$request" \
    "$api/chat/completions")"
  status="${metrics%%,*}"
  seconds="${metrics#*,}"
  [[ "$status" == 200 ]] || { cat "$response" >&2; exit 1; }

  content="$(jq -er '.choices[0].message.content' "$response")"
  returned="$(jq -r '.model // "unknown"' "$response")"
  [[ -n "$content" ]] || { echo "Empty response on iteration $i" >&2; exit 1; }
  case "$profile:$returned" in
    assistant:assistant|assistant:gpt-oss-20b|storyteller:storyteller|storyteller:stheno-8b) ;;
    *)
      echo "Wrong-model response: requested $profile but response reported $returned" >&2
      exit 1
      ;;
  esac
  printf '%s,%s,%s,%s,%s\n' "$i" "$profile" "$returned" "$seconds" "${#content}"
done

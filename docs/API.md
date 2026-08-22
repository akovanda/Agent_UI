# Gateway API

The gateway presents a small OpenAI-compatible surface plus local administration and memory APIs.
All endpoints except liveness, readiness, and metrics require:

```http
Authorization: Bearer <GATEWAY_API_KEY>
```

## OpenAI-compatible endpoints

### `GET /v1/models`

Returns advertised virtual profiles. Initial profiles:

- `assistant`
- `assistant-fast`
- `assistant-deep`
- `storyteller`
- `auto`

The response metadata includes the selected route type and concrete backend where applicable.

### `POST /v1/chat/completions`

Supports streamed and non-streamed chat-completion requests. The gateway preserves unknown
llama.cpp-compatible request fields, applies profile defaults only when the client did not supply a
value, and rewrites the backend `model` before proxying.

Minimal request:

```json
{
  "model": "assistant",
  "messages": [
    {"role": "user", "content": "Explain why this VLAN cannot see mDNS traffic."}
  ],
  "stream": true
}
```

Automatic routing:

```json
{
  "model": "auto",
  "messages": [
    {"role": "user", "content": "Continue our campaign scene aboard the cruiser."}
  ]
}
```

Response headers:

| Header | Meaning |
|---|---|
| `X-Local-AI-Profile` | Resolved virtual profile |
| `X-Local-AI-Backend-Model` | Concrete llama.cpp model alias |
| `X-Local-AI-Route-Reason` | Deterministic routing explanation |

### Routing overrides

Use one of:

- model `assistant` or `storyteller`;
- model `auto` with a latest-user-message prefix `/assistant` or `/story`;
- `X-Local-AI-Profile: <profile-id>`;
- a configured direct backend alias for diagnostics or Hermes.

Header override is intended for trusted clients. Do not expose arbitrary profile override to
untrusted multi-user clients without policy checks.

### Reasoning effort

For GPT-OSS profiles:

```http
X-Reasoning-Effort: low|medium|high
```

or a JSON field:

```json
{"reasoning_effort": "high"}
```

The gateway validates the value and forwards it as the native OpenAI-compatible
`reasoning_effort` field supported by the selected llama.cpp GPT-OSS template. Header wins over
JSON, which wins over the profile default. For GPT-OSS, local profile instructions, client
system/developer instructions, and retrieved memory are consolidated into one leading developer
message so none of those sections is discarded by the Harmony prompt template.

## Local APIs

### `POST /api/routes/preview`

Resolves a request without calling the model. Useful for routing evaluation.

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Write a scene in the engine room."}]
}
```

Returns profile, backend, and route reason.

### `GET /api/models/status`

Returns llama.cpp router model states such as `loaded`, `unloaded`, `loading`, `sleeping`, or a
failed state. This endpoint is diagnostic and may be unavailable in fixed-model fallback mode.

### `POST /api/admin/reload-profiles`

Reloads and validates the configured profile document without restarting the process. The standard
Docker deployment reads `/runtime/profiles.json`. Existing in-flight requests
continue with their already-resolved profile.

### `POST /api/memories`

Creates an explicit durable memory.

```json
{
  "namespace": "infrastructure",
  "content": "The barn UCS has one Tesla T4 with 16 GB VRAM.",
  "source": "manual",
  "importance": 0.8,
  "metadata": {"host": "ucs"}
}
```

The authenticated deployment is currently single-user and uses `DEFAULT_USER_ID` unless the
trusted client supplies an allowed user header. Multi-user identity federation is a later phase.

### `GET /api/memories/search`

Query parameters:

| Parameter | Required | Description |
|---|---:|---|
| `q` | yes | Full-text query |
| `namespace` | no, repeatable | Restrict retrieval namespaces |
| `limit` | no | Result count within configured cap |

Example:

```text
/api/memories/search?q=Tesla%20T4&namespace=infrastructure&limit=5
```

### `GET /health/live`

Process liveness only.

### `GET /health/ready`

Dependency readiness. Behavior depends on whether memory is configured as required.

### `GET /metrics`

Prometheus text exposition. Do not expose publicly; labels deliberately avoid prompt contents.

## Error shape

Errors use an OpenAI-like envelope:

```json
{
  "error": {
    "message": "human-readable explanation",
    "type": "invalid_request_error",
    "param": null,
    "code": null
  }
}
```

Important mappings:

| Status | Type | Typical cause |
|---:|---|---|
| 400 | `invalid_request_error` | Invalid JSON, messages, or reasoning effort |
| 401 | `authentication_error` | Missing/incorrect API key |
| 404 | `invalid_request_error` | Unknown profile/model |
| 502 | `upstream_error` | llama.cpp connection or response failure |
| 503 | `model_unavailable` | Model could not load, a transition timed out, or memory is disabled |

## Compatibility boundary

The endpoint intentionally implements only the subset required by Open WebUI, SillyTavern,
Hermes, and direct local clients. It is not a full replacement for every OpenAI API resource.
Add new endpoints only with contract tests and a real client requirement.

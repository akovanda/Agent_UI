# Gateway API

Agent UI exposes an authenticated OpenAI-compatible gateway plus read-only setup and routing inspection endpoints.

## Authentication

All routes except health and metrics require:

```http
Authorization: Bearer <GATEWAY_API_KEY>
```

## OpenAI-compatible routes

```text
GET  /v1/models
POST /v1/chat/completions
POST /v1/responses
POST /v1/completions
POST /infill
POST /v1/infill
POST /v1/embeddings
POST /v1/rerank
POST /v1/reranking
POST /v1/images/generations
```

Requests use either an experience ID such as `chat` or a directly registered model ID in the `model` field.

## Control headers

| Header | Purpose |
|---|---|
| `X-Agent-UI-Experience` | Override the request's selected experience. |
| `X-Agent-UI-User` | Stable user identity for memory isolation. |
| `X-Agent-UI-Memory-Namespace` | Add one approved namespace to retrieval. |
| `X-Reasoning-Effort` | Select a declared model-specific effort mapping. |
| `X-Request-ID` | Supply a request correlation ID. |

The legacy `X-Local-AI-*` header names remain accepted during the v0.3 compatibility window.

Routing response headers:

```text
X-Agent-UI-Experience
X-Agent-UI-Model
X-Agent-UI-Backend
X-Agent-UI-Route-Reason
X-Request-ID
```

## Model listing

`GET /v1/models` returns both experience and registered-model entries. Metadata includes:

- resource type;
- capability requirements;
- availability;
- backend ID and kind;
- declared model capabilities;
- reasoning contract;
- priority.

An empty installation still advertises experience templates with `available: false`, allowing a setup UI to explain what remains to be configured.

## Setup status

```text
GET /api/setup/status
```

Example shape:

```json
{
  "version": 2,
  "setup_required": true,
  "backends": {
    "local-llama": {
      "kind": "llama.cpp",
      "healthy": true,
      "detail": 200
    }
  },
  "models": {},
  "experiences": {
    "chat": {
      "capability": "chat",
      "available": false,
      "description": "General conversational and knowledge work."
    }
  }
}
```

## Catalog inspection

```text
GET /api/catalog
```

Returns the effective validated runtime catalog. Backends contain environment-variable names, never secret values.

Persistent catalog mutation is deliberately not exposed through the chat gateway. Use `./hub catalog plan/apply` so host mounts and credentials cannot be changed through prompt injection.

## Route preview

```text
POST /api/routes/preview
```

```json
{
  "model": "code",
  "messages": [{"role": "user", "content": "Review this function"}],
  "profile_override": null
}
```

Response:

```json
{
  "requested_model": "code",
  "experience": "code",
  "model": "registered-model-id",
  "backend": "registered-backend-id",
  "capabilities": ["chat", "code"],
  "reason": "experience selected explicitly; selected highest-priority capable model"
}
```

## Catalog reload

```text
POST /api/admin/reload-catalog
POST /api/admin/reload-profiles
```

Reloads the effective runtime file and reconstructs backend clients. Normal `./hub catalog apply` performs this through container recreation so generated storage mounts are also updated.

## Memory

Create explicit memory:

```text
POST /api/memories
```

```json
{
  "user_id": "optional-override",
  "namespace": "projects",
  "content": "The deployment uses a private network.",
  "source": "operator",
  "metadata": {},
  "importance": 0.7
}
```

Search:

```text
GET /api/memories/search?q=private+network&namespace=projects&limit=10
```

Memory is isolated by user and namespace. Retrieved content is inserted as explicitly labeled untrusted reference material rather than privileged instructions.

## Health and metrics

```text
GET /health/live
GET /health/ready
GET /health
GET /metrics
```

`/health` returns HTTP 200 with `status: setup_required` when no models are registered. This is a valid control-plane state, not a crash loop.

## Errors

OpenAI-compatible routes return:

```json
{
  "error": {
    "message": "...",
    "type": "model_unavailable",
    "param": null,
    "code": null
  }
}
```

Common types:

- `authentication_error`
- `invalid_request_error`
- `model_unavailable`
- `backend_unavailable`
- `upstream_error`

The gateway fails closed when an experience has no capable model, a requested capability is undeclared, or an explicitly coordinated local model cannot be loaded.

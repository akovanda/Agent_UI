# Open WebUI

Open WebUI is Agent UI's default human-facing interface. It connects only to the authenticated gateway, not directly to local or remote inference backends.

## Connection

Compose configures:

```text
OPENAI_API_BASE_URL=http://gateway:8000/v1
OPENAI_API_KEY=<GATEWAY_API_KEY>
```

The model picker receives stable experience IDs and directly registered model IDs from `/v1/models`.

## Experience entries

Typical entries:

- `chat`
- `code`
- `story`
- `image`
- `agent`

An experience may appear with `available: false` before a capable model is registered. Setup state is visible at `/api/setup/status`; inference fails with a clear `model_unavailable` error rather than selecting an incompatible model.

## Workspace models

Open WebUI Workspace Models can layer UI-specific prompts, knowledge, tools, and display names on top of Agent UI experiences. Keep the underlying model field stable:

```text
base model: code
workspace name: Repository Engineer
```

This avoids coupling Open WebUI exports to a physical model name.

## Files and RAG

Open WebUI can manage its own document collections and uses PostgreSQL/pgvector in the reference stack. An embedding-capable model may be registered through Agent UI, or Open WebUI may use its own embedding configuration.

Treat retrieved text as untrusted source material. Do not place tool credentials or privileged system instructions inside documents.

## Images

Open WebUI can use Agent UI's `/v1/images/generations` route when an image-capable backend is registered. It can also connect directly to an image workflow service using Open WebUI's native image settings. The latter is useful when the workflow engine exposes controls not represented by the OpenAI image request format.

## Code

Select the `code` experience for code chat and review. Tools or IDE clients may call the same gateway through chat, completions, or infill routes when the selected model declares the corresponding capability.

## Story work

The `story` experience is available in Open WebUI for ordinary creative chat. Use the optional story workspace when character cards, lorebooks, group roleplay, or scene-specific context management are required.

## Reasoning effort

Open WebUI request parameters may include `reasoning_effort`; custom functions or clients can set `X-Reasoning-Effort`. Agent UI maps the stable value through the selected model's feature declaration.

## Tools

A model's `features.tools: true` declaration means the backend can represent tool calls. It does not grant access. Configure tools in Open WebUI and begin with read-only operations. Require approval for writes, shell commands, infrastructure changes, and external communications.

## Memory

The reference Compose stack disables Open WebUI's independent personal memory by default:

```text
OPEN_WEBUI_ENABLE_MEMORIES=false
```

Agent UI shared memory remains available for experiences that enable it. Running both systems is possible, but duplicates and conflicting facts are harder to reason about.

## Users

Open WebUI authentication is enabled. The first account becomes administrator in the standard first-run flow. For multi-user memory isolation, ensure a stable user identifier is forwarded to Agent UI through `X-Agent-UI-User`; otherwise the configured default user is used.

## Exposure

Open WebUI binds to loopback by default. For remote access, use a VPN or authenticated TLS reverse proxy. Do not expose the gateway or underlying inference services merely because the UI has authentication.

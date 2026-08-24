# Capabilities and Model Features

Agent UI separates **what a deployment can do** from **how a particular backend exposes it**.

## Capabilities

Capabilities drive experience selection and endpoint authorization. They are extensible identifiers rather than a closed enum.

Common capabilities:

- `chat`
- `code`
- `story`
- `agent`
- `image`
- `embeddings`
- `rerank`
- `infill`
- `completions`
- `vision`
- `audio`

A model may declare multiple capabilities:

```yaml
capabilities:
  chat: {}
  code:
    endpoints: [chat, completions, infill]
  vision:
    input_types: [text, image]
```

Capability metadata is exposed through `/v1/models` for clients that choose to inspect it. Unknown metadata is preserved so specialized integrations can add constraints without changing the core schema.

## Endpoint gates

The gateway will not send an image request to a text-only model or an embedding request to a model that did not declare embeddings. The default compatibility sets are:

| Gateway route | Accepted capabilities |
|---|---|
| `/v1/chat/completions` | chat, code, story, agent, vision |
| `/v1/responses` | chat, code, story, agent, vision |
| `/v1/completions` | completions, code |
| `/infill`, `/v1/infill` | infill, code |
| `/v1/embeddings` | embeddings |
| `/v1/rerank`, `/v1/reranking` | rerank |
| `/v1/images/generations` | image |

This prevents accidental modality fallback. Clients receive a clear error describing the missing declaration.

## Reasoning and effort

Reasoning is a model feature, not a model-name pattern:

```yaml
features:
  reasoning:
    supported: true
    request_field: reasoning_effort
    transport: body
    values:
      fast: low
      balanced: medium
      deep: high
      none: null
    unsupported_policy: reject
```

Stable client-facing values can map to strings, numbers, booleans, objects, or `null`. An empty mapping passes the client's value through unchanged.

### Body transport

```yaml
transport: body
request_field: reasoning_effort
```

produces:

```json
{"reasoning_effort": "high"}
```

### Chat-template transport

Some local runtimes require template arguments:

```yaml
transport: chat_template_kwargs
request_field: effort
```

produces:

```json
{"chat_template_kwargs": {"effort": "high"}}
```

### Precedence

1. `X-Reasoning-Effort` header
2. request-body `reasoning_effort`
3. experience default `reasoning_effort`
4. model default `reasoning_effort`
5. no effort field

Explicit unsupported values fail before inference. A model that does not implement reasoning may set `unsupported_policy: ignore` or `reject`.

A model default makes directly advertised aliases useful as reasoning presets. For example, `fast`
and `deep` catalog IDs can map to one upstream deployment while choosing different effort values.
The selected model's feature declaration still validates and maps the preset before forwarding it.

## Tool use

Tool support is declared explicitly:

```yaml
features:
  tools: true
```

Agent UI forwards OpenAI-compatible tool schemas and tool calls. Declaring support does not grant tools or permissions. Tool availability is controlled by the human-facing client or optional agent harness.

Recommended rollout:

1. Read-only tools.
2. Narrow filesystem/network scope.
3. Approval for writes and destructive operations.
4. Audit logging.
5. Circuit breakers for repeated no-progress calls.

## Structured output

```yaml
features:
  structured_output: true
```

indicates that the deployment can honor JSON schema or constrained-output requests supported by its backend. Payload fields are forwarded unchanged.

## Vision and audio

```yaml
features:
  vision: true
  audio: true
```

These flags describe transport support; clients must still send content in a format accepted by the backend. Agent UI does not transcode media unless an explicitly registered adapter performs that work.

## Code paths

A code-oriented deployment can expose one or more interfaces:

- chat-based code generation and review;
- classic text completions;
- fill-in-the-middle through `/infill`;
- tool-driven repository operations in an agent client.

Declare `code` plus `completions` or `infill` when those endpoints are genuinely supported. Do not advertise FIM solely because a model was trained on code.

## Story paths

`story` is a semantic capability used by the story experience. Long-running campaigns typically also need:

- an experience-specific system prompt;
- higher creative sampling defaults;
- a story memory namespace;
- a lorebook/character-card client such as the optional story workspace.

The capability does not imply a particular model or UI.

## Image paths

An `image` model normally uses an `openai-compatible` backend whose image endpoint is declared explicitly. Raw workflow engines can be placed behind a small adapter or integrated directly through a human-facing client. Model metadata can advertise sizes, formats, or workflow names:

```yaml
capabilities:
  image:
    sizes: [512x512, 1024x1024]
    formats: [png, webp]
```

## Priority and pinning

Unpinned experiences choose the highest-priority capable model:

```yaml
priority: 100
```

Use priority to express an operator preference among compatible deployments. Pin an experience when deterministic model identity is more important than automatic failover.

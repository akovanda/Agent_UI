# Optional Story Workspace

The Compose and Helm deployments can run SillyTavern as an optional story-focused client. Agent UI does not require it, and the story capability is not tied to a particular model.

## Start

Compose:

```bash
./hub up --story
./hub ports
```

Kubernetes:

```yaml
sillyTavern:
  enabled: true
```

## Connect

Configure an OpenAI-compatible endpoint:

```text
API URL: http://gateway:8000/v1
API key: GATEWAY_API_KEY
Model: story
```

From a browser outside the Docker network, use the gateway's loopback URL/port instead of the service name.

## Why a separate story surface

A generic chat UI can write fiction, but a story workspace adds domain-specific context management:

- character cards;
- personas;
- lorebooks/world information;
- author's notes;
- scene state;
- group conversations;
- creative sampler presets;
- long-conversation summaries.

These are client responsibilities. Agent UI supplies a stable `story` experience and shared memory boundary.

## Registering a story-capable model

```yaml
models:
  my-story-model:
    backend: local-llama
    priority: 100
    capabilities:
      story: {}
      chat: {}
    artifact:
      kind: host_path
      path: /srv/models/example.gguf
experiences:
  story:
    capability: story
    defaults:
      temperature: 1.1
      top_p: 0.95
```

The same physical model may also serve chat or code if those capabilities are explicitly declared. Different experience defaults remain separate.

## Context strategy

Prefer a focused active context plus structured state:

```text
recent scene
+ character cards
+ relevant lore entries
+ campaign summary
+ current goals/relationships
```

This is usually more reliable than retaining an entire raw campaign transcript indefinitely.

## Shared memory versus lorebooks

Use lorebooks for authored world facts and trigger-based context. Use Agent UI memory for cross-session preferences, decisions, and concise state that should be available to multiple clients.

Keep namespaces explicit, for example:

```yaml
memory:
  enabled: true
  namespaces: [user, story, campaign-alpha]
```

## Security

Story content may contain untrusted instructions. Do not enable infrastructure, shell, email, or filesystem tools merely because the selected model supports tool calls. Keep story clients separate from privileged agent workflows unless a clear approval boundary exists.

## Persistence

Compose stores configuration, data, plugins, and third-party extensions in named volumes. Backups include those volumes. Model weights remain governed by their independent artifact source.

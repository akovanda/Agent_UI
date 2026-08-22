# Optional Agent Harness

Hermes is an optional agent client around Agent UI's `agent` experience. It is not the primary UI and is not required for ordinary chat, code, story, image, embedding, or reranking workloads.

## Start

```bash
./hub up --agent
./hub ports
```

The configuration renderer points Hermes at:

```text
http://gateway:8000/v1
```

with model `agent`. The gateway selects the highest-priority enabled model that declares the `agent` capability.

## Model requirements

Register only models that reliably support the tool-call format used by their backend:

```yaml
models:
  my-agent-model:
    backend: local-llama
    capabilities:
      agent: {}
      chat: {}
    features:
      tools: true
      structured_output: true
```

The `tools: true` declaration is descriptive, not a permission grant.

## Tool policy

Recommended rollout:

1. Read-only web/search and document tools.
2. Read-only infrastructure inspection.
3. Narrow write operations with explicit human approval.
4. Destructive operations behind a second approval or separate credential.
5. Continuous audit review and no-progress circuit breakers.

The generated Hermes configuration enables hard stops for repeated exact failures and repeated idempotent no-progress calls.

## Filesystem and host access

The reference container does not mount the Docker socket, host root, SSH credentials, or project directories into Hermes. Add those capabilities only intentionally and document ownership and approval boundaries.

A model artifact bind mount into llama.cpp does not make that host directory available to Hermes.

## Memory

Hermes maintains its own sessions, skills, and memory under its data volume. Agent UI can also inject shared scoped memory into the `agent` experience. Keep the roles distinct:

- Hermes skills: executable procedures and tool use;
- Hermes session memory: agent-specific continuity;
- Agent UI memory: cross-client user/project facts;
- catalog: deployment configuration, never conversational memory.

## Reasoning effort

Hermes may send an effort value if the selected model declares reasoning support. The gateway maps the stable value through the model feature declaration. Do not assume a high effort level is always better for tool loops; it can increase latency and token use substantially.

## API and dashboard

Compose exposes separate random loopback ports for:

- Hermes OpenAI-compatible API;
- Hermes dashboard.

The dashboard uses generated basic-auth credentials. Loopback binding remains the default; use a trusted VPN or authenticated reverse proxy for remote access.

## Multiple agent models

Agent UI can register multiple models with the `agent` capability. Priority determines the unpinned default. Pin a separate experience when different tool environments require deterministic model identity:

```yaml
experiences:
  infrastructure-agent:
    model: infrastructure-model
    capability: agent
```

## Security boundary

Persistent catalog changes remain outside Hermes. An agent request cannot register a host path, change a backend URL, or rewrite model permissions through the gateway API. A setup agent must use the `./hub catalog plan/apply` provisioning plane with operator approval.

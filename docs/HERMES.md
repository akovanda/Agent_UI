# Hermes Agent integration

Hermes is an optional agent layer, not the main UI and not the inference engine. Its upstream
provider calls the hidden `hermes-agent` profile through the authenticated Local AI Hub gateway.
That profile uses GPT-OSS 20B with high reasoning effort and disables the gateway's own memory
injection so Hermes remains the source of truth for its agent memory and skills.

## Start

Initialize the deployment and start the agent profile:

```bash
./hub init
./hub up --agent
./hub ports
```

The toolbox renders `/opt/data/config.yaml` and `SOUL.md` into the Docker-managed Hermes volume
named by `HERMES_VOLUME`. The official Hermes container then owns all mutable state under
`/opt/data`, including profiles, sessions, memory, skills, logs, and credentials. Replacing or
upgrading the container does not replace that volume.

The generated provider configuration is equivalent to:

```yaml
model:
  default: hermes-agent
  provider: custom
  base_url: http://gateway:8000/v1
  api_key: <GATEWAY_API_KEY>
  context_length: 65536
```

Do not edit generated files in the volume as the normal configuration workflow. Change the model
catalog, Hermes SOUL file, or renderer in Git, then run `./hub model render` and recreate Hermes.

## Open WebUI connection

Open WebUI's default connection goes directly to the Local AI Hub gateway for ordinary chat. Add a
second OpenAI-compatible connection when Hermes should be selectable from the same UI:

```text
Base URL: http://hermes:8642/v1       (from another Compose service)
          or the host URL printed by ./hub ports, ending in /v1
API key:  HERMES_API_KEY from .env
```

The Hermes API and dashboard bind to randomized loopback host ports. They are not public unless
`BIND_ADDRESS` or an external proxy is deliberately changed.

## Initial privilege policy

Start with:

- model calls;
- Hermes memory and skills;
- read-only file access inside a dedicated workspace;
- non-authenticated public web search only when configured.

Do not initially provide:

- the host Docker socket;
- the host root filesystem;
- unrestricted SSH keys;
- GitHub write credentials;
- email or calendar write credentials;
- production API tokens;
- home-automation controls.

Add capabilities incrementally with tests and approvals. Generated configuration enables hard-stop
circuit breakers after five repeated exact failures or five idempotent calls without progress, so
an unattended gateway does not merely warn while continuing an agent loop.

## Tool tiers

| Tier | Examples | Default policy |
|---|---|---|
| 0: reasoning only | model response, local memory | automatic |
| 1: read-only | read repository, inspect logs, query monitoring | automatic within allowlist |
| 2: reversible write | create branch, draft file, create local artifact | explicit session approval |
| 3: external write | PR comment, email, calendar, deployment | per-action approval |
| 4: privileged/destructive | merge, delete, production restart, Docker socket, root | deny by default |

A model's assertion that an action is safe is not policy evidence.

## T4 context caveat

The Hermes profile requests a 65,536-token context, but llama.cpp automatic fitting may reduce the
usable context to remain inside the T4's memory budget. Check the actual llama.cpp startup log and
benchmark the target host. If the requested window cannot be maintained, test KV-cache changes or
additional CPU MoE offload one variable at a time.

## Sandbox strategy

Preferred terminal order:

1. dedicated persistent sandbox container;
2. constrained SSH account on a non-production host;
3. narrowly mounted repository workspace;
4. host execution only for a documented, approved procedure.

Avoid mounting `/var/run/docker.sock`; access to that socket is effectively root-equivalent access
to the Docker host.

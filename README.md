# Agent UI

Agent UI is a model-agnostic, self-hosted interface and control plane for local and remote AI. It combines a capability registry, OpenAI-compatible gateway, Docker Compose deployment, Kubernetes/Helm deployment, shared memory, specialized human interfaces, and an optional agent runtime.

**Version 0.3 does not prescribe or bundle any model.** Models become useful only after an operator or setup agent registers what is actually available on the machine.

## What it is for

Agent UI gives one installation explicit paths for several workloads:

| Workload | Default human interface | Gateway path | Registry capability |
|---|---|---|---|
| General chat and analysis | Open WebUI | `/v1/chat/completions` | `chat` |
| Coding and repository work | Open WebUI or an agent client | `/v1/chat/completions` using profile `code` | `chat`, preferably `code`/`tools`/`reasoning` |
| Stories, roleplay, and worldbuilding | SillyTavern | `/v1/chat/completions` using profile `story` | `chat`, preferably `story`/`long_context` |
| Vision | Open WebUI | `/v1/chat/completions` using profile `vision` | `chat` + `vision` |
| Image generation | Open WebUI or any OpenAI client | `/v1/images/generations` | `image` |
| Embeddings | RAG clients and services | `/v1/embeddings` | `embedding` |
| Reranking | Retrieval clients | `/v1/rerank` | `rerank` |
| Tool-using agents | Hermes or another agent client | `/v1/chat/completions` using profile `agent` | `chat` + `tools` |

A profile describes a workload. A model advertises capabilities. The gateway binds the profile to the highest-priority compatible model at request time.

## Design goals

- **No model-name logic.** Routing, reasoning, tools, and prompt behavior come from registry metadata.
- **Use models where they already live.** Docker can mount an existing host directory read-only; copying to a managed volume is optional.
- **AI-first setup.** The registry is declarative YAML with a JSON Schema, machine-readable plan output, and read-only discovery APIs.
- **Human-first runtime.** Once configured, people use Open WebUI, SillyTavern, Hermes, or another OpenAI-compatible client.
- **Capability negotiation.** Reasoning effort is forwarded only through a transport the selected model declares.
- **One contract across platforms.** Compose and Helm consume the same providers, sources, models, profiles, and routes.
- **Private by default.** Compose binds to loopback and does not publish model weights or runtime secrets.

## Architecture

```text
 Open WebUI ───────┐
 SillyTavern ──────┼────► Agent UI Gateway ─────► registered providers
 Hermes / clients ─┘          │                         │
                              │                         ├─ llama.cpp / GGUF
                              │                         ├─ OpenAI-compatible text
                              │                         ├─ image generation
                              │                         ├─ embeddings / reranking
                              │                         └─ future adapters
                              │
                              └──── PostgreSQL + pgvector memory
```

The gateway exposes only profiles that currently resolve. An empty installation is valid: `/api/capabilities` explains which profiles are unbound and why.

## Quick start with Docker Compose

Requirements:

- Docker Engine
- Docker Compose v2
- NVIDIA Container Toolkit when the local llama.cpp provider should use an NVIDIA GPU

```bash
git clone https://github.com/akovanda/Agent_UI.git
cd Agent_UI
./hub init
./hub registry plan
```

`./hub init` allocates persistent high host ports, creates secrets, builds the toolbox image, creates Docker volumes, and renders an empty but valid runtime registry.

### Register a GGUF in place

A model can remain in a shared directory, a project directory, or any other existing path:

```bash
./hub model register my-chat-model \
  --provider local-gguf \
  --host-path /mnt/shared/models/my-chat-model.gguf \
  --capability chat \
  --capability code \
  --capability tools \
  --runtime ctx-size=32768 \
  --runtime n-gpu-layers=all \
  --reasoning-transport flat \
  --reasoning-level low,medium,high \
  --developer-role \
  --tool-calling
```

That command registers the parent directory as a read-only source and generates a Compose override such as:

```yaml
services:
  llama:
    volumes:
      - type: bind
        source: /mnt/shared/models
        target: /model-sources/host-my-chat-model
        read_only: true
        bind:
          create_host_path: false
```

The file is not copied.

For several models in one directory, register the source once:

```bash
./hub source add shared-models \
  --host-path /mnt/shared/models

./hub model register chat-a \
  --provider local-gguf \
  --source shared-models \
  --path team-a/chat-a.gguf \
  --capability chat

./hub model register story-b \
  --provider local-gguf \
  --source shared-models \
  --path projects/story-b.gguf \
  --capability chat \
  --capability story \
  --capability long_context
```

### Use managed storage instead

Managed copying remains available when portability is more important than avoiding duplication:

```bash
./hub model register portable-model \
  --provider local-gguf \
  --source managed \
  --path portable-model.gguf \
  --capability chat

./hub model import portable-model /path/to/portable-model.gguf
```

### Register a remote or separately hosted provider

Any OpenAI-compatible provider can be registered without a local artifact:

```bash
./hub provider add image-provider \
  --type openai_compatible \
  --base-url http://image-service:8080/v1 \
  --api-key-env IMAGE_PROVIDER_KEY \
  --endpoint image=images/generations

./hub model register local-image \
  --provider image-provider \
  --upstream-model diffusion-model \
  --capability image
```

Add `IMAGE_PROVIDER_KEY=...` to the private `.env` file. The gateway container receives the complete environment file, while the registry stores only the environment-variable name.

### Inspect and start

```bash
./hub registry validate
./hub registry plan
./hub model list
./hub doctor
./hub up
./hub ports
```

Optional services:

```bash
./hub up --agent --observability
```

## Generic workload profiles

The checked-in registry defines workload profiles but no concrete model bindings:

- `auto`
- `chat`
- `chat-fast`
- `chat-deep`
- `code`
- `story`
- `vision`
- `image`
- `embedding`
- `rerank`
- `agent`

Profiles can be selected directly as the OpenAI `model` value. Direct registered model IDs are also accepted when a client needs to bypass profile selection.

The automatic profile uses deterministic registry rules. Control prefixes such as `/code`, `/story`, and `/vision` are stripped before the request reaches the model.

## Reasoning and effort

Reasoning support is declared per model, not inferred from its name. Four transports are supported:

```yaml
features:
  reasoning:
    transport: flat          # payload.reasoning_effort
    field: reasoning_effort
    levels: [low, medium, high]
```

```yaml
features:
  reasoning:
    transport: object        # payload.reasoning.effort
    field: reasoning
    levels: [low, high]
```

```yaml
features:
  reasoning:
    transport: chat_template # payload.chat_template_kwargs[field]
    field: reasoning_effort
    levels: [low, medium, high]
```

```yaml
features:
  reasoning:
    transport: none
```

Clients can request effort with `reasoning_effort`, `reasoning.effort`, or `X-Reasoning-Effort`. Explicit unsupported requests fail clearly. A profile preference such as `chat-deep: high` is ignored when the selected model does not support reasoning, allowing the profile to remain usable.

## Declarative setup for humans and agents

The local overlay is stored in the Docker volume mounted at `/state/registry.local.yaml`. It can be managed entirely through commands or applied as one manifest:

```bash
./hub registry schema > registry.schema.json
./hub registry apply my-machine.yaml
./hub registry plan
```

Machine-readable runtime discovery:

```text
GET /api/registry/schema        public schema
GET /api/registry               effective registry
GET /api/capabilities           resolved and unresolved profiles
POST /api/routes/preview        deterministic route preview
POST /api/admin/reload-registry reload after an external edit
```

Mutating the registry is intentionally a local control-plane operation rather than an unauthenticated web API. A setup agent can generate and apply a manifest; a human uses the UI afterward.

## Kubernetes and Helm

The generic chart is in `deploy/helm/agent-ui`.

```bash
./hub k8s render
./hub k8s install
```

Host paths do not automatically translate to Kubernetes. Each host-backed source used by a local model must declare one Kubernetes mapping:

- existing PVC
- chart-created PVC
- NFS
- CSI
- explicit `hostPath`

Example source overlay:

```yaml
version: 2
sources:
  shared-models:
    type: host
    host_path: /mnt/shared/models
    mount_path: /model-sources/shared-models
    read_only: true
    kubernetes:
      type: existingClaim
      claimName: shared-models-pvc
```

`./hub registry k8s-values` refuses to render a referenced host source that lacks an explicit Kubernetes mapping.

## Security notes

- Host model sources are mounted read-only by default.
- Docker bind mounts use `create_host_path: false` to catch typos rather than creating empty directories.
- Provider secrets are referenced by environment-variable name and are not written into the registry.
- Gateway mutation APIs are not exposed; registry changes happen through the local control plane.
- Compose binds services to `127.0.0.1` unless explicitly changed.
- Model weights, `.env`, generated overlays, and backups are gitignored.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/MODEL_MANAGEMENT.md](docs/MODEL_MANAGEMENT.md), [docs/PURE_DOCKER.md](docs/PURE_DOCKER.md), and [docs/KUBERNETES.md](docs/KUBERNETES.md).

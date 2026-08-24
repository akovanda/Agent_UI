# Agent UI

Agent UI is an open-source, local-first control plane for running **the models and inference services you already have** behind stable, human-friendly interfaces.

It does not prescribe a particular model family. Operators register resources declaratively, while human users work through Open WebUI, an optional story workspace, an optional agent workspace, or any OpenAI-compatible client.

## What v0.3 provides

- A model-neutral catalog for local weights and remote inference APIs.
- Stable experiences such as `chat`, `code`, `story`, `image`, and `agent`.
- Capability-based selection instead of model-name conditionals.
- Arbitrary reasoning/effort mappings declared per model.
- OpenAI-compatible chat, Responses, completions, infill, embeddings, reranking, and image-generation routes.
- Existing-file registration without copying weights.
- Existing Docker-volume, container-path, Kubernetes-PVC, and Kubernetes-hostPath sources.
- Managed import/download as an option, not a requirement.
- Generated Compose mount overrides and generated Helm values from the same catalog.
- An AI-friendly CLI and JSON/YAML APIs for setup and inspection.
- Shared PostgreSQL/pgvector memory with explicit user and namespace isolation.
- Optional story, agent, private web-search, and observability services.

## Architecture

```text
Human interfaces                         AI/operator setup
────────────────────────────────         ─────────────────────────────
Open WebUI                               ./hub catalog plan/apply
Story workspace (optional)               ./hub model discover/link
Agent workspace (optional)               ./hub model register
Any OpenAI-compatible client             JSON Schema + YAML overlays
             │                                          │
             └────────────────┬─────────────────────────┘
                              ▼
                      Agent UI Gateway
                 capability + effort routing
                    memory + policy boundary
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
        local llama.cpp   OpenAI-compatible   image API
        mounted weights   local/remote API    local/remote
```

Model storage, inference backends, registered models, and user experiences are separate resources. A model may live in a shared host directory, inside a project, in an existing Docker volume, on a Kubernetes PVC, or behind an already-running API.

## Empty installation is valid

The repository ships with **no model identities and no model weights**. The stack starts in `setup_required` state and exposes:

```text
GET /health
GET /api/setup/status
GET /api/catalog
GET /v1/models
```

This makes first-run setup deterministic for automation rather than forcing users to edit source files or copy multi-gigabyte artifacts before the control plane can start.

## Quick start with Docker Compose

Prerequisites:

- Docker Engine
- Docker Compose v2
- NVIDIA Container Toolkit when the local backend uses an NVIDIA GPU

Initialize secrets, stable high loopback ports, named volumes, and generated runtime configuration:

```bash
git clone https://github.com/akovanda/Agent_UI.git
cd Agent_UI
./hub init
```

Inspect files you already have:

```bash
./hub model discover /srv/models --recursive --capability chat
```

Register an existing GGUF **in place**:

```bash
./hub model link my-text-model /srv/models/example-model.gguf \
  --backend local-llama \
  --capability chat \
  --capability code \
  --capability story \
  --runtime ctx-size=32768 \
  --runtime n-gpu-layers=auto
```

No copy is made. Agent UI writes a catalog overlay and generates a read-only Compose bind mount at a stable container path.

Start the default human interface:

```bash
./hub up
./hub ports
```

Optional surfaces:

```bash
./hub up --story
./hub up --agent
./hub up --web-search
./hub up --terminal
./hub up --tools
./hub up --story --agent --tools --observability
```

`--tools` enables the recommended Open WebUI tool baseline: private web search and URL fetching,
the isolated Open Terminal scratch environment, browser-based code interpretation, memories,
notes, tasks, calendar, and the existing PostgreSQL/pgvector knowledge retrieval.

## AI-first setup

The preferred automation contract is a version-2 catalog overlay validated by [schemas/catalog-v2.schema.json](schemas/catalog-v2.schema.json).

Plan without changing state:

```bash
./hub catalog plan examples/catalog/host-path-text-model.yaml
```

Apply and regenerate mounts/runtime configuration:

```bash
./hub catalog apply my-installation.yaml
```

Inspect the fully merged result:

```bash
./hub catalog show --json
./hub model list
./hub doctor --gpu
```

An AI setup agent can therefore:

1. Discover candidate files or endpoints.
2. Ask the operator which resource to register.
3. Produce a schema-valid overlay.
4. Run `catalog plan` and present the exact changes.
5. Apply only after approval.
6. Start the stack and verify `/api/setup/status`.

The live model remains behind an authenticated gateway; the human does not need to interact with provisioning internals during normal use.

## Experiences and capabilities

An **experience** is a stable name in a user interface. A **model** is an operator-registered deployment. Experiences select the highest-priority enabled model that declares the required capability.

Built-in experience templates are:

| Experience | Capability | Typical surface |
|---|---|---|
| `chat` | `chat` | Open WebUI or API |
| `code` | `code` | Open WebUI, IDE, completions, or infill |
| `story` | `story` | Open WebUI or optional story workspace |
| `image` | `image` | Open WebUI or `/v1/images/generations` |
| `agent` | `agent` | Open WebUI tools or optional agent workspace |
| `embeddings` | `embeddings` | RAG/indexing clients |
| `rerank` | `rerank` | Retrieval pipelines |

Capabilities are extensible strings. Future deployments can add vision, speech, music, video, specialized scientific inference, or private application-specific capabilities without changing the gateway schema.

## Reasoning and effort controls

Reasoning support is declared per model rather than inferred from its name:

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

Clients may set:

```text
X-Reasoning-Effort: deep
```

or send `reasoning_effort` in the request body. Values may be passed through unchanged or translated to any upstream representation, including `chat_template_kwargs`.

## Model locations

Supported artifact kinds:

- `managed` — copy or download into Agent UI's named model volume.
- `host_path` — bind-mount an existing host file or directory read-only.
- `docker_volume` — attach an existing named Docker volume.
- `container_path` — use a path mounted by an operator-provided Compose override.
- `pvc` — attach an existing Kubernetes PersistentVolumeClaim.
- `hostPath` — explicit node-local Kubernetes path, with the usual scheduling constraints.
- `none` — model is served by an API and has no local artifact.

See [docs/MODEL_SOURCES.md](docs/MODEL_SOURCES.md).

## Backends

The v0.3 contract supports:

- managed `llama.cpp` router mode for local GGUF models;
- arbitrary OpenAI-compatible local or remote services;
- image services exposed through an OpenAI-compatible image endpoint;
- a declarative `comfyui` backend contract for workflow adapters.

Backend credentials are referenced by environment-variable name. Secret values are never stored in the catalog or returned from `/api/catalog`.

Examples are in [examples/catalog](examples/catalog).

## Kubernetes

The same catalog can generate Helm values:

```bash
./hub k8s render
./hub k8s install
```

External cluster storage can be declared as an existing PVC or explicit hostPath. Generated values populate `llama.extraVolumes` and `llama.extraVolumeMounts`; advanced operators may supply arbitrary CSI, object-store, NFS, or secret-store volumes through those Kubernetes-native fields.

See [docs/KUBERNETES.md](docs/KUBERNETES.md).

## Security defaults

- Host ports bind to `127.0.0.1` by default.
- Ports are randomly allocated once and persisted in `.env`.
- Gateway, inference, agent, and UI keys are generated during initialization.
- External model mounts are read-only unless explicitly changed.
- Catalogs reference secret environment-variable names, not values.
- Retrieved memory is labeled as untrusted reference material.
- Tool-using agents remain optional and should start with read-only tools.

Do not expose the stack directly to the public internet. Use a trusted VPN, authenticated reverse proxy, or SSH tunnel.

## Operations

```bash
./hub status
./hub logs gateway
./hub smoke
./hub test
./hub lint
./hub backup
./hub restore BACKUP_DIR --yes
```

Managed volumes are included in normal backups. External host paths, Docker volumes, PVCs, and endpoints remain independently owned; the backup manifest records their references but does not silently copy them.

## Documentation

- [Catalog and AI setup](docs/CATALOG.md)
- [Model source and mount behavior](docs/MODEL_SOURCES.md)
- [Reasoning and feature declarations](docs/FEATURES.md)
- [Docker deployment](docs/PURE_DOCKER.md)
- [Kubernetes deployment](docs/KUBERNETES.md)
- [Gateway API](docs/API.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Security](docs/SECURITY.md)
- [Testing](docs/TESTING.md)

## Project status

v0.3 is the generic-platform release line. It intentionally favors explicit, inspectable configuration over hidden model-name heuristics. Contributions should preserve zero-model startup, schema compatibility, deterministic dry runs, and the separation between provisioning automation and live human interfaces.

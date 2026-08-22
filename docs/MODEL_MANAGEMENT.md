# Model, Provider, and Source Management

Agent UI 0.3 separates four concepts:

1. **Source** — where a local artifact is mounted.
2. **Provider** — the inference endpoint and protocol.
3. **Model** — an upstream model plus capabilities and feature metadata.
4. **Profile** — a human workload such as chat, code, story, image, or agent.

This separation lets one model serve several profiles and lets one profile move between models without changing the UI.

## Effective registry

The immutable base registry is baked into the toolbox image at:

```text
/opt/agent-ui/config/registry.yaml
```

Machine-specific changes are stored in the persistent state volume at:

```text
/state/registry.local.yaml
```

The control plane deep-merges the overlay into the base and validates the result with the same Pydantic schema used by the gateway.

```bash
./hub registry show
./hub registry validate
./hub registry plan
./hub registry schema
```

`plan` is JSON by default and reports:

- provider status and protocol
- registered model capabilities
- the model selected for every profile
- profiles that cannot currently resolve
- source mount configuration

## Sources

### Managed source

The base registry defines `managed`, mapped to the Docker volume at `/models`.

```bash
./hub model register portable \
  --provider local-gguf \
  --source managed \
  --path portable.gguf \
  --capability chat

./hub model import portable /downloaded/portable.gguf
```

This copies data and is intentionally optional.

### Existing host directory

```bash
./hub source add shared \
  --host-path /mnt/shared/models
```

This writes only registry metadata. The generated Compose override mounts the directory read-only:

```text
/mnt/shared/models -> /model-sources/shared
```

Register files relative to that directory:

```bash
./hub model register coding \
  --provider local-gguf \
  --source shared \
  --path coding/coding.gguf \
  --capability chat \
  --capability code
```

### Existing individual file

The convenience form creates a dedicated source for the parent directory:

```bash
./hub model register campaign-writer \
  --provider local-gguf \
  --host-path /srv/projects/campaign/models/writer.gguf \
  --capability chat \
  --capability story \
  --capability long_context
```

The weight remains in the project directory.

### Source safety

- Host paths must be absolute existing directories.
- Host sources are read-only unless `--writable` is explicitly supplied.
- Docker is instructed not to create a missing bind source.
- Artifact paths must be relative and may not contain `..`.
- Model weights remain outside Git.

## Providers

A provider describes an API, not a model family.

### Local llama.cpp provider

The base registry supplies `local-gguf`:

```yaml
providers:
  local-gguf:
    type: llama_cpp
    base_url: http://llama:8080/v1
    control_url: http://llama:8080
    api_key_env: LLAMA_API_KEY
    resource_group: local-gpu
    max_concurrency: 1
```

`llama_cpp` adds explicit model load/unload coordination. The gateway serializes transitions inside the provider's `resource_group` and can keep one model resident on a constrained GPU.

### OpenAI-compatible provider

```bash
./hub provider add text-service \
  --type openai_compatible \
  --base-url http://text-service:8000/v1 \
  --api-key-env TEXT_SERVICE_KEY \
  --endpoint chat=chat/completions \
  --endpoint embedding=embeddings
```

The registry stores `TEXT_SERVICE_KEY`, not its value. Add the value to `.env` or a Kubernetes Secret-backed environment entry.

A provider can expose one or several endpoint types:

- `chat`
- `completion`
- `image`
- `embedding`
- `rerank`

## Models

A model registration can refer to a local artifact or only to an upstream model identifier.

```bash
./hub model register general \
  --provider text-service \
  --upstream-model upstream-name \
  --capability chat \
  --capability code \
  --capability tools \
  --tag general \
  --priority 20
```

Capabilities are open strings. The built-in workload profiles use:

- `chat`
- `code`
- `story`
- `long_context`
- `vision`
- `image`
- `embedding`
- `rerank`
- `tools`
- `reasoning`
- `fast`

Additional capabilities are preserved and visible through `/api/capabilities`.

### Runtime arguments for llama.cpp

Runtime key/value pairs are copied into the generated llama.cpp preset:

```bash
./hub model register local-chat \
  --provider local-gguf \
  --host-path /models/local-chat.gguf \
  --capability chat \
  --runtime ctx-size=65536 \
  --runtime n-gpu-layers=all \
  --runtime cache-type-k=q8_0 \
  --runtime cache-type-v=q8_0
```

Agent UI does not infer safe settings from a model's name. Operators or setup agents declare them explicitly and validate on the target hardware.

## Reasoning support

Reasoning is feature metadata:

```bash
./hub model register reasoning-model \
  --provider text-service \
  --upstream-model reasoning-upstream \
  --capability chat \
  --capability reasoning \
  --reasoning-transport object \
  --reasoning-field reasoning \
  --reasoning-level low,medium,high
```

Supported transports:

| Transport | Upstream shape |
|---|---|
| `flat` | `{"reasoning_effort":"high"}` |
| `object` | `{"reasoning":{"effort":"high"}}` |
| `chat_template` | `{"chat_template_kwargs":{"reasoning_effort":"high"}}` |
| `none` | effort is not forwarded |

A client may use the flat field, nested object, or `X-Reasoning-Effort`. Unsupported explicit requests return a clear 400 response. Profile preferences are best-effort.

## Profile selection

Profiles use selectors rather than model names:

```yaml
profiles:
  code:
    endpoint: chat
    requires:
      all_of: [chat]
      prefer_capabilities: [code, tools, reasoning]
```

Selection order is deterministic:

1. Reject disabled models or providers.
2. Require the endpoint capability.
3. Apply `all_of`, `any_of`, `none_of`, and required tags.
4. Rank preferred capabilities and tags.
5. Apply operator priority.
6. Break ties by model ID.

A profile can be pinned when needed:

```bash
./hub profile bind code local-chat
./hub profile unbind code
```

Direct model IDs can also be sent as the OpenAI `model` field.

## Declarative registration

A setup agent can write one overlay:

```yaml
version: 2
sources:
  shared:
    type: host
    host_path: /mnt/shared/models
    mount_path: /model-sources/shared
    read_only: true
providers:
  local-text:
    type: llama_cpp
    base_url: http://llama:8080/v1
    control_url: http://llama:8080
    api_key_env: LLAMA_API_KEY
models:
  workstation-chat:
    provider: local-text
    capabilities: [chat, code, tools, reasoning]
    artifact:
      source: shared
      path: workstation/chat.gguf
    runtime:
      ctx-size: 32768
      n-gpu-layers: all
    features:
      developer_role: true
      tool_calling: true
      reasoning:
        transport: flat
        field: reasoning_effort
        levels: [low, medium, high]
```

Apply and inspect it:

```bash
./hub registry apply machine.yaml
./hub registry validate
./hub registry plan
```

## Kubernetes source mappings

A host source must state how Kubernetes can access the same bytes:

```yaml
sources:
  shared:
    type: host
    host_path: /mnt/shared/models
    mount_path: /model-sources/shared
    read_only: true
    kubernetes:
      type: existingClaim
      claimName: shared-models-pvc
```

Other mappings:

```yaml
kubernetes:
  type: nfs
  server: 10.0.0.10
  path: /exports/models
```

```yaml
kubernetes:
  type: csi
  driver: example.csi.io
  volumeAttributes:
    share: models
```

```yaml
kubernetes:
  type: hostPath
  path: /mnt/shared/models
```

`hostPath` is intentionally explicit because it couples a pod to node-local state. Rendering fails when an enabled local model references a host source without a Kubernetes mapping.

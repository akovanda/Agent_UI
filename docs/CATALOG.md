# Catalog and AI Setup

Agent UI v0.3 uses a versioned catalog to separate provisioning from live use. The checked-in base catalog contains backend and experience templates; installation-specific resources are stored in the `agent-ui-state` volume as `catalog.local.yaml`.

## Merge model

The effective catalog is a deep merge:

```text
config/models/catalog.yaml
          +
/state/catalog.local.yaml
          =
/runtime/catalog.resolved.json
```

Mappings merge recursively. A key set to `null` in an overlay removes the inherited key. Lists and scalar values replace the inherited value.

The generated runtime file is consumed by the gateway. Local llama.cpp presets and Compose mount overrides are generated from the same effective document, preventing storage and routing configuration from drifting apart.

## Top-level resources

```yaml
version: 2
backends: {}
models: {}
experiences: {}
```

### Backend

A backend describes an inference API, not a model:

```yaml
backends:
  workstation-api:
    kind: openai-compatible
    base_url: http://host.docker.internal:9000/v1
    api_key_env: WORKSTATION_API_KEY
    coordinator: none
    serialize_requests: false
```

Supported kinds are `llama.cpp`, `openai-compatible`, and `comfyui`. `base_url_env` may be used instead of embedding a deployment-specific URL. Credentials are always referenced by environment-variable name.

### Model

A model is a deployment registered against a backend:

```yaml
models:
  my-model:
    display_name: My Model
    backend: workstation-api
    upstream_model: model-name-reported-by-server
    priority: 50
    capabilities:
      chat: {}
      code: {}
    features:
      tools: true
    defaults:
      temperature: 0.4
    artifact:
      kind: none
```

`upstream_model` is the value sent to the backend. The Agent UI model ID remains stable even when the backend changes its internal name.

Model `defaults` are fallback request parameters. A request-body value takes precedence, and an
experience default takes precedence when an experience selects the model. Defaults apply to chat
and other OpenAI-compatible passthrough endpoints.

Multiple directly advertised model IDs may use the same `upstream_model` while providing different
presets. This is useful for clients whose model picker is easier to use than per-chat controls:

```yaml
models:
  my-model-fast:
    backend: workstation-api
    upstream_model: model-name-reported-by-server
    capabilities: [chat]
    defaults: {reasoning_effort: fast}
  my-model-deep:
    backend: workstation-api
    upstream_model: model-name-reported-by-server
    capabilities: [chat]
    defaults: {reasoning_effort: deep}
```

The model list may expose the stable `reasoning_default` preset. It does not expose the complete
defaults object, which may contain deployment-specific values.

### Experience

An experience is the stable ID selected by a human-facing client:

```yaml
experiences:
  code:
    capability: code
    description: Software engineering work.
    defaults:
      temperature: 0.25
```

The resolver chooses the enabled model with the highest numeric priority that declares the required capability. Pinning is also supported:

```yaml
experiences:
  code:
    model: my-model
    capability: code
```

## Plan and apply

Validate an overlay and show the resulting resource IDs without changing state:

```bash
./hub catalog plan installation.yaml
```

Apply it:

```bash
./hub catalog apply installation.yaml
```

The operation is transactional at the catalog-file level:

1. Read the current overlay.
2. Merge the requested changes.
3. Merge the candidate overlay with the checked-in base.
4. Validate all references and artifact requirements.
5. Atomically replace `catalog.local.yaml`.
6. Regenerate runtime configuration and Compose mounts.
7. Recreate inference and gateway containers when they are already running.

`--replace` replaces the installation overlay instead of merging it. `--dry-run --json` is intended for automation.

## AI setup protocol

A setup agent should follow this sequence:

1. Run `./hub catalog show --json` and `./hub model list`.
2. Discover candidate local files with `./hub model discover PATH --recursive`.
3. Ask the operator which files/endpoints and capabilities should be registered.
4. Produce a version-2 overlay conforming to `schemas/catalog-v2.schema.json`.
5. Run `./hub catalog plan FILE` and present the diff in resource terms.
6. Apply only after approval.
7. Start or update the stack.
8. Query `/api/setup/status` and run a capability-specific smoke request.

The agent should never infer that an arbitrary file is safe, licensed, or appropriate merely because discovery found it. Model selection, license acceptance, network credentials, writable mounts, and tool permissions require explicit operator intent.

## Read-only runtime APIs

Authenticated operators and automation can inspect:

```text
GET /api/catalog
GET /api/setup/status
GET /v1/models
POST /api/routes/preview
POST /api/admin/reload-catalog
```

The HTTP API does not mutate the persistent catalog. Provisioning writes remain in the `./hub` control plane so an exposed chat gateway cannot be prompted into mounting host paths or changing credentials.

## Direct registration commands

For simple cases the CLI can write a catalog overlay without a hand-authored YAML file:

```bash
./hub model register my-model \
  --backend local-llama \
  --source-kind host_path \
  --path /srv/models/example.gguf \
  --capability chat \
  --capability code \
  --runtime ctx-size=32768
```

`./hub model link` is shorthand for `artifact.kind=host_path`. `./hub model import` is intentionally limited to models declared with `artifact.kind=managed`.

## Schema evolution

The v2 document is validated both by application models and the published JSON Schema. Additive metadata is permitted in model capabilities and runtime options. Breaking semantic changes require a new catalog version and an explicit migration tool.

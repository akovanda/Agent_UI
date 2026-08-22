# Pure-Docker Deployment

Docker Compose is the reference deployment for Agent UI v0.3. The host requires Docker Engine, Docker Compose v2, and an appropriate GPU container runtime when local inference uses a GPU. Python, Helm, kubectl, PostgreSQL clients, and model-management libraries run inside the toolbox image.

## Initialize

```bash
./hub init
```

Initialization:

- builds the toolbox;
- creates `.env` with cryptographically random secrets;
- selects unique unused host ports from `40000–60999`;
- creates explicitly named volumes;
- validates the model-neutral base catalog;
- generates runtime configuration;
- generates `.agent-ui/compose.generated.yaml`.

Ports are bound to `127.0.0.1` by default and remain stable after initialization.

## Register resources before or after startup

An empty catalog is valid. Register an existing model in place:

```bash
./hub model link my-model /srv/models/example.gguf \
  --capability chat \
  --capability code \
  --runtime ctx-size=32768
```

Register a model inside a project directory:

```bash
./hub model link project-model "$HOME/src/project/models" \
  --filename example.gguf \
  --capability story
```

Register an endpoint with a YAML overlay:

```bash
./hub catalog plan examples/catalog/openai-compatible-endpoint.yaml
./hub catalog apply examples/catalog/openai-compatible-endpoint.yaml
```

Catalog changes regenerate runtime files and recreate the local inference/gateway services when they are already running.

## Generated Compose overrides

The base `compose.yaml` cannot know arbitrary host paths in advance. Agent UI generates a local override:

```text
.agent-ui/compose.generated.yaml
```

For `artifact.kind=host_path`, it adds a read-only bind mount. For `docker_volume`, it declares an external named volume. The `./hub` wrapper automatically runs:

```text
docker compose -f compose.yaml -f .agent-ui/compose.generated.yaml ...
```

Do not commit the generated file. It contains installation-specific paths.

## Start surfaces

Default:

```bash
./hub up
```

This starts PostgreSQL, configuration rendering, local llama.cpp, the gateway, and Open WebUI.

Optional story workspace:

```bash
./hub up --story
```

Optional agent workspace:

```bash
./hub up --agent
```

Optional metrics:

```bash
./hub up --observability
```

Options can be combined.

## External endpoints on the Docker host

The gateway receives:

```text
host.docker.internal → host-gateway
```

A host service may therefore be registered as:

```yaml
base_url: http://host.docker.internal:9000/v1
```

On Linux this mapping is added explicitly by Compose. Services on another host use their normal private address or DNS name.

## Existing Docker volumes

```yaml
models:
  shared-model:
    backend: local-llama
    capabilities: [chat]
    artifact:
      kind: docker_volume
      volume: organization-model-cache
      sub_path: example.gguf
```

The referenced volume is `external: true`; Agent UI does not delete it during `./hub destroy --yes`.

## Managed model volume

Use managed storage when a self-contained copy is desirable:

```yaml
artifact:
  kind: managed
  filename: example.gguf
```

Then:

```bash
./hub model import MODEL_ID /path/to/example.gguf
```

or configure a source and run `./hub model fetch MODEL_ID`.

## Catalog state

Installation-specific catalog state lives in the named `HUB_STATE_VOLUME`, not in the Git checkout. Inspect it through:

```bash
./hub catalog show
./hub catalog show --json
```

The runtime copy is written to `RUNTIME_CONFIG_VOLUME` and mounted read-only into the gateway and inference service.

## Operations

```bash
./hub status
./hub logs gateway
./hub model list
./hub model render
./hub doctor --gpu
./hub smoke
./hub switch-regression
./hub benchmark
```

`/health` reports `setup_required` when no models are enabled. That state is operationally healthy for the control plane.

## Backup and restore

```bash
./hub backup
./hub backup /backup/location --include-models
./hub restore /backup/location --yes
```

Backups include database dumps, catalog state, gateway state, UI state, story state, and agent state. Managed weights are optional because of size. External paths and volumes remain independently owned and are recorded only as catalog references.

After restore, verify every external source before accepting traffic.

## Direct Docker Compose use

The `./hub` wrapper is preferred because it renders the catalog and includes the generated mount file. For debugging, inspect the exact model:

```bash
./hub compose config
./hub compose ps
```

Running `docker compose` directly without the generated override may omit external model mounts.

## Networking and exposure

The backend network permits outbound access for registered remote endpoints. Host ports remain loopback-only unless `BIND_ADDRESS` is deliberately changed. Use a trusted VPN, SSH tunnel, or authenticated TLS reverse proxy for remote access. Never expose unauthenticated inference, story, or agent services directly to the public internet.

## Destruction

```bash
./hub destroy --yes
```

This removes Agent UI-managed containers, generated files, and managed volumes. It does not remove independent host files or external Docker volumes.

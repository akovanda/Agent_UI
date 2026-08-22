# Model Management

Agent UI v0.3 manages **registrations**, not a prescribed model library. Model weights remain independently licensed and owned by the operator.

## Discover

```bash
./hub model discover /srv/models --recursive --capability chat
```

Discovery reports GGUF files, sizes, stable-ID suggestions, and a registration skeleton. It does not modify the catalog and does not assert suitability or licensing.

## Link an existing path

```bash
./hub model link my-model /srv/models/example.gguf \
  --backend local-llama \
  --capability chat \
  --capability code \
  --priority 100 \
  --runtime ctx-size=32768
```

This writes `artifact.kind=host_path` and generates a read-only Compose mount. The source file remains in place.

A project-owned directory is equally valid:

```bash
./hub model link project-model "$HOME/src/project/models" \
  --filename example.gguf \
  --capability story
```

## Register a remote/local endpoint model

Use a catalog overlay:

```yaml
version: 2
backends:
  endpoint:
    kind: openai-compatible
    base_url: http://host.docker.internal:9000/v1
    api_key_env: ENDPOINT_API_KEY
models:
  endpoint-model:
    backend: endpoint
    upstream_model: server-model-name
    capabilities: [chat, code]
    artifact: {kind: none}
```

Apply:

```bash
./hub catalog plan endpoint.yaml
./hub catalog apply endpoint.yaml
```

## Managed import

First declare managed storage:

```yaml
models:
  managed-model:
    backend: local-llama
    capabilities: [chat]
    artifact:
      kind: managed
      filename: example.gguf
```

Then import:

```bash
./hub model import managed-model /path/to/example.gguf
```

Use `--force` to replace an existing managed copy. Imports are atomic.

## Managed download

```bash
./hub model fetch managed-model --url URL
```

or:

```bash
./hub model fetch managed-model \
  --repository ORGANIZATION/REPOSITORY \
  --file FILE.gguf
```

Set `HF_TOKEN` for private or gated repositories. Pin a SHA-256 checksum in the catalog or pass one to verification.

## Register arbitrary options

```bash
./hub model register my-model \
  --backend local-llama \
  --source-kind host_path \
  --path /srv/models/example.gguf \
  --capability chat \
  --capability agent \
  --runtime ctx-size=65536 \
  --runtime n-gpu-layers=auto \
  --reasoning fast=low \
  --reasoning balanced=medium \
  --reasoning deep=high
```

For advanced resources, use a schema-valid catalog overlay rather than adding more CLI flags.

## List and inspect

```bash
./hub model list
./hub catalog show --json
curl -H "Authorization: Bearer $GATEWAY_API_KEY" \
  http://127.0.0.1:GATEWAY_PORT/v1/models
```

## Verify

```bash
./hub model verify MODEL_ID
./hub model verify MODEL_ID --sha256 EXPECTED_HASH
```

Managed artifacts are visible directly to the toolbox. External host/volume mounts are verified after container creation:

```bash
./hub model render
./hub up
./hub smoke
```

## Remove

```bash
./hub model remove MODEL_ID --yes
```

This removes only a managed copy. It never deletes a host-path source, external Docker volume, PVC, hostPath directory, or remote endpoint model.

To remove a registration, apply an overlay with the model key set to `null`.

## Runtime generation

```bash
./hub model render
```

Generates:

- llama.cpp `models.ini` for enabled local models;
- `/runtime/catalog.resolved.json` for the gateway;
- `.agent-ui/compose.generated.yaml` for external mounts;
- optional agent configuration.

## Kubernetes

```bash
./hub catalog k8s-values
./hub k8s render
./hub k8s model-import MODEL_ID FILE.gguf
```

Use `artifact.kind=pvc` for an existing claim or `hostPath` for explicit node-local storage. See [MODEL_SOURCES.md](MODEL_SOURCES.md).

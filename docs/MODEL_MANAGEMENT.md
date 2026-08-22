# Model management

## Catalog as the contract

`config/models/catalog.yaml` describes model identity, canonical filename, aliases, source metadata, llama.cpp runtime settings, and virtual profiles. The generated llama.cpp preset is not hand-edited.

The base catalog defines:

- `gpt-oss-20b`
- `stheno-8b`
- `assistant`
- `assistant-fast`
- `assistant-deep`
- `storyteller`

Private additions are written to `/state/catalog.local.yaml` in the managed state volume. The overlay is merged recursively with the base catalog.

## Importing local GGUF files

```bash
./hub model import MODEL_ID /absolute/path/to/model.gguf
```

The operation:

1. Mounts the source directory read-only.
2. Streams the file into a temporary path in the model volume.
3. Flushes it to disk.
4. Atomically renames it to the canonical catalog filename.
5. Prints its SHA-256 digest.
6. Regenerates runtime configuration.
7. Restarts llama.cpp if it is already running.

Replacement requires `--force`.

## Downloading

Catalog source:

```bash
./hub model fetch stheno-8b
```

Ad hoc Hugging Face source:

```bash
HF_TOKEN=... ./hub model fetch MODEL_ID \
  --repository OWNER/REPOSITORY \
  --file EXACT-FILENAME.gguf
```

Direct URL:

```bash
./hub model fetch MODEL_ID --url https://example/model.gguf
```

Downloads use a partial file and atomic rename. Add a `sha256` field to a model or source record when a trusted digest is available.

## Registering another model

```bash
./hub model register qwen-local \
  --filename Qwen-Local-Q4_K_M.gguf \
  --display-name "Qwen Local" \
  --role general \
  --context 32768 \
  --gpu-layers auto \
  --cache-type q8_0

./hub model import qwen-local /path/to/Qwen-Local-Q4_K_M.gguf
```

Registration changes only the local overlay. It does not download or load a model. By
default it also creates an advertised gateway profile with the same id, so `qwen-local`
is immediately selectable after import and runtime rendering. Give the profile a different
name or register an inference-only backend with:

```bash
./hub model register qwen-local \
  --filename Qwen-Local-Q4_K_M.gguf \
  --profile-id qwen-chat

./hub model register embedding-backend \
  --filename Embedding-Model-Q8_0.gguf \
  --no-profile
```

Advanced sampler, memory, and system-prompt settings can be added under `profiles:` in
`config/models/catalog.local.yaml`; no Python changes are required.

## Runtime presets

The renderer creates `/runtime/models.ini` in a named volume. Relevant defaults include:

```ini
version = 1

[*]
jinja = true
fit = true
fit-target = 1024
parallel = 1
load-on-startup = false
stop-timeout = 180
```

Per-model sections point to files in `/models` and apply model-specific context, KV-cache, GPU, and MoE settings.

## T4 baseline

GPT-OSS 20B starts with:

- 65,536-token context;
- automatic GPU layer fitting;
- eight CPU-MoE layers;
- Q8 KV cache;
- one parallel slot;
- 1,024 MiB fit margin.

Stheno starts with:

- 32,768-token context;
- all GPU layers when possible;
- Q8 KV cache;
- one parallel slot.

These are operational hypotheses. Adjust only after collecting benchmark and VRAM evidence.

## Kubernetes model PVC

```bash
./hub k8s model-import MODEL_ID /path/to/model.gguf
```

The toolbox creates a temporary pod that mounts the model PVC, copies the file under the canonical catalog name, flushes it, deletes the loader pod, and restarts the llama Deployment.

For large files, `kubectl cp` is simple but not always the fastest path. On remote clusters, object storage plus an authenticated init job may be preferable.

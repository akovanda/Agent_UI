# Local AI Hub

Local AI Hub is a private, multi-model AI stack designed for a single NVIDIA GPU and a large-memory Linux server. It presents GPT-OSS 20B as the general assistant and agent model, Stheno 8B as the story and roleplay model, and gives each workload the interface that fits it:

- **Open WebUI** for general chat, files, RAG, memory, coding, and assistant profiles.
- **SillyTavern** for persistent campaigns, character cards, lorebooks, and creative generation.
- **Hermes Agent** as an optional agent runtime for tools, skills, memory, scheduling, and integrations.
- **llama.cpp router mode** as the common inference backend, with at most one model resident on the T4.
- **PostgreSQL + pgvector** for gateway memory and Open WebUI persistence.

Compose is the canonical deployment. A Helm chart maintains the same topology for Kubernetes.

## Deployment contract

The host needs only:

1. Docker Engine.
2. Docker Compose v2.
3. NVIDIA Container Toolkit for GPU use.
4. A POSIX-compatible shell to invoke `./hub`.

Python, PostgreSQL, Hugging Face CLI, Helm, kubectl, OpenSSL, and model-management utilities run inside the toolbox container. No model weights, generated secrets, databases, conversations, or runtime state are stored in Git.

```text
Browser / phone
      │
      ├── Open WebUI ───────────┐
      ├── SillyTavern ──────────┼── Local AI Hub Gateway
      └── Hermes Agent (opt.) ──┘          │
                                           ├── scoped memory / profiles
                                           ├── request serialization
                                           └── model routing
                                                    │
                                              llama.cpp router
                                                    │
                                      one model resident on the T4
                                           ┌────────┴────────┐
                                           │                 │
                                     GPT-OSS 20B        Stheno 8B
```

## Compose quick start

### 1. Initialize

```bash
./hub init
```

Initialization builds the toolbox image, generates strong local secrets, creates Docker-managed volumes, and assigns unused high loopback ports. It does **not** assume that host port 5432—or any other familiar port—is free. The selected ports are persisted in `.env` and displayed with:

```bash
./hub ports
```

All published ports bind to `127.0.0.1` by default. Containers continue to use stable internal ports such as PostgreSQL `5432`; only host mappings are randomized.

### 2. Import the two models

For an existing Stheno file:

```bash
./hub model import stheno-8b \
  /path/to/Llama-3.1-8B-Stheno-v3.4.Q4_K_M.gguf
```

The importer also accepts the common hyphenated filename and normalizes it to the catalog's canonical name.

For GPT-OSS 20B:

```bash
./hub model import gpt-oss-20b /path/to/gpt-oss-20b.gguf
```

Stheno can also be fetched from its cataloged Hugging Face source:

```bash
./hub model fetch stheno-8b
```

A GPT-OSS GGUF source can be supplied explicitly without changing code:

```bash
./hub model fetch gpt-oss-20b \
  --repository OWNER/GPT-OSS-GGUF-REPOSITORY \
  --file THE-EXACT-GGUF-FILENAME.gguf
```

Inspect and verify the managed model volume:

```bash
./hub model list
./hub model verify gpt-oss-20b
./hub model verify stheno-8b
```

### 3. Start the normal stack

```bash
./hub up
```

This starts PostgreSQL, llama.cpp, the gateway, Open WebUI, and SillyTavern. The command prints the selected URLs.

Start optional components with Compose profiles:

```bash
./hub up --agent                    # include Hermes Agent
./hub up --observability            # include Prometheus
./hub up --agent --observability    # include both
```

### 4. Complete first-login setup

Open WebUI is preconnected to the Local AI Hub gateway. The first registered user becomes its administrator. After creating the administrator account, set `OPEN_WEBUI_ENABLE_SIGNUP=false` in `.env` and restart Open WebUI:

```bash
./hub compose up -d --force-recreate open-webui
```

In SillyTavern, add an OpenAI-compatible connection:

```text
API URL: http://gateway:8000/v1
API key: the GATEWAY_API_KEY value from .env
Model:   storyteller
```

That URL is resolved inside the Compose network. From another host-side client, use the gateway URL printed by `./hub ports`.

## Daily commands

```bash
./hub status
./hub logs gateway
./hub logs llama
./hub restart gateway
./hub smoke
./hub switch-regression 25
./hub benchmark 3
./hub doctor --gpu
./hub backup /secure/backups/local-ai-hub
./hub down
```

Model operations are atomic and update the generated llama.cpp preset. When inference is already running, the control plane restarts only llama.cpp so the new catalog is applied.

```bash
./hub model register my-model \
  --filename My-Model-Q4_K_M.gguf \
  --display-name "My Model" \
  --role general \
  --context 32768

./hub model import my-model /path/to/My-Model-Q4_K_M.gguf
```

## Why one model at a time

The Tesla T4 has 16 GiB of VRAM. Stheno is small enough to fit comfortably, while GPT-OSS 20B sits close to the practical memory ceiling once context and runtime buffers are included. The router is therefore configured with `models-max = 1`:

1. Requests name a virtual profile or base model.
2. The gateway serializes the transition.
3. llama.cpp unloads the old model when necessary.
4. The requested model is loaded with its own context and offload settings.
5. The streamed response retains the GPU lease until completion.

The default GPT-OSS profile uses a 65,536-token context, quantized KV cache, automatic fit, and eight CPU-MoE layers as a conservative T4 starting point. Benchmark the actual machine and tune `config/models/catalog.yaml` rather than assuming those values are optimal.

## Kubernetes

The Helm chart lives at `deploy/helm/local-ai-hub`. Helm and kubectl are included in the toolbox image.

Render manifests:

```bash
./hub k8s render
```

Install or upgrade:

```bash
./hub k8s install \
  --set-string gateway.image.repository=registry.example/local-ai-hub-gateway \
  --set-string gateway.image.tag=0.2.0
```

Import model weights into the model PVC:

```bash
./hub k8s model-import gpt-oss-20b /path/to/gpt-oss-20b.gguf
./hub k8s model-import stheno-8b \
  /path/to/Llama-3.1-8B-Stheno-v3.4.Q4_K_M.gguf
```

Kubernetes uses ClusterIP services by default. PostgreSQL is not bound to a node port, so an existing host PostgreSQL installation does not conflict. Access frontends through Ingress or `kubectl port-forward`.

See [Kubernetes deployment](docs/KUBERNETES.md) for GPU operator, storage, image publishing, ingress, and upgrade details.

## Security posture

- Every Compose port binds to loopback unless `BIND_ADDRESS` is explicitly changed.
- PostgreSQL gets a generated high host port and is never exposed publicly by default.
- Secrets are generated into mode-`0600` `.env`, which is ignored by Git.
- Models and application state use named Docker volumes.
- Hermes is disabled by default and has tool-loop hard stops when enabled.
- The Docker socket is **not** mounted into the gateway, model, frontend, or Hermes containers.
- Kubernetes Secrets are created before Helm installation and referenced by name.
- Ingress is disabled by default.

For remote private access, prefer Tailscale, an authenticated reverse proxy, or a VPN. Do not publish llama.cpp, PostgreSQL, or Hermes directly to the public internet.

## Documentation

- [Pure Docker design](docs/PURE_DOCKER.md)
- [Model management](docs/MODEL_MANAGEMENT.md)
- [Kubernetes deployment](docs/KUBERNETES.md)
- [Operations and recovery](docs/OPERATIONS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Implementation plan](docs/BUILD_PLAN.md)

## Development and validation

Development tooling is also containerized:

```bash
./hub test
./hub lint
```

The release gate should additionally run `./hub smoke` on a host with Docker, NVIDIA Container Toolkit, the T4, and both model files. Offline unit tests cannot prove CUDA compatibility, model quality, token throughput, or long-context stability.

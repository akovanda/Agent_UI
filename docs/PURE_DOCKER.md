# Pure Docker deployment design

## Goal

Local AI Hub should be deployable and controllable without installing a local Python environment, PostgreSQL client, Hugging Face tooling, Helm, or kubectl. The repository uses Docker as both the application runtime and the operational runtime.

## Host boundary

The supported host contract is deliberately narrow:

- Docker Engine and a reachable daemon.
- Docker Compose v2.
- NVIDIA Container Toolkit when GPU inference is enabled.
- Bash or a compatible shell for `./hub`.
- Sufficient disk for images, model weights, databases, and backups.

`./hub` is a thin dispatcher. Substantive work runs in `local-ai-hub-toolbox`.

## Managed resources

| Resource | Compose implementation | Kubernetes implementation |
|---|---|---|
| Model weights | Named Docker volume | PVC |
| PostgreSQL | pgvector container + volume | StatefulSet + PVC |
| Gateway state | Named volume | Stateless Deployment; DB-backed memory |
| Open WebUI | Container + DB + volume | Deployment + DB + PVC |
| SillyTavern | Container + named volumes | Deployment + PVC subdirectories |
| Hermes | Optional profile + named volume | Optional Deployment + PVC |
| llama configuration | Generated named volume | Generated ConfigMap |
| Secrets | Generated `.env`, mode 0600 | Existing Kubernetes Secret |
| Port allocation | Generated unused high loopback ports | ClusterIP by default |

## Port allocation

Container ports remain predictable:

- PostgreSQL: 5432
- llama.cpp: 8080
- gateway: 8000
- Open WebUI: 8080
- SillyTavern: 8000
- Hermes API: 8642
- Hermes dashboard: 9119
- Prometheus: 9090

Host ports are intentionally unrelated to those numbers. During `./hub init`, the toolbox container binds candidate high ports on host networking to verify availability, then writes unique choices to `.env`. This handles an existing PostgreSQL process on 5432 without special cases.

The selected values remain stable across normal restarts. Reallocate them explicitly:

```bash
./hub init --reallocate-ports
```

Rotate secrets separately or together:

```bash
./hub init --rotate-secrets
./hub init --reallocate-ports --rotate-secrets
```

Rotating service credentials on an existing database requires an application-aware migration; do not rotate PostgreSQL credentials casually after first boot.

## Compose lifecycle

```bash
./hub init
./hub model import gpt-oss-20b /models/gpt-oss-20b.gguf
./hub model import stheno-8b /models/Llama-3.1-8B-Stheno-v3.4.Q4_K_M.gguf
./hub up
./hub smoke
```

Optional profiles keep the default footprint small:

```bash
./hub up --agent
./hub up --observability
./hub up --agent --observability
```

## Why a toolbox image

The toolbox centralizes:

- Port probing and secret generation.
- YAML catalog validation.
- Atomic model imports and downloads.
- SHA-256 verification.
- llama.cpp preset rendering.
- Hermes configuration rendering.
- Unit tests and linting.
- Helm and kubectl.

Pinning these dependencies in one image prevents the deployment from changing behavior based on whichever Python, `sed`, Helm, or kubectl happens to be installed on the host.

## Named volumes versus bind mounts

Mutable data uses named volumes. The repository itself is mounted only for explicit toolbox operations and the Prometheus configuration. Application images contain their code and managed defaults.

Benefits:

- File ownership is controlled by containers.
- A deployment is not dependent on arbitrary host directory layouts.
- Model imports can be atomic.
- Backups can address resources by stable names.
- Compose and Kubernetes storage concepts remain parallel.

The model volume is never committed or included in source archives.

## Networking

All services share `hub-backend`. Browser-facing services publish generated ports on `127.0.0.1`. Internal service names are stable:

```text
postgres:5432
llama:8080
gateway:8000
open-webui:8080
sillytavern:8000
hermes:8642
```

`BIND_ADDRESS=0.0.0.0` is supported but intentionally not the default. Prefer a VPN or authenticated reverse proxy rather than broad host binding.

## GPU ownership

Only the llama.cpp container requests the GPU. Frontends, gateway, PostgreSQL, and Hermes run on CPU. This makes GPU ownership explicit and avoids accidental competition between independent model servers.

`LLAMA_MODELS_MAX=1` enforces a single resident model. The T4-specific defaults are a safe starting point, not a claim of optimality. Measure:

- cold-load time;
- prompt-processing tokens per second;
- generation tokens per second;
- peak VRAM;
- host RAM and NUMA behavior;
- 8K, 32K, and 64K context behavior;
- model-switch latency.

## Destruction

Normal shutdown preserves volumes:

```bash
./hub down
```

Complete deletion is intentionally explicit:

```bash
./hub destroy --yes
```

That removes all Local AI Hub managed volumes, including model weights and databases. Back up first.

# Local AI Hub 0.2.0

## Pure Docker control plane

- Compose is now the canonical deployment.
- llama.cpp inference runs in the official CUDA server container.
- Operational dependencies moved into a toolbox image.
- Host Python, PostgreSQL, Helm, kubectl, Hugging Face CLI, and OpenSSL are no longer deployment requirements.

## Conflict-free ports

- `./hub init` probes and persists unused high loopback ports.
- PostgreSQL no longer assumes host port 5432.
- Every published service is loopback-only by default.

## Model lifecycle

- Added a base model catalog and private overlay.
- Added atomic local GGUF imports.
- Added Hugging Face and URL fetch support.
- Added checksum verification and canonical filename normalization.
- Added llama.cpp preset and Hermes config generation.
- Added Kubernetes model-PVC importer.

## Kubernetes

- Added a Helm chart for PostgreSQL, llama.cpp, gateway, Open WebUI, SillyTavern, and optional Hermes.
- Added GPU resource, storage class, existing PVC, ingress, ServiceMonitor, and network-policy controls.
- PostgreSQL remains ClusterIP-only by default.

## Operational cautions

- Third-party image tags default to moving upstream tags and should be pinned before production deployment.
- GPT-OSS GGUF acquisition remains operator-supplied until an exact repository, filename, and checksum are selected.
- Docker, T4, CUDA, long-context, model-switch, and Kubernetes runtime gates still require execution on the target environment.

# Upstream Compatibility Contract

This repository encodes the external contracts Local AI Hub requires from llama.cpp, Open WebUI,
SillyTavern, Hermes Agent, PostgreSQL/pgvector, Docker Compose, and Helm. Moving upstream image tags
are convenient bootstrap defaults, not a production version policy. Pin tested tags or digests in
`.env` and record them with each accepted baseline.

## Required contracts

### llama.cpp server

The gateway and control plane require:

- OpenAI-compatible `POST /v1/chat/completions`;
- router `GET /models` with `data[].id` and `data[].status.value`;
- `POST /models/load` and `POST /models/unload` using a model id;
- preset INI support through `--models-preset`;
- request routing by the OpenAI `model` field;
- native top-level `reasoning_effort` support for GPT-OSS/Harmony;
- API-key protection for non-health endpoints;
- health and Prometheus metrics endpoints;
- one-model operation through `--models-max 1`.

If router behavior regresses, use `compose.single-model.yaml` to run a fixed backend while pinning or
replacing the offending image. Fixed mode intentionally rejects profiles targeting a different
model rather than returning a response from the wrong backend.

### Open WebUI

The baseline requires:

- an OpenAI-compatible base URL and API key;
- PostgreSQL persistence plus `/app/backend/data` local state;
- `/health` readiness;
- local authentication and configurable signup;
- configurable built-in memory behavior;
- pgvector-backed RAG support.

### SillyTavern

The baseline requires the official image's config, data, plugin, and extension paths; its
`src/healthcheck.js` command; and a browser-configurable OpenAI-compatible endpoint.

### Hermes Agent

The optional integration requires:

- mutable state under `/opt/data`;
- a custom OpenAI-compatible provider and configurable context length;
- `gateway run`, the API server on 8642, and dashboard on 9119;
- API authentication and authenticated dashboard access;
- container UID/GID remapping support;
- tool-loop hard stops and persistent configuration.

Hermes routes inference through the Local AI Hub gateway rather than receiving the llama.cpp key.

## Validation status

| Layer | Repository/CI validation | Target-host validation |
|---|---|---|
| Gateway routing, transforms, memory, and streaming | Automated tests with branch coverage | Re-run smoke with real models |
| Python, shell, YAML, JSON, and schemas | Containerized lint/test gate | Re-run on deployment candidate |
| Compose topology | `docker compose config` in CI | Start services and verify persistence |
| Helm chart | `helm lint` and `helm template` in CI | Install against the actual cluster/storage/GPU stack |
| llama.cpp router lifecycle | Source contract and mocked tests | **Required with selected image** |
| GPT-OSS and Stheno templates | Payload behavior tested | **Required with real GGUFs** |
| CUDA/T4 fit, context, and speed | Not provable without target hardware | **Required** |
| Open WebUI, SillyTavern, and Hermes UX | Configuration contract tested | **Required interactively** |

## Promotion procedure

For every upstream image or model change:

1. select a candidate immutable tag/digest and record its upstream version;
2. run `./hub test` and `./hub lint`;
3. render and review Compose and Helm output;
4. run `./hub doctor --gpu` on the UCS;
5. run `./hub smoke`, `./hub switch-regression 25`, and `./hub benchmark 3`;
6. verify Open WebUI, SillyTavern, and optional Hermes state across restart;
7. record GGUF hashes, image digests, NVIDIA driver, settings, and results in `docs/baselines/`;
8. retain the previous known-good images/configuration for rollback;
9. update `.env`, `CHANGELOG.md`, and this compatibility record.

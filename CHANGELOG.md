# Changelog

All notable changes to Agent UI are documented here.

## 0.3.0 - Generic Platform

### Added

- Version-2 catalog with separate backend, model, artifact, capability, feature, and experience resources.
- Valid zero-model startup with `setup_required` health and inspection APIs.
- Capability-based selection for chat, code, story, image, agent, embeddings, and reranking workloads.
- Declarative reasoning/effort mappings with body or chat-template transport.
- Generic multi-backend routing for managed llama.cpp and OpenAI-compatible services.
- OpenAI-compatible Responses, completions, infill, embeddings, reranking, and image routes.
- AI-first `catalog plan/apply/show/validate` workflow and published JSON Schema.
- Existing host-path and Docker-volume model registration without copying.
- Kubernetes existing-PVC and hostPath generation plus arbitrary extra volumes/mounts.
- Generic discovery, registration, linking, managed import, and endpoint examples.
- Optional story, agent, and observability deployment profiles.

### Changed

- Removed model identities and weight assumptions from the distributable catalog.
- Renamed user-facing deployment defaults from Local AI Hub to Agent UI while preserving compatibility headers and package paths during migration.
- Compose now layers a generated installation-specific mount override.
- Helm defaults to an empty model map and disables optional story/agent surfaces.
- Reasoning support is no longer inferred from model names.
- Backups explicitly distinguish Agent UI-managed data from externally owned model sources.

### Security

- Persistent catalog mutation remains outside the chat gateway.
- External model mounts default to read-only.
- Backend catalogs reference secret environment-variable names rather than secret values.
- Capability checks fail closed before requests reach an incompatible backend.

## 0.2.0 - Container and Kubernetes Foundation

- Introduced the pure-Docker Compose control plane, named volumes, port allocation, Helm deployment, local model management, optional agent integration, backup/restore, and runtime validation.

## 0.1.0 - Initial Prototype

- Established the gateway, local inference coordination, shared memory, general and creative client surfaces, documentation, and test harness.

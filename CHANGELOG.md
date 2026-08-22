# Changelog

## 0.2.0 — 2026-08-21

- Made Docker Compose the canonical pure-container deployment.
- Containerized llama.cpp CUDA inference and all operational tooling.
- Added generated unused high loopback ports, including PostgreSQL.
- Added strong local secret generation and explicit named volumes.
- Added model catalog, private overlay, atomic imports, downloads, checksums, and canonical filename handling.
- Added optional Hermes and Prometheus Compose profiles.
- Added containerized smoke, switch-regression, benchmark, test, lint, backup, and restore commands.
- Added a Helm chart with PostgreSQL, GPU inference, gateway, Open WebUI, SillyTavern, optional Hermes, storage, ingress, and model-PVC support.
- Added Kubernetes Secret and model-import workflows through the toolbox container.

## 0.1.0

- Initial local multi-model gateway, memory, routing, and interface deployment baseline.

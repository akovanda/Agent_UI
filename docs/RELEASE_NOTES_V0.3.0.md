# Agent UI 0.3.0 — Generic Platform

Agent UI 0.3 removes preferred-model assumptions from the distributable project and
turns the system into a capability-oriented control plane for operator-owned models.

## Core changes

- Empty installations start successfully and report `setup_required`.
- Models are registered against backends and declare capabilities explicitly.
- Stable experiences (`chat`, `code`, `story`, `image`, `agent`, and retrieval
  workloads) select compatible deployments by priority or explicit pinning.
- Reasoning/effort support is declared per model and can map client values to any
  upstream body field or chat-template argument.
- The gateway supports chat, Responses, completions, infill, embeddings, reranking,
  and image-generation routes.

## Existing model storage

The model artifact contract supports:

- Agent UI-managed copies;
- read-only host file or directory mounts;
- existing Docker volumes;
- operator-provided container paths;
- existing Kubernetes PVCs;
- explicit Kubernetes hostPath sources;
- API-served models with no local artifact.

Compose mount overrides and Helm values are generated from the same effective catalog.
External storage remains independently owned and is not silently copied or deleted.

## AI-first setup

A setup agent can discover candidates, produce a schema-valid overlay, run a dry plan,
request operator approval, apply the catalog, start the stack, and verify setup status.
The live gateway exposes read-only setup inspection but cannot mutate host mounts or
credentials through a chat request.

## Compatibility

The v0.3 gateway retains legacy Local AI Hub headers and version-1 profile loading for
migration. New installations should use the version-2 catalog and `X-Agent-UI-*`
headers.

## Validation expectations

A release candidate must pass Python tests with branch coverage, Ruff, Python compile,
shell syntax, toolbox and gateway image builds, generated Compose validation, Helm
lint, default chart rendering, and existing-PVC chart rendering.

# Architecture

## Design goal

Agent UI is a generic local AI control plane. It should remain useful when model families, runtimes, modalities, and storage layouts change.

The system therefore avoids model-name heuristics and separates five resource types:

1. **Storage/artifact source** — where weights or related files live.
2. **Backend** — which inference API serves requests.
3. **Model** — a registered deployment on a backend.
4. **Experience** — a stable human-facing name selecting a capability.
5. **Client surface** — the UI or API consumer used during live operation.

## Provisioning versus live use

```text
Provisioning plane                         Runtime plane
──────────────────────────────────         ─────────────────────────────
AI setup agent / human operator            Human UI driver
./hub catalog plan/apply                   Open WebUI
./hub model discover/link/register         Story workspace (optional)
JSON Schema + YAML overlay                 Agent workspace (optional)
Docker/Helm mount generation               OpenAI-compatible clients
              │                                      │
              └──────── catalog contract ────────────┘
```

The provisioning plane may inspect host paths and generate container mounts. The runtime gateway is read-only with respect to the persistent catalog. This boundary prevents an untrusted chat prompt from mounting host directories, altering secrets, or installing a different model.

## Resource graph

```text
Experience ──requires──► Capability
     │                        ▲
     └────selects──────── Model deployment
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
                Backend              Artifact
          inference transport      optional weights
```

A model may have no artifact when it is served by an endpoint. One artifact may be independently shared by multiple projects. An experience may be unpinned and select the highest-priority capable model.

## Default deployment

```text
Open WebUI ───────────────────────┐
Story workspace (optional) ───────┼──► Agent UI Gateway
Agent workspace (optional) ───────┘          │
                                             ├── catalog/experience resolver
                                             ├── reasoning/feature translator
                                             ├── memory isolation
                                             ├── backend registry
                                             └── metrics and policy boundary
                                                        │
                            ┌───────────────────────────┼───────────────────────────┐
                            ▼                           ▼                           ▼
                     local llama.cpp         OpenAI-compatible API          image service
                    generated mounts          local or remote               local or remote
                            │
                     one large model
                     resident as policy
```

PostgreSQL/pgvector stores shared memory and UI data. Compose is the default deployment mechanism; Helm provides the cluster equivalent.

## Catalog lifecycle

```text
base catalog
     +
installation overlay
     │
     ▼
validation
     │
     ├──► resolved gateway catalog
     ├──► llama.cpp models.ini
     ├──► generated Compose mount override
     ├──► optional agent configuration
     └──► generated Helm values
```

All outputs derive from one contract. Storage, runtime presets, and gateway routes cannot silently disagree about a model's identity or path.

## Empty-install behavior

A zero-model catalog is valid. The gateway starts, reports `setup_required`, exposes its catalog and experience templates, and rejects inference with a precise unavailable-model error. This supports automated onboarding and avoids using container crash loops as setup signaling.

## Request flow

1. Authenticate the gateway request.
2. Resolve the requested model field as an experience or direct model ID.
3. Select an enabled model by declared capability and priority.
4. Confirm that the selected model declares the endpoint's modality.
5. Resolve the backend and secret environment variable.
6. Apply experience defaults without overwriting explicit request values.
7. Translate reasoning effort according to the model feature declaration.
8. Retrieve scoped memory for eligible chat experiences.
9. For explicitly coordinated local backends, acquire the backend lease and load the selected model.
10. Forward or stream the request.
11. Release resources on normal completion, disconnect, cancellation, or error.
12. Emit route and latency metrics without storing prompt content.

## Backend model

A backend declares:

- kind;
- URL or URL environment variable;
- API-key environment variable;
- endpoint path overrides;
- model coordination mode;
- request serialization policy;
- backend-specific options.

The gateway currently supports llama.cpp and OpenAI-compatible transports. The backend registry is intentionally independent of model capabilities so the same API can serve text, embeddings, reranking, vision, or images.

## Local model coordination

A single-GPU installation may set:

```yaml
coordinator: explicit
serialize_requests: true
```

The lease covers model transition and the complete streamed response. This prevents one request from unloading a model used by another request. Remote or independently scaled backends normally disable serialization.

## Reasoning translation

Reasoning effort is modeled as a transport adapter:

```text
client value ──map──► upstream value ──place──► body field or template kwargs
```

The accepted values and mapping are declared per model. The gateway never infers support from a model ID.

## Storage mapping

### Compose

Host paths and existing Docker volumes generate `.agent-ui/compose.generated.yaml`. The base Compose file remains distributable and model-neutral. The `hub` wrapper always layers the generated override when present.

### Kubernetes

The catalog generates model values plus `llama.extraVolumes` and `llama.extraVolumeMounts`. Existing PVCs and explicit hostPath sources are first-class; arbitrary CSI/NFS/object-store volumes can be supplied directly through Helm values.

## Memory boundary

Shared memory is keyed by user and namespace. Experiences define allowed namespaces. Retrieved memory is inserted under a heading that explicitly labels it as untrusted reference material. It does not become a system authority merely because it was previously stored.

## Extension paths

The architecture supports adding:

- new capability names;
- new endpoint mappings;
- new backend adapters;
- new human-facing clients;
- new storage renderers;
- new reasoning transports;
- policy plugins;
- model scoring/selection strategies.

Extensions should preserve declarative registration, zero-model startup, deterministic dry runs, explicit secret references, and fail-closed capability checks.

## Non-goals

Agent UI does not:

- redistribute model weights;
- decide that discovered models are licensed or safe;
- silently grant tools to a model;
- make external storage part of its backup ownership;
- expose host mount mutation through the chat API;
- assume every backend implements every OpenAI route.

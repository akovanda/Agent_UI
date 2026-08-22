# Architecture

## Goals

Local AI Hub provides one coherent private AI environment without pretending that one model or
one interface is ideal for every task. It must:

1. make GPT-OSS convenient for daily chat, technical work, knowledge, coding, and tools;
2. preserve Stheno's creative behavior in a story-native interface;
3. operate on one 16 GB Tesla T4 without accidental simultaneous model residency;
4. expose stable OpenAI-compatible interfaces so components remain replaceable;
5. maintain shared, scoped user/project/campaign memory without silently trusting retrieved text;
6. support an agent harness without making privileged automation part of the baseline failure path;
7. be observable, testable, recoverable, and safe to access remotely.

## Logical components

### 1. llama.cpp router

The inference process advertises three backend aliases from `config/llama/models.ini`:

- `gpt-oss-20b`: 32K target context for ordinary chat.
- `gpt-oss-20b-hermes`: the same weights with a 64K target for longer agent loops.
- `stheno-8b`: 32K creative profile.

The router is started with a maximum of one loaded model and autoload disabled. The gateway calls
`/models/unload` and `/models/load` explicitly. This produces deterministic transitions and makes
load time visible in gateway metrics.

Router mode is an optimization, not a hard dependency. `compose.single-model.yaml` replaces it
with ordinary single-model serving when necessary.

### 2. Local AI Hub Gateway

The FastAPI gateway is the stable control plane and OpenAI-compatible data plane.

Responsibilities:

- API-key enforcement and CORS boundaries;
- virtual model discovery;
- explicit and automatic intent routing;
- prompt/profile transformation;
- shared-memory retrieval;
- single-GPU request leases;
- model lifecycle coordination;
- streaming and non-streaming proxying;
- OpenAI-shaped failures;
- health, status, and Prometheus metrics.

The gateway does **not** execute shell commands. Tool execution belongs in Hermes or explicitly
configured Open WebUI tools, behind their own approval and credential boundaries.

### 3. PostgreSQL/pgvector

Phase-one memory uses PostgreSQL full-text search because it is deterministic, auditable, and does
not require another embedding model competing for the T4. The schema already includes a nullable
`vector` field for later hybrid retrieval.

Namespaces prevent accidental cross-domain retrieval:

| Profile | Default namespaces |
|---|---|
| `assistant` | `user`, `infrastructure`, `projects`, `general` |
| `storyteller` | `story`, `campaign` |
| direct model aliases | none |

Retrieved text is injected under a system message that explicitly labels it as untrusted reference
data, never as instructions.

### 4. Open WebUI

Open WebUI is the default daily interface. It connects to the gateway and sees the virtual model
IDs. Its own knowledge collections, tools, and workspace models remain available, but critical
routing and GPU lifecycle behavior do not depend on Open WebUI-specific extensions.

When Hermes is enabled, Open WebUI should add it as a second OpenAI-compatible connection. This
keeps ordinary chat direct and low-risk while making agent mode an explicit model choice.

### 5. SillyTavern

SillyTavern connects to the same gateway but normally selects `storyteller`. Character cards,
World Info/lorebooks, Author's Notes, personas, scene state, and campaign summaries remain in the
story client, where they are most effective.

Shared gateway memory should hold durable cross-session facts and campaign state—not every line of
transcript. SillyTavern remains the source of truth for active character and lorebook structure.

### 6. Hermes Agent

Hermes is an optional service profile. Its provider points to the hidden `hermes-agent`
profile through the gateway, while Hermes exposes a separate OpenAI-compatible API back to
Open WebUI.

This arrangement preserves:

- gateway GPU locking and model lifecycle control;
- Hermes memory, skills, terminal, browser, and tool loop;
- explicit selection of agent mode;
- the ability to remove or replace Hermes without changing the baseline stack.

## Request flows

### General chat

```text
Open WebUI
  POST model=assistant
        │
        ▼
Gateway authenticates → retrieves scoped memory → injects GPT-OSS profile
        │
        ▼
Acquire GPU lease → ensure gpt-oss-20b loaded → stream llama.cpp response
```

### Story request with automatic routing

```text
Client sends model=auto and "Continue our campaign scene"
        │
        ▼
Rule router selects storyteller
        │
        ▼
Retrieve story/campaign memory → apply creative sampler
        │
        ▼
Unload GPT-OSS if needed → load Stheno → stream response
```

### Agent request

```text
Open WebUI selects Hermes connection
        │
        ▼
Hermes performs reasoning/tool loop
        │ model calls
        ▼
Gateway model=hermes-agent → llama.cpp
```

## Concurrency model

The first deployment uses one gateway worker and a one-permit GPU semaphore. The lease covers both
model transition and the full response stream. This prevents:

- one request unloading a model used by another;
- two backends attempting to occupy the T4 simultaneously;
- uncontrolled queue growth inside llama.cpp;
- model switches in the middle of SSE streaming.

CPU-only work, database queries, UI activity, and Hermes orchestration can remain concurrent. If a
second GPU is added, the coordinator can evolve to per-device leases rather than removing this
abstraction.

## Trust boundaries

```text
Untrusted: user prompts, uploaded files, retrieved documents, web content, model output
    │
    ▼
Gateway: authentication, namespace controls, bounded injection, no command execution
    │
    ▼
Agent/tool boundary: explicit approvals, constrained credentials, sandbox/SSH policy
    │
    ▼
Hosts, Git repositories, email, home/network systems
```

No model is trusted to decide its own privilege. A tool call is a proposal interpreted by a
separate policy layer.

## Failure strategy

| Failure | Degradation |
|---|---|
| PostgreSQL unavailable | Chat continues without shared memory unless `MEMORY_REQUIRED=true` |
| llama router bug | Switch to fixed-model Compose override |
| Hermes unavailable | General and story chat continue |
| Open WebUI unavailable | Direct API and SillyTavern continue |
| SillyTavern unavailable | Open WebUI can still select `storyteller` |
| Model load fails/OOM | Gateway returns 503 with model-transition detail; tune preset |
| Bad route | Select explicit model or use `/story`/`/assistant` |

## Evolution points

- Replace rule routing with a measured classifier while preserving explicit overrides.
- Add document ingestion and hybrid lexical/vector retrieval.
- Add approved memory proposals rather than silent transcript harvesting.
- Add per-profile tool allowlists and policy decisions.
- Add a second GPU or remote inference endpoint behind the same backend aliases.
- Add Open Responses API compatibility once required by clients.

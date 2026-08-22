# Testing and Release Gate

Local AI Hub separates repository validation from target-host validation. The repository tests can
prove routing, configuration generation, memory isolation, packaging, and static integrity. Only a
machine with Docker, NVIDIA Container Toolkit, the T4, and real GGUF files can prove CUDA
compatibility, model quality, long-context behavior, and model-switch reliability.

## Repository validation

All development tools run in the toolbox container:

```bash
./hub test
./hub lint
```

`./hub test` runs the gateway suite with branch-aware coverage and requires at least 80 percent.
The suite covers:

- profile loading, shorthand generated profiles, and advertised model discovery;
- deterministic automatic routing and explicit overrides;
- GPT-OSS developer-message and `reasoning_effort` transforms;
- story sampler defaults and client overrides;
- scoped memory creation, retrieval, and untrusted-context rendering;
- explicit model load/unload behavior and fixed-model fallback;
- one-request GPU serialization, streaming, cancellation, and cleanup;
- upstream error mapping, health, metrics, and administration endpoints;
- Docker control-plane model registration and generated configuration.

`./hub lint` runs Ruff, Python compilation, shell syntax checks, YAML/JSON/schema validation,
repository guards, Compose rendering, and Helm lint/template from containerized tooling.

CI repeats the same containerized checks on `ubuntu-24.04`; it does not require model files or a
GPU.

## Target-host preflight

On the UCS:

```bash
./hub init
./hub model list
./hub model verify gpt-oss-20b
./hub model verify stheno-8b
./hub doctor --gpu
```

The preflight must establish:

- Docker Engine and Compose v2 work;
- NVIDIA Container Toolkit exposes the T4 inside a container;
- generated ports are unique and loopback-bound;
- generated secrets are present and `.env` permissions are restrictive;
- the model catalog resolves to files in the managed model volume;
- the runtime llama.cpp and gateway profile files render successfully.

## Container smoke test

Start the default stack and exercise it from inside the Compose network:

```bash
./hub up
./hub smoke
```

The smoke test must verify:

1. PostgreSQL is ready;
2. llama.cpp is healthy and authenticated;
3. the gateway is healthy and rejects an incorrect key;
4. `/v1/models` advertises the expected virtual profiles;
5. an assistant request reaches GPT-OSS;
6. a story request reaches Stheno;
7. response metadata identifies the selected profile and backend;
8. no more than one model is resident.

## Model-switch regression

Run at least 25 alternating requests:

```bash
./hub switch-regression 25
```

For a longer run:

```bash
./hub switch-regression 50
```

Acceptance criteria:

- every response reports the intended virtual and backend model;
- no GPU OOM, reset, orphan model process, or deadlock occurs;
- a failed load or timeout returns a bounded error rather than hanging;
- same-model consecutive requests do not reload unnecessarily;
- cancellation releases the GPU lease;
- the stack remains healthy after the final transition.

## Performance baseline

Run the built-in benchmark while separately watching the GPU and containers:

```bash
./hub benchmark 3 | tee benchmarks/t4-baseline.csv
nvidia-smi dmon -s pucvmet -d 1
./hub compose stats
```

Measure and record:

- cold model load and first-token latency in both directions;
- warm prompt processing and generation throughput;
- 8K, 16K, 32K, and intended maximum contexts;
- peak VRAM, system RAM, CPU, and swap;
- response quality for assistant and story tasks;
- exact model hashes and image digests.

Client-side token estimates must be labeled as approximate. Prefer llama.cpp-native timings and
usage fields for accepted baselines.

## Interface and persistence checks

After Open WebUI and SillyTavern are configured, verify:

- Open WebUI sign-in, model selection, streaming, uploads, RAG, and conversation history;
- SillyTavern character cards, lorebooks, presets, streaming, and campaign continuity;
- optional Hermes API/dashboard authentication and read-only agent operation;
- state survives `./hub restart` and a host reboot;
- assistant and story memory namespaces remain isolated.

## Backup and restore drill

```bash
./hub backup /secure/backups/local-ai-hub-test
./hub restore /secure/backups/local-ai-hub-test --yes
./hub smoke
```

Perform destructive restore testing only against an isolated deployment or after a reviewed
production outage procedure. Validate representative Open WebUI, SillyTavern, gateway memory, and
Hermes state—not merely archive checksums.

## Quality evaluation

Keep qualitative prompt baselines under `docs/baselines/`. Include:

- ordinary chat and factual uncertainty;
- technical explanation and debugging;
- long-document summarization;
- story continuation, character voice, and player agency;
- campaign continuity through summaries and lorebooks;
- ambiguous prompts and explicit route overrides;
- memory conflicts, stale memories, and prompt-injection attempts;
- tool-call attempts when tools are intentionally unavailable.

Store outputs only after removing private conversations and credentials.

## Release gate

A release is ready for routine use when:

- repository tests, lint, Compose rendering, and Helm rendering pass;
- model files and image versions are pinned and recorded;
- `./hub doctor --gpu` and `./hub smoke` pass;
- at least 25 alternating model transitions pass;
- Open WebUI and SillyTavern preserve state across restart;
- a staged backup and restore drill passes;
- default ports remain loopback-only;
- a representative soak has no unexplained hangs, wrong routing, memory crossover, or corrupt
  persistent state.

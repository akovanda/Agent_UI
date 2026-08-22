# Detailed Build Plan

## Delivery definition

The project is complete when Andrew can reliably use one private URL for everyday AI, one
story-native URL for campaigns, and one explicitly selected agent mode, while the system chooses or
loads the correct model, preserves scoped memory, survives upgrades, and provides enough telemetry
to distinguish model quality problems from infrastructure problems.

This plan separates **repository implementation**, **target-host validation**, and **operational
acceptance**. The repository can encode and test behavior, but T4 performance, model context fit,
and CUDA image compatibility must be measured on the UCS.

## Status legend

- **Done:** implemented and covered by repository tests.
- **Ready to validate:** implemented, but requires the UCS/model files.
- **Planned:** issue-ready design exists; implementation remains.

## Milestone 0 — Repository foundation

**Status: Done**

Deliverables:

- private-repo-ready Git history and publishing script;
- Compose topology and environment contract;
- gateway package, tests, CI, documentation, ADRs, and backlog;
- secrets excluded from Git;
- model weights excluded from Git;
- loopback-only default exposure.

Exit criteria:

- `python3 -m pytest --cov` passes at or above 80%;
- all YAML and Python parse;
- archive contains no generated secrets or model weights;
- repository can be published with `scripts/publish-to-github.sh`.

## Milestone 1 — UCS host readiness

**Status: Ready to validate**

Tasks:

1. Confirm Ubuntu, Docker Engine, Compose plugin, NVIDIA driver, and NVIDIA Container Toolkit.
2. Confirm T4 is visible both to `nvidia-smi` and inside the selected llama.cpp CUDA image.
3. Put models on SSD-backed storage; avoid the slower HDD for model swapping.
4. Record driver, CUDA compatibility, Docker image digest, llama.cpp build number, CPU topology,
   NUMA layout, and filesystem throughput.
5. Run `bootstrap.sh`, `doctor.sh`, and `check-models.sh`.

Exit criteria:

- `doctor.sh` reports no fatal issue;
- GPU is visible in the container;
- at least 30 GiB remains free after both weights are present;
- model files pass filename and size validation;
- generated secrets are at least 256 bits and permissions are `0600`.

## Milestone 2 — Stable inference on one T4

**Status: Ready to validate**

Tasks:

1. Start llama.cpp router only and inspect `/models`.
2. Load Stheno; verify full or intended GPU offload, 32K allocation, and generation quality.
3. Unload Stheno; load GPT-OSS; verify no OOM and inspect CPU/GPU split selected by `--fit`.
4. Exercise `gpt-oss-20b-hermes` at 64K target.
5. Run the tuning matrix in `MODEL_TUNING.md`.
6. Pin the best-known llama.cpp CUDA image by immutable build tag or digest.
7. Test the fixed-model fallback before relying on router mode.

Initial performance gates are deliberately broad until measured:

- no model-load crash or GPU reset in 25 consecutive transitions;
- no malformed chat template output in a 50-prompt probe set;
- stable 30-minute generation for each model;
- Stheno feels interactively responsive;
- GPT-OSS remains usable for chat even if some MoE/layers spill to host RAM;
- model switch time is measured and surfaced, not hidden behind a generic timeout.

Exit criteria:

- a committed `docs/baselines/ucs-t4-<date>.md` records the chosen preset and benchmark;
- both router and fallback paths pass smoke tests;
- GPU memory has a safety margin under worst tested context;
- no unbounded CPU or swap growth.

## Milestone 3 — Everyday and story interfaces

**Status: Ready to validate**

Tasks:

1. Launch Open WebUI, create the first admin, disable open signup, and select
   `assistant` by default.
2. Verify Markdown, code blocks, file uploads, conversation history, mobile browser behavior,
   and long-response streaming.
3. Launch SillyTavern and configure an OpenAI-compatible endpoint to the gateway.
4. Create a reusable story preset selecting `storyteller`.
5. Import or build character cards, personas, World Info/lorebooks, Author's Note, and campaign
   summary conventions.
6. Verify model switching does not corrupt or cross-contaminate UI sessions.

Exit criteria:

- both interfaces work from desktop and iPhone through the chosen private network path;
- general chat never inherits Stheno sampling settings;
- story chat never receives GPT-OSS reasoning directives;
- a campaign can resume after container restart with card/lore data intact;
- Open WebUI data and SillyTavern bind mounts are included in backup jobs.

## Milestone 4 — Shared memory and knowledge

**Status: Baseline done; hybrid retrieval planned**

Already implemented:

- explicit memory create/search API;
- PostgreSQL full-text retrieval;
- namespace and user scoping;
- bounded context injection;
- untrusted-memory labeling;
- nullable pgvector field for later embeddings.

Remaining tasks:

1. Build an ingestion API for Markdown, text, PDF-extracted text, Git repositories, and selected
   infrastructure documentation.
2. Add chunk lineage, source hashes, timestamps, deletion, and re-index behavior.
3. Select a small CPU-friendly embedding model or isolated embedding service.
4. Implement hybrid lexical/vector scoring with deterministic fallback.
5. Add memory proposals: the model suggests a durable fact, but storage requires approval or a
   constrained policy.
6. Add memory review/edit/delete UI or Open WebUI function.
7. Add stale-memory detection and source-of-truth links.
8. Add campaign-specific namespaces and export/import.

Exit criteria:

- no retrieval crosses user or campaign namespaces in adversarial tests;
- deleting a source removes its chunks and vectors;
- retrieved claims retain source metadata;
- prompt injection inside stored documents cannot turn into tool instructions;
- a 100-query evaluation establishes recall/precision baselines.

## Milestone 5 — Hermes Agent

**Status: Compose and provider template done; privilege policy planned**

Tasks:

1. Start Hermes with `make agent` and verify its API health.
2. Add Hermes as a second Open WebUI OpenAI connection.
3. Verify `gpt-oss-20b-hermes` context actually remains at or near 64K on the T4.
4. Begin with file/search tools only; keep terminal, SSH, browser, GitHub, email, and Docker access
   disabled or approval-gated.
5. Define tool tiers:
   - read-only/local;
   - reversible write;
   - externally visible write;
   - privileged/destructive.
6. Add explicit approval prompts and immutable audit records for tiers 2–4.
7. Configure an isolated terminal backend; do not mount the host Docker socket by default.
8. Add learned skills only after reviewing their generated scripts and credential needs.

Exit criteria:

- Hermes can complete a multi-step read-only diagnostic through Open WebUI;
- every side effect is visible and attributable;
- prompt-injected content cannot silently elevate tool privilege;
- an agent failure cannot take down ordinary chat;
- agent memory/skills survive image replacement and are backed up.

## Milestone 6 — Unified routing and identity

**Status: Deterministic baseline done; evaluation-driven improvements planned**

Already implemented:

- `auto` virtual model;
- explicit model selection;
- `/story`, `/assistant`, and profile-header overrides;
- route-reason response header and Prometheus labels.

Remaining tasks:

1. Build a labeled routing dataset from real prompts.
2. Measure false-story and false-general rates.
3. Add conversation stickiness so follow-up turns do not oscillate models.
4. Add a lightweight classifier only if it materially outperforms rules.
5. Add model-load-cost awareness: avoid switching for a one-line task when the current model is
   adequate, unless accuracy/intent requires it.
6. Add per-conversation route display and correction feedback.
7. Share stable user identity and namespace metadata across Open WebUI, SillyTavern, and Hermes.
8. Preserve explicit user choice above all automatic logic.

Exit criteria:

- at least 95% routing accuracy on the maintained evaluation set;
- zero unexplained model switches in a 100-turn mixed-session test;
- route override works without restarting any service;
- every response identifies the selected profile/backend in headers and logs.

## Milestone 7 — Observability, evaluation, and quality

**Status: Metrics baseline done; dashboards/evals planned**

Tasks:

1. Pin and enable Prometheus.
2. Add Grafana or equivalent dashboards for:
   - queue depth;
   - first-token and total latency;
   - model load duration and failure rate;
   - prompt/generated token counts where available;
   - GPU memory, utilization, temperature, power, and throttling;
   - CPU, RAM, disk I/O, and swap;
   - route distribution and corrections.
3. Add NVIDIA DCGM exporter or a minimal `nvidia-smi` exporter.
4. Build eval suites for general knowledge, coding, tool calling, story continuity, routing, memory
   retrieval, and prompt injection.
5. Store benchmark metadata with image/model hashes.
6. Alert on repeated OOM, GPU reset, corrupt model response, DB failure, and backup age.

Exit criteria:

- a bad release is detectable within one smoke/eval run;
- benchmark results are reproducible and tied to exact versions;
- dashboards distinguish cold model load from warm inference latency;
- on-call diagnostics do not require guessing which model is resident.

## Milestone 8 — Security, remote access, and recovery

**Status: Safe defaults done; target integration planned**

Tasks:

1. Keep app ports loopback-bound; expose through Tailscale or an authenticated reverse proxy.
2. Restrict firewall access and use TLS for any non-loopback endpoint.
3. Disable Open WebUI signup after the admin account exists.
4. Rotate gateway, Hermes, and PostgreSQL secrets after initial setup.
5. Define credential files per tool and least-privilege scopes.
6. Encrypt backups and test restore to a clean directory/host.
7. Pin container images and create a controlled update procedure with rollback.
8. Add dependency/image scanning and secret scanning.
9. Record retention policy for conversations, memory, agent logs, and uploaded files.

Exit criteria:

- no unauthenticated service is reachable from the LAN/WAN;
- a restore drill recovers conversations, campaign data, memories, and Hermes state;
- secrets do not appear in Git, logs, process arguments, or benchmark output;
- update rollback is documented and tested.

## Milestone 9 — Operational acceptance

The system is accepted after a two-week soak in which:

- Andrew uses Open WebUI for ordinary work and SillyTavern for at least one continuing campaign;
- both models survive repeated daily switching;
- no unexplained OOM, GPU reset, or cross-profile prompt leakage occurs;
- backups complete and one restore succeeds;
- routing corrections are captured;
- Hermes completes approved tasks without unauthorized effects;
- known limitations and measured performance are recorded in the repository.

## Recommended implementation order

1. Publish the repository privately.
2. Validate T4 container compatibility and model files.
3. Establish stable llama.cpp single-model baselines.
4. Enable router mode and gateway switching.
5. Validate Open WebUI and SillyTavern.
6. Tune profiles and story context.
7. Add memory content deliberately.
8. Add Hermes with read-only tools.
9. Build hybrid RAG, routing evaluation, and dashboards.
10. Harden remote access, backups, and upgrade procedures.

This sequence avoids debugging model quality, GPU fit, UI configuration, agent tools, memory, and
remote networking simultaneously.

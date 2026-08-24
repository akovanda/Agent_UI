# Local Setup Issues

This log captures issues found while bringing a fresh clone of Agent UI v0.3 up on the
local UCS/T4 host. It is intended as a follow-up backlog, including non-blocking setup
friction.

## Environment

- Date: 2026-08-22 through 2026-08-24 UTC
- Host: Linux with Docker Compose v2 and the NVIDIA container runtime
- Checkout: `akovanda/Agent_UI`, branch `fix/v03-local-readiness`
- Local inference reused during setup: Ollama at `127.0.0.1:11436`
- Local UI validation: Open WebUI 0.11.0 at `127.0.0.1:46763`

## Issues encountered

### 1. Cold toolbox builds can appear stalled during external downloads

- Status: Open
- Area: `deploy/docker/toolbox.Dockerfile`, `./hub init`
- Observed: The first `./hub init` spent several minutes in the toolbox package/download
  layer. After the APT phase, the build emitted no progress while fetching the pinned
  `kubectl` and Helm binaries. The combined OS-package and external-tool layer completed
  in about 285 seconds; the following Python dependency layer took about 113 seconds.
- Impact: An operator cannot distinguish a slow download from a hung build, and the
  download commands have no visible retry or timeout policy in the build output.
- Current workaround: Allow the first build to finish and reuse Docker's layer cache on
  subsequent runs.
- Suggested follow-up: Add bounded connect/overall timeouts, retries, and concise progress
  messages around external binary downloads. Consider checksums for both downloads as part
  of the same hardening work.

### 2. Concurrent Hub commands can duplicate the toolbox image build

- Status: Open
- Area: `./hub init`, `./hub test`, toolbox image lifecycle
- Observed: Running `./hub init` and `./hub test` at the same time started two independent
  `docker build` processes for `agent-ui-toolbox:0.3.0`.
- Impact: Duplicate builds waste CPU, disk I/O, and network bandwidth, and they make a slow
  cold build harder to diagnose.
- Current workaround: Serialize Hub commands that require the toolbox image during first
  initialization.
- Suggested follow-up: Add a host-side build lock or a shared "ensure toolbox image" path
  that prevents concurrent builds of the same tag and context.

### 3. Restrictive checkout umask made the toolbox entrypoint unreadable

- Status: Resolved in this PR
- Area: `deploy/docker/toolbox.Dockerfile`
- Observed: This fresh clone was created under umask `077`, so tracked files arrived as
  mode `0600` and directories as `0700`. The toolbox image copied those modes as root and
  then used `chmod +x` on `hubctl.py`. That produced an executable-but-not-readable file
  for the image's non-root runtime user. `./hub init` ended with:

  ```text
  python: can't open file '/opt/agent-ui/ops/hubctl.py': [Errno 13] Permission denied
  ```

- Impact: A valid Git clone can build successfully but cannot run the control-plane CLI
  when the operator uses a privacy-preserving umask.
- Resolution: The toolbox image now makes the copied runtime tree readable/traversable and
  explicitly sets its Python/shell entrypoints to `0755`. Regression coverage checks the
  deterministic modes.

### 4. Loopback-only host inference is not reachable through `host.docker.internal`

- Status: Open documentation/integration issue; working local workaround applied
- Area: External OpenAI-compatible endpoint setup
- Observed: The existing Ollama service publishes `127.0.0.1:11436`. A toolbox container
  with the documented `host.docker.internal:host-gateway` mapping could not connect to
  `host.docker.internal:11436`, because the published service is deliberately bound only
  to host loopback.
- Impact: The documented host-endpoint pattern does not work for privacy-preserving
  loopback-only services on Linux, even though the same endpoint works from the host.
- Current workaround: Attach the existing Ollama container to Agent UI's private backend
  network and address it by container DNS name and internal port.
- Suggested follow-up: Document this Linux loopback limitation and a supported shared
  Docker-network pattern. A first-class local-network attachment option would avoid
  manual `docker network connect` state.

### 5. Dependency installation is expensive when the toolbox cache misses

- Status: Open
- Area: `deploy/docker/toolbox.Dockerfile`
- Observed: Rebuilding the toolbox re-downloaded and installed the complete Python runtime
  and development dependency set; that layer took roughly 163 seconds on the second build.
  A later README-only edit invalidated the same layer because the Dockerfile copies README
  beside `pyproject.toml` before dependency installation.
- Impact: Small Dockerfile or relevant source-context changes can make local iteration much
  slower than the code change itself.
- Current workaround: Preserve Docker's completed image/layer cache and serialize builds.
- Suggested follow-up: Separate dependency metadata from application-source copies and use
  a BuildKit pip cache mount or a wheelhouse stage so dependency downloads survive normal
  source edits.

### 6. Generated Compose override was unreadable after root-container rendering

- Status: Resolved in this PR
- Area: `render_runtime`, `ops/hubctl.py`, `.agent-ui/compose.generated.yaml`
- Observed: Catalog application rendered the host-side Compose override from a root Docker
  container. The result was owned by `root:root` with mode `0600`; the next normal-user
  `./hub compose create postgres` failed with:

  ```text
  open /home/akovanda/dev/Agent_UI/.agent-ui/compose.generated.yaml: permission denied
  ```

- Impact: Initialization or catalog application can make all subsequent host-side Compose
  operations fail for the invoking user.
- Resolution: Host-consumed generated Compose output now uses mode `0644` while the private
  generated-state directory remains `0700` and runtime catalog/secrets remain `0600`.
  Regression coverage asserts the mode distinction.

### 7. The merged `main` branch contained unapplied v0.3 finalizer artifacts

- Status: Resolved in this PR
- Area: v0.3 configuration/schema compatibility and one-shot release automation
- Observed: The fresh `main` checkout produced 26 test failures and 40 lint failures. The
  repository still contained tracked one-shot v0.3 finalizer scripts, workflows, and trigger
  files; the scripts describe transformations that had not been applied to the shipped source.
- Impact: The repository's checked-in tests/contracts disagree with runtime configuration,
  backend status behavior, Kubernetes values, local-artifact validation, and legacy profile
  compatibility. Embedded patch strings in the finalizer scripts also account for many lint
  errors.
- Resolution: Applied the intended transformations as reviewed source changes and removed
  the obsolete one-shot artifacts. Canonical validation now passes 69 tests with 81.13%
  branch coverage plus Ruff, formatting, compile, and shell-syntax checks.
- Suggested follow-up: Gate merges on the canonical commands after release-finalizer
  transformations have run, and avoid merging trigger commits independently of their result.

### 8. Docker disk-usage inventory can hang on this host

- Status: Open host-operations observation; non-blocking
- Area: Local Docker image store
- Observed: `docker system df` produced no output for more than a minute and was canceled.
  The filesystem itself remains healthy with roughly 353 GB free.
- Impact: Routine capacity diagnosis is slow or unreliable on this host's large Docker store.
- Current workaround: Use targeted filesystem and image/container inspections; do not prune
  blindly.
- Suggested follow-up: Investigate Docker daemon/image metadata performance during a separate
  maintenance window. Preserve Codex transcripts and project artifacts during any cleanup.

### 9. Direct Helm validation did not match the shipped values structure

- Status: Resolved in this PR
- Area: Helm chart validation and canonical lint coverage
- Observed: Direct `helm lint`/`helm template` checks fail because PostgreSQL templates reference
  `.Values.postgresql` while the shipped values structure uses `postgres:`. Four workload
  templates also read `.Values.global.imagePullPolicy`, but no `global` values map exists; each
  workload already has its own `image.pullPolicy`. The canonical `./hub lint` command still passes
  because it does not run Helm validation, despite the testing guide indicating that Helm checks
  are part of the lint lane.
- Impact: Kubernetes rendering can regress while the advertised canonical lint command remains
  green.
- Resolution: Aligned the templates with the shipped component-specific service, image, storage,
  annotation, Open WebUI, and ingress values; added default Secret creation; and removed the
  Hermes dependency on a hard-coded model. The repository CI covers Helm lint, default rendering,
  and generated existing-PVC values, and the optional SillyTavern/Hermes/Ingress render was also
  validated locally.
- Suggested follow-up: Add the same Helm checks to the local `./hub lint` command so local and
  CI validation remain equivalent.

### 10. Core UI/inference images are large and use floating tags

- Status: Open; local UI is healthy through a documented fallback, official image unresolved
- Area: `.env.example`, core Docker image lifecycle
- Observed: Refreshing the default core images pulled `open-webui:main` and
  `llama.cpp:server-cuda`. The transfer exposed Open WebUI layers of roughly 1.69 GB and 747 MB;
  each repeatedly lost the registry connection and restarted. After about 35 minutes, the
  unconverged default pull was canceled. The officially supported `main-slim` variant still has
  compressed layers of roughly 970 MB and 346 MB on the current build, but it omits pre-bundled
  models that this external-inference installation does not need. A concurrent test container
  also remained queued at `docker run` while the daemon extracted layers and was canceled. The
  configured `main`/implicit `latest` tags change independently of this repository. The current
  stable Open WebUI release was confirmed as `v0.11.0`, but both GHCR and Docker Hub subsequently
  timed out during registry handshakes. The supported slim image never resolved to a local digest.
- Impact: First startup is slow and network-sensitive, while two installations from the same
  Agent UI commit may run materially different upstream images.
- Current workaround: Pin the ignored local `.env` to `v0.11.0-slim` for a later registry retry.
  For this validation, install the exact `open-webui[postgres]==0.11.0` wheel plus CPU-only PyTorch
  into an isolated toolbox-derived container, attach it to the backend network, and expose it only
  through a small local forwarder at `127.0.0.1:46763`. The UI health, root HTML, version/config,
  PostgreSQL migrations, embedding-model initialization, and gateway model discovery all passed;
  the UI-side gateway request advertised `local-gpt-oss` and the stable experiences.
- Fallback limitations: This runtime is not Compose-managed and its Open WebUI exec process must be
  relaunched after the container or Docker daemon restarts. Its upload/model cache is stored in the
  fallback container rather than the named Open WebUI volume, and `ffmpeg` is absent, so media/audio
  features were not validated. Chat routing and the persistent PostgreSQL-backed application state
  are functional.
- Suggested follow-up: Pin tested release tags or immutable digests, publish a compatibility
  matrix, and provide a resilient preflight/pull command with concise progress and retry
  reporting. A published project-owned UI image or a supported local wheel fallback would also
  avoid coupling first startup to two multi-gigabyte third-party transfers.

### 11. The documented local lint gate is broader than the implemented command

- Status: Open
- Area: `./hub lint`, `docs/TESTING.md`, local/CI parity
- Observed: The testing guide says `./hub lint` performs Python compilation, YAML/JSON/schema
  validation, repository guards, Compose rendering, and Helm lint/template in addition to Ruff
  and shell syntax. The command currently runs Ruff and shell syntax only; compilation is part of
  `./hub test`, while the remaining checks are split across GitHub Actions jobs.
- Impact: A contributor can follow the documented canonical local commands and still discover
  Compose or Helm failures only after pushing a branch.
- Current workaround: Run the Python, container/Compose, and Helm lanes explicitly before opening
  a pull request.
- Suggested follow-up: Make `./hub lint` execute the advertised checks, or introduce one local
  release-gate command and update the guide to describe the exact split.

### 12. Hub-created volumes produce Compose ownership warnings

- Status: Open; non-blocking
- Area: `./hub init`, named volume lifecycle
- Observed: Hub initialization creates every named volume directly with `docker volume create`.
  Later Compose commands warn that each existing volume was not created by Compose and recommend
  declaring it external, because the volume lacks Compose ownership labels.
- Impact: Normal startup emits warnings that look like a configuration mistake, and ownership is
  ambiguous even though `./hub destroy --yes` explicitly removes these volumes.
- Current workaround: Treat the warning as expected for volumes created by `./hub init`.
- Suggested follow-up: Pick one ownership model: let Compose create project-owned volumes, apply
  the expected Compose labels during initialization, or declare truly external volumes as such.

### 13. A cold gateway image build depends on an unbounded Debian mirror fetch

- Status: Open; local fallback used for validation
- Area: `deploy/docker/gateway.Dockerfile`, cold local builds
- Observed: The first gateway build remained in `apt-get update` for almost five minutes while
  fetching the 9.7 MB Debian package index. A host-side HEAD request to the same mirror timed out
  after ten seconds, so the build was canceled. The gateway also uses the floating
  `python:3.12-slim` base, which had advanced to Debian Trixie and invalidated the prior layer.
- Impact: A fresh clone cannot reliably build the otherwise small gateway when the mirror is slow,
  and rebuild behavior changes whenever the base tag moves.
- Current workaround: For this validation run, derive a local gateway runtime from the already
  built toolbox image; CI should still build the production Dockerfile on normal network egress.
- Suggested follow-up: Pin the tested Python base digest, add APT retry/timeout configuration, and
  consider a shared dependency/base stage or published gateway image so local startup does not
  duplicate a cold toolchain download.

### 14. Small non-streaming GPT-OSS requests can hide the answer or exceed common timeouts

- Status: Open performance/operations observation; model routing is functional
- Area: Local Ollama GPT-OSS inference and client timeout/token settings
- Observed: A low-reasoning request capped at 32 output tokens consumed its allowance on
  reasoning and returned no visible assistant content. A 128-token non-streaming request took
  about 216 seconds on this host and exceeded a 180-second client timeout, although the upstream
  request completed. Repeating the request with a 300-second timeout returned the expected
  `agent-ui-ok` response through the gateway.
- Impact: A valid local model route can look broken to clients that set a small output allowance
  or assume a conventional HTTP timeout, especially for non-streaming requests.
- Current workaround: Allow enough output tokens for both reasoning and the visible answer, use
  streaming where practical, and set local inference timeouts from measured hardware latency.
- Suggested follow-up: Add a documented local performance preflight and recommended defaults for
  reasoning-capable models, including first-token/total latency, streaming, token allowance, and
  timeout guidance.

### 15. Large container operations can starve Docker's management plane

- Status: Open host/image-store issue; currently blocks live catalog publication
- Area: Local Docker overlay/image metadata path
- Observed: Committing the cache-cleaned Open WebUI fallback container (about 5.2 GB of writable
  runtime state) produced no result for more than 13 minutes. During the commit, the Docker daemon
  repeatedly timed out health-check and exec creation across unrelated containers and logged
  closed-FIFO and context-deadline errors. Canceling the commit restored normal inspection and the
  PostgreSQL/gateway services returned healthy.
- Impact: A large image snapshot can make otherwise healthy workloads look failed and prevents
  reliable service inspection while the daemon is busy. This is broader than the slow
  `docker system df` observation above.
- Current workaround: Do not snapshot the large fallback while live services share this daemon;
  run the already-installed container directly and use the loopback forwarder for UI access.
- Suggested follow-up: Diagnose the host's overlay/containerd metadata and I/O behavior in a
  maintenance window, reproduce with daemon metrics enabled, and use a reproducible multi-stage
  image build or a published image instead of `docker commit` for future fallback packaging.
- Recurrence on 2026-08-24: A completed 7.3 GiB GGUF upload through a temporary
  `ollama create` client printed `success` but the attached `docker run --rm` never exited. The
  resulting ghost container could not be stopped or killed through Docker, while unrelated
  `docker exec`, health-check creation, and a cached toolbox build timed out. The daemon logged
  `only one connection allowed`, closed-FIFO, and context-deadline errors. Existing inference and
  application processes remained reachable, so the daemon was deliberately not restarted while
  unrelated workloads were active. This shows that the fault is not limited to `docker commit`;
  long container attach/cleanup paths can trigger the same host-wide management-plane failure.
- Current severity: The second recurrence accumulated roughly 600 tasks in uninterruptible `D`
  state, including more than 300 `runc` processes, and raised the host load average above 700.
  Read-only Docker metadata calls and existing HTTP inference/UI requests still worked, but new
  exec, health-check, create, remove, and build operations did not. The imported Ollama models and
  rendered Agent UI catalog are intact; publishing that catalog to the named runtime volume must
  wait for a maintenance window because a Docker restart or host reboot can interrupt unrelated
  workloads. Stop issuing container lifecycle commands once this signature appears, since every
  attempted exec or health check can add another stuck runtime process.

### 16. A host-wide HTTPS listener blocks the default Tailscale Serve port

- Status: Open host-networking observation; tailnet-only workaround applied
- Area: Remote UI access through Tailscale Serve
- Observed: The existing `devpi-caddy-1` container publishes TCP port 443 on `0.0.0.0` and
  `[::]`. Tailscale accepted a tailnet-only HTTPS proxy configuration on port 443, but
  `tailscaled` could not bind either Tailscale address and the client saw a TLS internal error.
- Impact: A valid-looking `tailscale serve status` entry can remain unreachable when another
  service owns the same port on every host address.
- Current workaround: Keep the existing Caddy listener and unrelated Tailscale routes intact,
  and expose the loopback-only Open WebUI fallback at
  `https://ucs.tail9d8219.ts.net:8444/`. The route is tailnet-only (Funnel is not enabled), and
  certificate validation plus the root page, `/health`, and `/api/version` were verified through
  Tailscale.
- Suggested follow-up: Reserve and document host ports used by Caddy and Tailscale Serve, and add
  a listener-conflict preflight before installing a new Serve route.

### 17. Shallow UI probes passed after its database and gateway stopped

- Status: Open runtime-observability issue; services restored
- Area: Open WebUI fallback health and core-service lifecycle
- Observed: The PostgreSQL and gateway containers stopped at the same instant with exit code 137
  and `OOMKilled=false`, then remained stopped despite their `unless-stopped` policy. Docker's
  retained event stream did not identify the initiator. The separately managed Open WebUI
  fallback stayed up, and both `/health` and `/api/version` still returned success, but
  `/api/config` returned HTTP 500 because the backend could no longer resolve or connect to
  PostgreSQL. The browser consequently displayed its misleading frontend-only/backend-required
  page.
- Impact: A Tailscale or local reachability check can report success while the browser UI is
  unusable and model routing is offline.
- Current workaround: Restore the core services with
  `docker compose -f compose.yaml -f .agent-ui/compose.generated.yaml up -d postgres gateway`,
  then verify `/api/config` through the same URL used by the browser. PostgreSQL, gateway model
  discovery, local `/api/config`, and tailnet `/api/config` all passed after recovery.
- Suggested follow-up: Manage the fallback UI in the same lifecycle as its dependencies and make
  the readiness probe exercise `/api/config` plus gateway model discovery. Add explicit alerting
  or restart diagnostics when required containers remain stopped.

### 18. Public SearXNG providers can throttle or challenge this host

- Status: Open; non-blocking, search returns results through other providers
- Area: Optional SearXNG web-search profile
- Observed: A live general search returned 25 structured results, but Brave temporarily returned
  HTTP 429 and Startpage redirected to a CAPTCHA. SearXNG suspended those engines and continued
  serving results from the remaining provider set. A default Wikidata startup request also
  received HTTP 403, while the default Ahmia and Torch definitions could not load without their
  Tor transport.
- Impact: Result mix and latency can vary by source IP, and noisy upstream warnings can look like
  total search failure even when the metasearch request succeeds.
- Current workaround: Remove the Tor-only engines and blocked Wikidata initializer from the local
  profile, retain SearXNG's multi-provider failover, and validate the returned JSON result count
  rather than treating any single-engine warning as a failed search.
- Suggested follow-up: Measure provider reliability over time and ship a deliberately curated
  engine set, health summary, or optional paid-provider path for operators who need predictable
  coverage.

### 19. GGUF discovery does not validate candidate integrity or chat suitability

- Status: Open product gap; invalid and non-chat candidates excluded manually
- Area: `./hub model discover`, local model inventory
- Observed: The recursive inventory found `mythomax-l2-13b.Q4_K_M.gguf`, but the file was only
  about 354 MiB and a llama.cpp probe reported tensor data outside the file bounds before the
  probe process exited abnormally. Discovery currently reports every file ending in `.gguf`
  without parsing metadata or checking tensor boundaries. The same disk inventory also contained
  diffusion, upscaler, and face-restoration weights that should not appear in a chat picker.
- Impact: A plausible filename can be registered as a chat model and fail only during a slow load,
  while unrelated weight formats require manual classification.
- Current workaround: Probe every GGUF before registration, retain the original source file, and
  exclude failed probes from the catalog. The corrupt Mythomax file was not imported or advertised.
- Suggested follow-up: Add a read-only discovery probe that reports GGUF version, architecture,
  parameter count, quantization, context, embedded template, tensor-range validity, and a clear
  `valid`, `invalid`, or `unverified` status.

### 20. Raw Ollama GGUF imports can lose the prompt template

- Status: Workaround applied locally; generic import support remains open
- Area: Ollama 0.20.0 GGUF import and generated local model manifests
- Observed: Three valid Qwen3 GGUF files imported successfully, but `ollama show` initially listed
  only the `completion` capability and the OpenAI chat endpoint produced malformed continuations.
  The GGUF metadata contained a Qwen3 chat template, but the imported Ollama manifest did not.
  Pygmalion 2 likewise required its custom `<|system|>`, `<|user|>`, and `<|model|>` role format.
- Impact: A model can pass import and load checks yet behave as if chat is broken; tools and
  thinking are also unavailable when the runtime manifest lacks the expected template.
- Current workaround: Create derived chat manifests that reuse the imported weight blobs. Qwen3
  uses the official Ollama Qwen3 template and stop parameters; Pygmalion uses the prompt format
  documented by its model card. The corrected Qwen3 manifests advertise completion, tools, and
  thinking, and the corrected Pygmalion manifest passed a story response test.
- Suggested follow-up: Extend the import workflow with explicit template selection, show the
  resulting Ollama capabilities, and require a short chat-level acceptance test before advertising
  an imported model.

### 21. The local Qwen3 GRPO checkpoint behaves unlike a general assistant

- Status: Open model-quality observation; model labeled experimental
- Area: `/home/akovanda/models/model.gguf`, local picker metadata
- Observed: Metadata identifies the file as a 4B Qwen3 GRPO Q8 checkpoint. It loads and tokenizes
  correctly with the official Qwen3 template, but an exact-response smoke prompt produced an
  unsolicited Chinese roleplay/scenario continuation rather than the requested answer.
- Impact: Runtime health alone would make this checkpoint look interchangeable with the 4B Qwen3
  Instruct model even though its behavior is materially different.
- Current workaround: Keep it available for comparison under an explicit `experimental` model ID,
  do not route stable chat/code/agent experiences to it, and do not advertise tool reliability.
- Suggested follow-up: Identify the original repository/model card from the GGUF hash, document
  its intended reward objective and prompt format, then add an evaluation set before promoting it.

### 22. Single-GPU model swaps have highly variable cold-load latency

- Status: Open performance limitation; models are usable
- Area: Ollama scheduling on the Tesla T4, client timeout guidance
- Observed: With `keep_alive: 0`, the corrected Qwen3 0.6B model took about 27 seconds for a cold
  load and exact response. The old GGUFv2 Pygmalion 2 13B model took about 122 seconds to load and
  about 133 seconds end to end for a 43-token response. A Qwen3 high-thinking request also spent
  its entire 128-token allowance on reasoning before emitting a visible answer.
- Impact: Switching picker entries can look like a hang, and small completion limits can yield an
  empty visible response from a healthy reasoning model.
- Current workaround: Use streaming, allow a multi-minute first-request timeout for the 13B story
  model, provide enough output tokens for reasoning plus the answer, and prefer the smaller models
  while comparing behavior interactively.
- Suggested follow-up: Surface warm/cold state and measured load latency in model metadata, add a
  swap-aware progress indicator, and tune per-model context/KV-cache settings for this 16 GiB GPU.

## Resolved during setup

### Local development extras omitted the CLI's `requests` dependency

- Status: Resolved in this PR
- Area: `pyproject.toml`, local test setup
- Observed: Installing `.[dev]` in a clean virtual environment left the control-plane tests unable
  to import `ops/hubctl.py` because that module imports `requests`, while the package metadata did
  not declare it.
- Impact: The container-based checks could pass because the toolbox image installed its own
  dependency set, but a normal contributor environment failed during test collection.
- Resolution: Added a bounded `requests` dependency to the development extra. A fresh local
  environment then completed all 78 tests and Ruff checks.

### External-only catalogs still forced the bundled llama.cpp service to start

- Status: Resolved in this PR
- Area: Generated Compose override and gateway startup dependencies
- Observed: The GPT-OSS catalog uses an existing OpenAI-compatible Ollama endpoint and has no
  local llama.cpp models, but the base Compose graph still required `llama` to start and report
  healthy before the gateway could start. That also forced a multi-gigabyte CUDA image pull that
  was irrelevant to this installation.
- Impact: API-served models were nominally supported by the catalog while a fresh external-only
  installation still depended on local llama.cpp, a GPU reservation, and its image lifecycle.
- Resolution: Runtime rendering now places llama.cpp behind an inactive Compose profile when no
  enabled local llama model exists and marks the gateway dependency optional. Registering an
  enabled local llama model leaves the original service and health dependency active. Catalog
  application also checks the effective Compose service set before explicitly recreating llama.cpp,
  so applying an external-only catalog does not activate the otherwise inactive profile.

### Root-owned runtime output was unreadable by the non-root gateway

- Status: Resolved in this PR
- Area: Runtime rendering and the shared runtime-config volume
- Observed: Copying the expanded catalog into the named runtime volume retained mode `0600` but
  assigned the file to root. The rebuilt gateway correctly ran as UID/GID 10001 and restarted with
  `PermissionError` while reading `/runtime/catalog.resolved.json`. The same ownership mismatch was
  possible when the root toolbox rendered the volume during normal catalog application.
- Impact: A valid catalog and healthy backend could still leave the gateway in a restart loop after
  an apply or recovery operation.
- Resolution: Runtime rendering now assigns the four private runtime files to a configurable
  service UID/GID (default `10001:10001`) after writing them. Compose and Hub rendering both pass
  that ownership contract, the files remain mode `0600`, and regression coverage checks all four
  ownership operations.

### The smoke test unconditionally required llama.cpp

- Status: Resolved in this PR
- Area: `./hub smoke`
- Observed: The smoke script always called `http://llama:8080/health`, even when the effective
  catalog contained only API-served models and the local llama.cpp service was intentionally
  disabled.
- Impact: A healthy external-only deployment could never pass its canonical smoke check.
- Resolution: The smoke script now inspects the resolved catalog and checks llama.cpp only when an
  enabled local llama model is present. PostgreSQL, gateway health, and advertised model discovery
  remain mandatory for every deployment.

### Restrictive checkout modes blocked shipped bind-mounted configuration

- Status: Resolved in this PR
- Area: `config/postgres-init`, `config/prometheus`, Compose bind mounts
- Observed: The privacy-preserving checkout umask also left the PostgreSQL init directory at
  `0700` and its files at `0600`. PostgreSQL restarted continuously with `Permission denied` while
  listing `/docker-entrypoint-initdb.d`. The Prometheus configuration uses the same host bind-mount
  pattern and would have failed for its non-root runtime user as well.
- Impact: Image builds and control-plane rendering could succeed while core service startup still
  failed solely because of host checkout modes.
- Resolution: Every Hub-mediated Compose operation now makes only the shipped, non-secret
  PostgreSQL and Prometheus bind sources readable/traversable. External model paths and generated
  secret state retain their operator-controlled permissions.

### Generated `.agent-ui/` state was not ignored

- Status: Resolved in this PR
- Area: `.gitignore`, `.dockerignore`, generated Compose and Kubernetes output
- Observed: The documentation says `.agent-ui/compose.generated.yaml` is local-only and
  must not be committed, but neither ignore file excluded `.agent-ui/`. Because the gateway
  Dockerfile copies the repository root, local generated state could also enter its image.
- Impact: A normal initialization could leave deployment-specific paths and generated
  configuration visible as untracked files, vulnerable to accidental commits, and present in
  the gateway build context/image.
- Resolution: Added `.agent-ui/` to both ignore files and excluded local coverage output from
  the Docker context.

### Open Terminal's domain allowlist silently became deny-all without `NET_RAW`

- Status: Resolved in this PR
- Area: Optional Open Terminal container capabilities and egress firewall
- Observed: The terminal started healthy with `NET_ADMIN`, but its nftables-compatible `iptables`
  frontend reported that it could not open the ipset socket. The final drop rule was still
  installed, so even allowlisted `github.com` requests timed out.
- Impact: The UI could advertise a healthy terminal that could not perform the permitted Git
  operations or downloads.
- Resolution: Added only the `NET_RAW` capability required while Open Terminal creates its ipset
  rule, then retained the upstream entrypoint's permanent `NET_ADMIN` drop. Runtime validation
  confirmed GitHub returned HTTP 200, `example.com` could not resolve, the tool ran as user
  `user`, and no Docker socket was present.

### SearXNG loaded irrelevant failing engines during startup

- Status: Resolved in this PR
- Area: Optional SearXNG settings
- Observed: Marking Ahmia, Torch, and Wikidata disabled was insufficient because SearXNG still
  imported and initialized their definitions. That emitted Tor-engine load errors and an HTTP 403
  stack trace on every startup.
- Impact: Startup logs obscured actionable failures even though general web search worked.
- Resolution: Used SearXNG's `use_default_settings.engines.remove` merge control to remove those
  three definitions before engine initialization. A clean restart no longer emits those errors.

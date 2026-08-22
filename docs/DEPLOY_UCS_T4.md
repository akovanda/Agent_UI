# Deploy on the UCS C240 M5 with a Tesla T4

## Target assumptions

- Ubuntu 24.04-class Linux host.
- 2× Xeon Gold 6132 and 192 GB RAM.
- One Tesla T4 with 16 GB VRAM.
- Docker Engine, Docker Compose v2, NVIDIA driver, and NVIDIA Container Toolkit.
- Enough Docker storage for images, PostgreSQL state, UI state, and at least 25–40 GB of model
  weights.

The deployment is intentionally pure Docker. Host Python, PostgreSQL, Helm, kubectl, and model
management CLIs are not required.

## 1. Verify the host

```bash
nvidia-smi
docker version
docker compose version
free -h
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
df -h
```

Confirm CUDA access from a container:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

The `./hub doctor --gpu` command repeats the important deployment checks after initialization.

## 2. Clone and initialize

```bash
git clone https://github.com/akovanda/Agent_UI.git /opt/local-ai-hub
cd /opt/local-ai-hub
./hub init
./hub ports
```

Initialization builds the toolbox, creates Docker-managed volumes, generates local secrets, and
chooses unused high loopback ports. It does not assume host PostgreSQL port 5432 is available.

Review `.env` with Vim before exposing anything beyond loopback:

```bash
vim .env
```

Keep `BIND_ADDRESS=127.0.0.1` for the initial deployment.

## 3. Import model files

Import the existing Stheno GGUF without copying it into Git:

```bash
./hub model import stheno-8b \
  /absolute/path/to/Llama-3.1-8B-Stheno-v3.4.Q4_K_M.gguf
```

Import GPT-OSS:

```bash
./hub model import gpt-oss-20b /absolute/path/to/gpt-oss-20b.gguf
```

The importer normalizes filenames into the Docker-managed model volume. Validate catalog and file
presence:

```bash
./hub model list
./hub model verify gpt-oss-20b
./hub model verify stheno-8b
./hub doctor --gpu
```

Record trusted SHA-256 hashes before treating downloaded weights as production inputs.

## 4. Start the normal stack

```bash
./hub up
./hub status
./hub smoke
```

This starts PostgreSQL, llama.cpp, the gateway, Open WebUI, and SillyTavern. Retrieve exact URLs
with `./hub ports` rather than assuming familiar port numbers.

Inspect startup and model transitions:

```bash
./hub logs llama
./hub logs gateway
```

## 5. Establish model baselines

Run:

```bash
./hub benchmark 3
./hub switch-regression 25
```

Record:

- cold and warm model-load time;
- prompt-processing and generation tokens per second;
- peak VRAM and system RAM;
- CPU utilization by socket;
- GPU temperature and power;
- actual context reported by llama.cpp;
- transition time in both directions.

The checked-in GPT-OSS configuration starts at 65,536 context tokens, Q8 KV cache, automatic fit,
and eight CPU MoE layers. Treat those values as a baseline hypothesis, not a guarantee.

## 6. Fixed-model fallback

If the selected llama.cpp build has a router regression, the repository includes a pure-Compose
single-model override. Run one backend at a time:

```bash
ACTIVE_MODEL_PATH=/models/gpt-oss-20b.gguf \
ACTIVE_MODEL_ALIAS=gpt-oss-20b \
ACTIVE_MODEL_CONTEXT=65536 \
  docker compose --env-file .env \
  -f compose.yaml -f compose.single-model.yaml \
  up -d --build llama gateway
```

For Stheno:

```bash
ACTIVE_MODEL_PATH=/models/Llama-3.1-8B-Stheno-v3.4.Q4_K_M.gguf \
ACTIVE_MODEL_ALIAS=stheno-8b \
ACTIVE_MODEL_CONTEXT=32768 \
  docker compose --env-file .env \
  -f compose.yaml -f compose.single-model.yaml \
  up -d --build llama gateway
```

In fallback mode, requests for the inactive backend return `503 model_unavailable` rather than
silently receiving a response from the wrong model.

## 7. Open WebUI first run

1. Open the Open WebUI URL printed by `./hub ports`.
2. Create the first administrator account.
3. Confirm `auto`, `assistant`, `assistant-fast`, `assistant-deep`, and `storyteller` appear.
4. Set `assistant` as the normal default.
5. Set `OPEN_WEBUI_ENABLE_SIGNUP=false` in `.env`.
6. Recreate Open WebUI with `./hub compose up -d --force-recreate open-webui`.
7. Test conversation history, Markdown, code, file upload, RAG, and mobile layout.

## 8. SillyTavern first run

Open the SillyTavern URL printed by `./hub ports` and configure:

```text
API type: OpenAI-compatible
Base URL: http://gateway:8000/v1 from inside Compose
API key: GATEWAY_API_KEY
Model: storyteller
Streaming: enabled
```

Use the host gateway URL for clients outside the Compose network. See
[SillyTavern](SILLYTAVERN.md) for campaign and lore guidance.

## 9. Hermes

After normal chat and switching are stable:

```bash
./hub up --agent
./hub ports
```

Add Hermes as a separate OpenAI-compatible connection in Open WebUI using the generated
`HERMES_API_KEY`. Do not mount the Docker socket, broad home directories, SSH keys, or production
credentials during the first test.

## 10. Private remote access

Preferred order:

1. keep all published ports bound to `127.0.0.1`;
2. expose only Open WebUI and SillyTavern through Tailscale or an authenticated TLS proxy;
3. keep PostgreSQL and llama.cpp private to the Docker network;
4. keep gateway and Hermes authentication enabled on every private-network path;
5. verify CORS origins and proxy headers against the actual browser URLs.

## 11. Baseline artifact

Create `docs/baselines/ucs-t4-YYYY-MM-DD.md` containing:

- hardware and driver output;
- exact image digests;
- model hashes and licenses;
- benchmark and switching results;
- selected context/offload settings;
- warnings and known limitations;
- rollback image and configuration.

Do not call the deployment stable until the baseline, fixed-model fallback, backup, restore, and
restart-persistence checks have all passed on the UCS.

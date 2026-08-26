# Local model handoff

Snapshot: 2026-08-26. This document records Andrew's current installation for the next worker. It
is not a generic Agent UI default or a promise that other installations have the same machines,
models, addresses, or capacity.

## Working entry points

| Purpose | Endpoint | Model IDs / notes |
|---|---|---|
| Main Open WebUI | `https://ucs.tail9d8219.ts.net:8444` | Preferred human UI; includes Agent UI continuity memory and configured tools. |
| Agent UI gateway on `ucs` | `http://127.0.0.1:55425/v1` | OpenAI-compatible; requires the installation gateway key. |
| Raw GPT-OSS from the tailnet | `http://ucs.tail9d8219.ts.net:11436/v1` | `gpt-oss-20b:64k`; tailnet-only Tailscale Serve TCP forward. |
| Raw desktop Ollama from the tailnet | `http://100.112.11.33:11434/v1` | `qwen3.5:4b-128k` and `qwen3.5:9b-128k`; use the literal tailnet IP. |

`kovanda-pi` can list both new desktop aliases and `gpt-oss-20b:64k`. A completion sent from the Pi
to `qwen3.5:4b-128k` returned `PI_OK`, so this is tested reachability rather than a DNS-only check.

The raw endpoints are intentionally separate from Agent UI. Calls made to them do not receive
continuity memory, Open WebUI web retrieval, tool configuration, or Agent UI experience aliases.
They also have no application-layer authentication; their current protection is tailnet reachability.

## Current long-context models

| Model | Quantization | Model maximum | Configured runtime context | Measured loaded footprint on desktop |
|---|---:|---:|---:|---:|
| GPT-OSS 20B | existing local build | 131,072 | 65,536 | Runs on `ucs`, not the desktop GPU. |
| Qwen 3.5 4B (`4.7B`) | `Q4_K_M` | 262,144 | 131,072 | 8.64 GB total, 6.18 GB assigned to VRAM. |
| Qwen 3.5 9B (`9.7B`) | `Q4_K_M` | 262,144 | 131,072 | 11.07 GB total, 5.95 GB assigned to VRAM. |

The desktop is a Dell Precision T5610 with 64 GB of system RAM and an 8 GB GTX 1070. It runs
Ollama `0.32.15` with the Vulkan backend. Both 128K aliases were loaded through the API, returned a
completion, and reported `context_length: 131072` from `/api/ps`. Approximate cold loads were 20
seconds for 4B and 23-26 seconds for 9B. The spill into system RAM is expected on this GPU.

The original `qwen3.5:4b` and `qwen3.5:9b` tags remain untouched. They advertise a 256K model
maximum but were observed loading with only a 4,096-token runtime context when no override was
provided. The installation therefore uses derived Ollama aliases created from the original tags
with `num_ctx=131072`. Temporary 64K aliases also remain available as
`qwen3.5:4b-64k` and `qwen3.5:9b-64k`; Agent UI does not select them.

128K is a capacity ceiling, not durable memory and not a reason to stuff every known fact into each
prompt. Continuity should retain durable user/game facts, while retrieval selects the relevant
subset for a turn. Very long prompts still cost ingestion time and can dilute attention.

## Reasoning controls

GPT-OSS provides genuine `low`, `medium`, and `high` effort levels. The current 64K GPT-OSS UI
profiles map to those upstream values. Ollama documents GPT-OSS as level-controlled rather than a
normal on/off thinking model.

The desktop Qwen 3.5 models are different. Ollama's OpenAI-compatible endpoint accepts
`reasoning_effort` values `none`, `low`, `medium`, `high`, and `max`, but on these exact models and
runtime the reliable control is binary:

- `none` disables thinking and returns no reasoning trace.
- Any of `low`, `medium`, `high`, or `max` enables thinking.
- There is no verified token budget or reliable graded amount of thought for Qwen 3.5.

Controlled, temperature-zero probes support that conclusion:

| Model | Compared labels | Result |
|---|---|---|
| Qwen 3.5 4B | `low` vs `high` | Identical 102-token completion and byte-identical reasoning trace. |
| Qwen 3.5 9B | `low` vs `medium` vs `high` | Identical 174-token completion and byte-identical reasoning trace. |

The UI still exposes `none`, `low`, `medium`, and `high` entries so future model/runtime upgrades can
be tested without changing the generic gateway contract. Their descriptions warn that Qwen may
treat all enabled labels equivalently. `max` is not exposed because it showed no additional effect.
For predictable MUD latency, use `none` for routine turns and an enabled profile for work that
benefits from a hidden scratchpad. Give enabled-thinking requests enough `max_tokens` for both the
trace and visible answer; a small completion budget can be exhausted before any answer is emitted.

References: [Ollama thinking capability](https://docs.ollama.com/capabilities/thinking) and
[Ollama create-model API](https://docs.ollama.com/api/create).

## Agent UI and Open WebUI mapping

The ignored installation overlay `.agent-ui/local-gpt-oss.yaml` points the desktop models at the
128K aliases. This keeps machine-specific addresses and model names out of the generic checked-in
catalog. The gateway advertises these experience IDs:

- 4B: `qwen35-desktop-none`, `qwen35-desktop-low`, `qwen35-desktop-medium`,
  `qwen35-desktop-high`
- 9B: `qwen35-9b-desktop-none`, `qwen35-9b-desktop-low`, `qwen35-9b-desktop-medium`,
  `qwen35-9b-desktop-high`

All eight IDs are present in `/v1/models`. End-to-end gateway probes for both `none` profiles
returned `OK`, and the desktop reported a live 128K allocation for each upstream alias.

The stable `story` experience now routes to Qwen 3.5 9B at 128K with thinking disabled by default.
This makes the normal long-form/game path meet the requested context floor. Pygmalion remains a
direct comparison model, but its architecture only supports 4K.

Open WebUI has same-ID Workspace Model records for all eight entries. Each uses
`params.function_calling=legacy` and advertises web-search capability, so enabled web retrieval is
performed before Qwen inference. This avoids the compact model's previously reproduced selection
of a calendar-search tool in place of web search.

## Short-context models still on disk

Not every historical model can be made 64K. The older Qwen 2.5 Coder files declare a 32K maximum,
the local Qwen 3 files declare 32K or 40K, and Pygmalion 2 declares 4K. They remain visible for the
requested model-comparison experiments, but they must not be selected for large-context MUD work.
The stable chat/code/agent/story paths and the MUD-ready model choices all meet the 64K floor: they
use the GPT-OSS 64K profiles or one of the desktop Qwen 3.5 128K families.

## Known follow-up issues

1. Qwen 3.5 effort labels are accepted but currently behave as an on/off switch. Re-test after
   Ollama or model upgrades and collapse the UI to two choices if graded behavior remains absent.
2. The 128K desktop profiles trade cold-start time and system-RAM spill for capacity. Benchmark
   long-prompt ingestion and sustained generation before placing them in a latency-sensitive game
   loop.
3. Desktop Ollama rejects the MagicDNS host in the HTTP `Host` header with 403. Continue using
   `100.112.11.33`, or add a private reverse proxy/stable service identity that normalizes the host.
4. Direct raw inference endpoints bypass continuity, web retrieval, tools, aliases, and gateway
   authentication. A MUD service needing those features should call the gateway or explicitly add
   equivalent orchestration rather than assuming raw Ollama provides them.
5. `./hub catalog validate .agent-ui/local-gpt-oss.yaml` treats the merge overlay as standalone and
   complains that the disabled inherited `local-llama` backend has no `kind`. The merge-aware
   `catalog plan` and `catalog apply` paths succeed. Validation should understand overlay mode or
   explain the distinction.
6. The tracked UI override is an installation database record, not catalog state. Add a supported,
   idempotent automation path for per-model Open WebUI parameters so rebuilds do not require direct
   database reconciliation.

# Model Tuning

## Principles

1. Preserve a known-good baseline.
2. Change one variable per benchmark.
3. Record exact llama.cpp build, image digest, model hash, prompt, and context.
4. Tune for sustained user experience, not one favorable tokens/second number.
5. Leave VRAM safety margin for KV cache, CUDA workspace, and allocator fragmentation.

## Starting presets

| Alias | Model | Context target | KV cache | Fit margin | Intended use |
|---|---|---:|---|---:|---|
| `gpt-oss-20b` | GPT-OSS 20B MXFP4 | 64K | Q8 K/V | 1024 MiB | Assistant and Hermes profiles |
| `stheno-8b` | Stheno Q4_K_M | 32K | Q8 K/V | 1024 MiB | Story/RPG |

`n-gpu-layers=auto` and `fit=on` are intentional initial choices. They allow current llama.cpp to
find a viable CPU/GPU split instead of encoding an unmeasured layer count.

## GPT-OSS matrix

Test in this order:

| Test | Context | KV | CPU/MoE adjustment | Purpose |
|---|---:|---|---|---|
| A | 16K | Q8 | automatic | establish stable minimum |
| B | 32K | Q8 | automatic | preferred everyday profile |
| C | 64K | Q8 | automatic | Hermes target |
| D | 64K | Q4 | automatic | recover KV memory if needed |
| E | 64K | Q8 | first 4 MoE layers on CPU | create VRAM margin |
| F | 64K | Q8 | first 8 MoE layers on CPU | last-resort stable agent profile |

For E/F change `n-cpu-moe` on the `gpt-oss-20b` backend in the catalog. Because the assistant
and Hermes virtual profiles share the same loaded weights, backend context/offload settings apply
to both. Measure prompt ingestion and output tokens/second; host-RAM fit is not useful if latency
makes agent loops impractical.

## Stheno matrix

Stheno should fit comfortably. Optimize it for story experience:

| Variable | Values |
|---|---|
| Context | 16K, 32K, 64K |
| Temperature | 1.1, 1.25, 1.4 |
| `min_p` | 0.08, 0.12, 0.2 |
| Repeat penalty | 1.05, 1.08, 1.12 |
| Lore strategy | raw history vs summaries + lorebook |

Judge:

- character voice consistency;
- repetition;
- willingness to advance a scene;
- adherence to player agency;
- continuity after summaries;
- speed to first visible prose.

Do not infer that a 128K architectural limit means 128K is the best active story context. A focused
32K window plus structured lore and summaries may produce better attention and faster prompts.

## CPU topology

The default uses 42 of 56 logical threads to leave capacity for Docker, UIs, PostgreSQL, and the
agent harness. Test:

- 28 threads, approximately one per physical core if topology confirms it;
- 36 threads;
- 42 threads;
- NUMA `distribute` versus default.

Measure prompt processing separately from generation. More threads can reduce performance when
cross-socket traffic and memory bandwidth become the bottleneck.

## Benchmark procedure

```bash
./hub benchmark 3
nvidia-smi dmon -s pucvmet -d 1
# In another terminal:
docker stats
```

The included benchmark reports approximate token throughput because generic SSE chunks do not
always include exact token counts. For final baselines, also capture llama.cpp's native timings and
usage fields.

## Acceptance record

For every accepted preset, record:

- cold load seconds;
- warm first-token latency;
- prompt tokens/second;
- generated tokens/second;
- max VRAM and system RAM;
- context actually allocated;
- output quality notes;
- failure count over 25 transitions;
- container/model hashes.

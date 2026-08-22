# Model and Runtime Tuning

Tuning belongs to a registered model deployment, not to Agent UI's source code. Start conservatively, measure on the target hardware, and store only the resulting runtime options in the catalog overlay.

## Local llama.cpp runtime

```yaml
models:
  my-model:
    backend: local-llama
    capabilities: [chat]
    artifact:
      kind: host_path
      path: /srv/models/example.gguf
    runtime:
      ctx-size: 32768
      n-gpu-layers: auto
      cache-type-k: q8_0
      cache-type-v: q8_0
      batch-size: 2048
      ubatch-size: 512
```

Runtime keys map to llama.cpp preset arguments without leading dashes. Agent UI does not hard-code architecture-specific values.

## Establish a baseline

```bash
./hub doctor --gpu
./hub model verify MODEL_ID
./hub up
./hub benchmark 5
```

Record:

- model load time;
- prompt-processing tokens per second;
- generation tokens per second;
- first-token latency;
- VRAM and host RAM usage;
- context length;
- request failures and fallback behavior;
- output quality on representative workloads.

Store baseline reports outside model-weight directories, then compare one variable at a time.

## Context length

A model's architectural maximum is not automatically the best operational context. Larger contexts increase KV-cache memory, prompt ingestion time, and attention cost.

Start with the smallest window that supports the workload:

- interactive chat: often 8K–32K;
- repository/code work: often 16K–64K;
- agent/tool traces: workload-dependent;
- long-form story: combine an active window with summaries and lore retrieval rather than retaining every raw token indefinitely.

Use context compaction and retrieval before assuming a very large raw window will improve quality.

## GPU offload

`n-gpu-layers: auto` is a reasonable starting point. For architectures with mixture-of-experts weights, runtime-specific CPU-offload controls may be required. Declare those options only after confirming the runtime and model architecture support them.

Keep a VRAM safety margin for:

- KV cache;
- CUDA/runtime allocations;
- prompt batches;
- temporary buffers;
- context growth.

A configuration that barely loads may fail during a real request.

## KV cache

Quantized KV cache can reduce memory pressure. Validate output quality and long-context stability for the actual model before adopting aggressive cache quantization.

## Batch and micro-batch

Prompt throughput may improve with larger batch sizes, but peak memory rises. Interactive single-user latency and multi-user throughput have different optimal settings.

Agent UI's single-model coordination protects a constrained GPU, but it does not make an oversized batch safe.

## Concurrency

For one local GPU and dynamically switched models:

```yaml
backends:
  local-llama:
    coordinator: explicit
    serialize_requests: true
```

This keeps the load transition and complete streamed response under one lease. Remote or independently scaled endpoints should normally disable serialization.

## Sampling defaults by experience

Sampling belongs in experiences:

```yaml
experiences:
  code:
    capability: code
    defaults:
      temperature: 0.25
      top_p: 0.9
  story:
    capability: story
    defaults:
      temperature: 1.1
      top_p: 0.95
```

Explicit request values override experience defaults. Avoid applying creative settings to deterministic tasks simply because one model serves both experiences.

## Reasoning effort

Reasoning can change latency and output-token usage substantially. Benchmark each declared effort level:

```yaml
features:
  reasoning:
    values:
      fast: low
      balanced: medium
      deep: high
```

Do not assume labels are comparable across model families. The mapping establishes a stable client vocabulary while preserving backend-specific semantics.

## Capability-specific evaluation

Use separate evaluation prompts for:

- chat factuality and instruction following;
- code correctness, tests, and tool use;
- story continuity, voice, and character consistency;
- image prompt adherence and artifact quality;
- embedding retrieval quality;
- reranking precision;
- agent loop completion and safety.

A model that excels at one capability should not automatically receive high priority for all others.

## Change discipline

1. Capture a baseline.
2. Change one runtime or sampling parameter.
3. Repeat the same workload.
4. Compare performance and quality.
5. Keep the change only if the result is reproducible.
6. Record target hardware, runtime image, model hash, and catalog revision.

## Acceptance gates

Before declaring a local configuration stable:

```bash
./hub smoke
./hub switch-regression 25
./hub benchmark 5
```

Then run a sustained soak with realistic prompt sizes and context growth. Monitor GPU memory, load failures, cancellation cleanup, and model switching under client disconnects.

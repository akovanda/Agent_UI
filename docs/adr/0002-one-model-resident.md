# ADR 0002: Keep One Model Resident on the Tesla T4

- Status: Accepted
- Date: 2026-08-21

## Context

GPT-OSS 20B MXFP4 and Stheno 8B Q4_K_M together exceed the 16 GB T4 before KV cache and runtime
allocations. Attempting to retain both in VRAM would force substantial offload, reduce usable
context, and make out-of-memory behavior harder to predict.

## Decision

Run llama.cpp with a maximum of one active model. The gateway serializes inference and explicitly
unloads the current backend before loading a different backend. Same-model requests reuse the warm
model. A fixed-model Compose override is retained as a fallback for router regressions.

## Consequences

- Warm requests are simple and predictable.
- Switching profiles has visible cold-load latency.
- Concurrent assistant/story use is queued rather than parallel.
- The architecture can later increase `models-max` when a larger GPU is installed, but only after
  concurrency and memory tests are updated.

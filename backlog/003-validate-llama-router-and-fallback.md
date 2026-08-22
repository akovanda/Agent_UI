# Validate llama.cpp router mode and fixed-model fallback

## Outcome

Prove one-model-at-a-time loading is deterministic on the T4 and that a router regression cannot
block ordinary use.

## Scope

- Current router preset format and model aliases
- Explicit `/models/unload` and `/models/load`
- One loaded model maximum
- Fixed-model Compose override
- Client cancellation and failed-load recovery

## Tasks

- [ ] Start router mode with both presets advertised and none loaded.
- [ ] Load each backend through the gateway and record cold/warm times.
- [ ] Run 25 alternating assistant/story transitions.
- [ ] Cancel requests during loading and streaming; check lease/process recovery.
- [ ] Deliberately misconfigure one filename and verify bounded 503 behavior.
- [ ] Verify no orphan processes or stale VRAM allocation.
- [ ] Run assistant, story, and Hermes fixed-model fallback modes.
- [ ] Document exact router build and any required workarounds.

## Acceptance criteria

- 25/25 alternating transitions return from the correct model.
- At most one model process is loaded at any time.
- Same-model requests do not reload unnecessarily.
- Cancellation cannot deadlock the GPU lease.
- Bad model configuration fails within the configured timeout.
- All three fixed-model fallback modes answer successfully.

## Dependencies

- Validate the UCS host and NVIDIA container runtime.
- Pin container images and establish model checksums.

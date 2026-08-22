# Pin container images and establish model checksums

## Outcome

Replace moving deployment inputs with a reproducible, rollback-capable baseline.

## Scope

- llama.cpp CUDA server image
- gateway image/commit
- Open WebUI
- SillyTavern
- Hermes Agent
- PostgreSQL/pgvector
- Prometheus
- GPT-OSS and Stheno GGUF files
- Python gateway dependency lock/constraints

## Tasks

- [ ] Pull candidate images on the UCS.
- [ ] Record immutable image digests.
- [ ] Compute SHA-256 for both GGUF files.
- [ ] Generate and review a hash-locked gateway dependency set for the target Python version.
- [ ] Verify model source, filename, size, and license notice.
- [ ] Update `.env` or a deployment baseline file to immutable values.
- [ ] Retain previous known-good images for rollback.
- [ ] Add a script/check that warns when deployed values differ from the baseline.

## Acceptance criteria

- A clean host can reproduce the exact image/model and gateway dependency set from recorded identifiers.
- `docker inspect` confirms deployed images match pinned digests.
- Both model hashes match the recorded values.
- Stheno's non-commercial constraint is visible in deployment documentation.
- Rollback identifiers are documented.

## Dependencies

- Validate the UCS host and NVIDIA container runtime.

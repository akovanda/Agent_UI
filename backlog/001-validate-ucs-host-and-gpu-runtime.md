# Validate the UCS host and NVIDIA container runtime

## Outcome

Prove that the target UCS C240 M5 can run the pure-Docker Local AI Hub and CUDA-enabled llama.cpp
reliably before model or UI tuning begins.

## Scope

- Record Ubuntu, kernel, Docker, Compose, NVIDIA driver, T4-visible state, and NVIDIA Container
  Toolkit versions.
- Confirm Docker exposes the Tesla T4 to the configured llama.cpp CUDA image.
- Confirm Docker volume storage has sufficient free space and acceptable I/O.
- Check NUMA topology, CPU affinity options, RAM, swap, thermals, and power behavior.
- Run the containerized initialization, model checks, Compose rendering, and GPU doctor.

## Tasks

- [ ] Clone the repository onto SSD-backed storage.
- [ ] Install or verify Docker Engine and Compose v2.
- [ ] Install or verify the NVIDIA driver and Container Toolkit from supported packages.
- [ ] Run `nvidia-smi` on the host and from a disposable CUDA container.
- [ ] Run `./hub init` and verify all assigned host ports are unused and loopback-bound.
- [ ] Import or fetch both GGUFs through `./hub model ...` commands.
- [ ] Run `./hub model verify gpt-oss-20b` and `./hub model verify stheno-8b`.
- [ ] Run `./hub doctor --gpu` and resolve every fatal finding.
- [ ] Record `lscpu`, NUMA, memory, disk, and GPU details in a redacted baseline.
- [ ] Decide whether swap remains enabled and document the reason.

## Acceptance criteria

- `./hub doctor --gpu` reports zero fatal issues.
- A container sees one Tesla T4 with approximately 16 GiB VRAM.
- `docker compose config`, generated runtime configuration, and Helm rendering pass.
- At least 30 GiB remains free after both model files are imported.
- The recorded baseline contains exact versions and no secrets.

## Dependencies

None.

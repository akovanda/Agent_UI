# Add GPU, host, service, and model observability

## Outcome

Make hangs, queueing, model-load failures, resource pressure, and quality-impacting changes visible
without inspecting every container manually.

## Scope

- Prometheus scrape configuration
- NVIDIA GPU exporter/DCGM or equivalent
- Host metrics
- Dashboard(s)
- Alerts for actionable local failures
- Log retention and privacy

## Tasks

- [ ] Add GPU utilization, VRAM, temperature, power, and error metrics.
- [ ] Add CPU, RAM, swap, disk, filesystem, and load metrics.
- [ ] Build dashboard for requests, routes, queue wait, latency, model loads, and errors.
- [ ] Add model-switch timeline and current loaded backend.
- [ ] Add alerts for service down, repeated load failure, GPU OOM, disk low, backup stale, and high
      temperature.
- [ ] Verify labels/logs contain no prompt, document, or secret contents.
- [ ] Define retention and backup behavior for metrics/logs.

## Acceptance criteria

- A single dashboard explains whether a slow request waited, loaded a model, evaluated a prompt, or
  generated slowly.
- Simulated service/model/disk failures trigger actionable alerts.
- Metrics cannot reconstruct user prompt content.
- Observability survives ordinary stack restart and has bounded disk growth.

## Dependencies

- Baseline stack operational and pinned.

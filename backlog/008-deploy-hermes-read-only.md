# Deploy Hermes Agent with a read-only capability baseline

## Outcome

Expose a useful GPT-OSS agent through Open WebUI without granting mutation or shell privileges in
the first deployment.

## Scope

- Hermes Docker/profile deployment
- Custom provider through the gateway
- 64K target and reduced-context fallback
- Read-only tools only
- Open WebUI second-provider connection
- Separate state, key, logs, and acceptance tests

## Tasks

- [ ] Validate official Hermes image/command/config against the pinned release.
- [ ] Confirm Hermes sees the intended `hermes-agent` backend.
- [ ] Benchmark 64K target; define a lower safe context if fit/offload is unacceptable.
- [ ] Enable only low-risk read capabilities with explicit path/source allowlists.
- [ ] Disable shell, SSH, writes, external communication, and infrastructure mutation.
- [ ] Connect Open WebUI as a separate provider.
- [ ] Test multi-step read-only tasks, cancellation, max-turn limits, and prompt injection.
- [ ] Verify Hermes state backup and restore.

## Acceptance criteria

- Hermes completes representative multi-step read-only tasks.
- No enabled tool can mutate host, repository, email, calendar, or devices.
- Agent failure does not affect direct assistant/story chat.
- Context use fits reliably or a documented lower fallback is selected.
- Open WebUI clearly distinguishes direct assistant and Hermes agent paths.

## Dependencies

- Configure and harden Open WebUI for daily use.
- Validate llama.cpp router mode and fixed-model fallback.

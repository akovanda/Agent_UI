# Optionally deploy an isolated self-hosted GitHub Actions runner

## Outcome

Provide a controlled alternative to the current GitHub-hosted CI runner when private-repository
billing, network locality, or policy makes a self-hosted runner preferable—without exposing
production credentials or model data to workflow jobs.

## Scope

- Dedicated non-root runner identity, container, or VM
- A separate self-hosted workflow or reviewed `runs-on` switch
- Docker-based test, lint, Compose, and Helm validation
- Network and filesystem isolation
- Patch, cleanup, and revocation procedure
- No model or GPU dependency for ordinary CI

## Tasks

- [ ] Decide whether the public/GitHub-hosted workflow is sufficient.
- [ ] Create a dedicated runner account or isolated container/VM when self-hosting is justified.
- [ ] Register explicit labels such as `self-hosted`, `linux`, `x64`, and `local-ai-hub`.
- [ ] Add a runner-specific workflow or deliberately change `ci.yml`; do not assume current labels.
- [ ] Ensure checkout cannot read production `.env`, backups, model weights, UI state, or Docker
      volumes.
- [ ] Install Docker Engine/CLI and Compose; all project language tools remain in the toolbox image.
- [ ] Run the CI workflow from a pull request and verify cancellation/concurrency behavior.
- [ ] Define runner updates, cleanup, logs, and emergency removal.
- [ ] Keep runner credentials separate from Hermes and any GitHub tool credentials.

## Acceptance criteria

- Pull-request CI runs tests, coverage, lint, shell checks, Compose rendering, Helm rendering, image
  builds, and repository guards.
- CI succeeds without GPU or model files.
- Workflow jobs cannot access production secrets or state.
- Superseded runs cancel promptly.
- The runner can be revoked and rebuilt from documentation.

## Dependencies

- Repository published and default-branch protections decided.

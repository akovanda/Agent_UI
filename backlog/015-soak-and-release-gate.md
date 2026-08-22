# Complete two-week soak and routine-use release gate

## Outcome

Promote the platform from engineering baseline to dependable daily assistant and story service.

## Scope

- Representative daily use
- Reliability, routing, memory, performance, and persistence
- Restart/reboot behavior
- Backup and recovery confidence
- Issue triage and release baseline

## Tasks

- [ ] Complete all release-gate tests in `docs/TESTING.md`.
- [ ] Use assistant and storyteller daily for two weeks.
- [ ] Exercise at least one long technical session, one document workflow, and one multi-session
      campaign.
- [ ] Reboot the host and verify automatic recovery.
- [ ] Track hangs, wrong routes, hallucination patterns, state loss, and unacceptable latency.
- [ ] Resolve release-blocking defects and rerun affected tests.
- [ ] Record final pinned images, model hashes, settings, and benchmarks.
- [ ] Tag the known-good repository release.

## Acceptance criteria

- No unexplained deadlock, orphan model, wrong-backend response, corrupt state, or namespace leak.
- Warm chat is comfortably interactive and cold switches are measured/understood.
- Open WebUI and SillyTavern state survive restart/reboot.
- Backup and staged restore have passed.
- Default remote exposure remains private.
- Known limitations and rollback steps are documented.
- A versioned release tag identifies the deployed baseline.

## Dependencies

- Router/fallback validation.
- UI configuration.
- Remote access.
- Backup/restore drill.
- Observability.

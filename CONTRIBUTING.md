# Contributing

This is an infrastructure repository. Changes should be small, reversible, and backed by a
specific operational or user-quality outcome.

## Workflow

1. Create or select an issue with acceptance criteria.
2. Branch from `main` using `feat/`, `fix/`, `docs/`, or `ops/`.
3. Do not commit model weights, runtime data, secrets, private conversations, or backups.
4. Run `./hub test`, `./hub lint`, and relevant Compose/smoke tests.
5. Open a pull request using the template.
6. Record target-host validation separately when the change affects CUDA, llama.cpp, model
   settings, storage, networking, or persistent data.
7. Squash-merge after review and green CI.

## Definition of done

- behavior has automated tests where practical;
- operational changes update the runbook;
- security boundaries are unchanged or explicitly reviewed;
- logs/metrics do not contain prompt or secret contents;
- rollback is documented;
- acceptance criteria are demonstrated on the appropriate environment.

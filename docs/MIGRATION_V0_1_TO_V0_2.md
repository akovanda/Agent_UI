# Migrating from 0.1.x to 0.2.0

Version 0.2.0 changes the deployment contract. Compose remains the default, but inference and operations are now containerized and mutable state is standardized on explicit named volumes.

## Before migration

1. Keep the 0.1.x repository archive and Git tag.
2. Record existing model paths and SHA-256 digests.
3. Export gateway and Open WebUI databases.
4. Back up SillyTavern and Hermes state.
5. Stop the 0.1.x stack without deleting data.

## Important differences

| 0.1.x | 0.2.0 |
|---|---|
| Host-side operational dependencies possible | Toolbox container owns operations |
| Host model directories or mixed mounts | Explicit model named volume/PVC |
| Familiar fixed ports | Generated unused high loopback ports |
| Multiple deployment entrypoints | `compose.yaml` + `./hub` are canonical |
| Kubernetes scaffolding | Helm chart with the same topology |
| Manual model filename alignment | Catalog and canonical importer |

## Recommended migration

Initialize 0.2.0 as a separate Compose project first:

```bash
cp .env.example .env.migration-template
COMPOSE_PROJECT_NAME=local-ai-hub-v02 ./hub init
```

The normal `./hub init` writes `COMPOSE_PROJECT_NAME=local-ai-hub`. For a parallel validation deployment, edit `.env` to use a distinct project name and distinct volume names before starting it.

Import model files through the new control plane:

```bash
./hub model import gpt-oss-20b /old/path/gpt-oss.gguf
./hub model import stheno-8b /old/path/Stheno.gguf
```

Start and validate:

```bash
./hub up
./hub smoke
```

Then import application data using the 0.2.0 restore path or application-native exports.

## PostgreSQL ports

Do not carry forward a fixed `5432:5432` mapping. Run:

```bash
./hub init --reallocate-ports
./hub ports
```

Container-to-container PostgreSQL traffic still uses `postgres:5432`; only the host-facing loopback port changes.

## Secrets

Do not copy old API keys blindly into a public or shared file. Version 0.2.0 generates a mode-0600 `.env`. Existing PostgreSQL credentials require deliberate migration because changing the environment value does not automatically change the password inside an existing database cluster.

## Cutover

After both assistant and story acceptance tests pass:

1. Stop the 0.1.x deployment.
2. Start 0.2.0 with the intended project and volume names.
3. Confirm Open WebUI administration and disable public signup.
4. Confirm SillyTavern campaign persistence.
5. Run the alternating-model regression and T4 benchmarks.
6. Keep the 0.1.x backups through at least one successful 0.2.0 backup/restore drill.

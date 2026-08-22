# Operations and recovery

## Health sequence

```bash
./hub status
./hub doctor --gpu
./hub smoke
```

`doctor` validates generated secrets, unique high ports, catalog shape, Docker, Compose, and optionally GPU visibility. `smoke` validates PostgreSQL, llama.cpp, gateway health, and OpenAI-compatible model discovery from inside the Compose network.

After both models are imported, validate switching and collect a repeatable baseline:

```bash
./hub switch-regression 25
./hub benchmark 3 | tee benchmarks/t4-baseline.csv
```

The switch regression alternates assistant and storyteller requests and fails when the response reports the wrong virtual or base model. The benchmark records end-to-end latency and token usage; use llama.cpp metrics for lower-level prompt and generation rates.

## Logs

```bash
./hub logs postgres
./hub logs llama
./hub logs gateway
./hub logs open-webui
./hub logs sillytavern
./hub logs hermes
```

For a one-time snapshot:

```bash
./hub compose logs --tail=500 SERVICE
```

## Common startup failures

### Port already allocated

Reallocate host ports:

```bash
./hub init --reallocate-ports
./hub up
```

The operation preserves existing secrets.

### GPU not visible

```bash
./hub doctor --gpu
```

Verify NVIDIA driver, NVIDIA Container Toolkit, daemon restart, and `docker run --gpus all` independently of the application.

### llama.cpp starts but a model fails to load

```bash
./hub model list
./hub model verify MODEL_ID
./hub logs llama
```

Likely causes include a missing file, unexpected filename, unsupported GGUF, insufficient VRAM, excessive context, or incompatible llama.cpp image.

Reduce context or increase CPU offload in the catalog, then:

```bash
./hub model render
./hub restart llama
```

### Open WebUI has no models

Confirm gateway discovery:

```bash
./hub smoke
```

Then verify Open WebUI's configured endpoint is `http://gateway:8000/v1` and its key matches `GATEWAY_API_KEY`.

### SillyTavern cannot connect

Inside Compose, use `http://gateway:8000/v1`, not the randomized host port. The randomized URL is only for clients outside the Docker network.

## Data inventory

Use `docker volume inspect` on names stored in `.env`. Important resources:

- PostgreSQL volume.
- Model volume.
- Open WebUI volume.
- SillyTavern volumes.
- Hermes volume.
- Gateway and catalog state volumes.

## Containerized backup and restore

Create a logical PostgreSQL backup plus application-state archives:

```bash
./hub backup
```

Choose a destination and include the large model volume when needed:

```bash
./hub backup /secure/backups/local-ai-hub-2026-08-21 --include-models
```

The backup command briefly quiesces running gateway/frontend services, dumps both databases from inside PostgreSQL, archives managed state volumes through the toolbox container, copies the deployment environment, and writes SHA-256 checksums. The environment copy contains plaintext secrets and must be protected.

Restore into the current deployment configuration:

```bash
./hub restore /secure/backups/local-ai-hub-2026-08-21 --yes
./hub smoke
```

Restore recreates PostgreSQL, restores the application volumes that are present, regenerates runtime configuration, and starts the default stack. It does not silently replace the active `.env`; database dumps are restored under the credentials of the current deployment.

## Backup policy

A production backup should include:

1. PostgreSQL logical dumps for `local_ai_hub` and `open_webui`.
2. Open WebUI local data volume.
3. SillyTavern data/config/plugins/extensions.
4. Hermes data if enabled.
5. The catalog overlay and generated runtime metadata.
6. `.env`, encrypted at rest.
7. Model checksums and acquisition records.

Model weights can be omitted from frequent backups when they are reproducibly downloadable and verified, but locally unique or hard-to-reacquire weights should be backed up.

Do not treat a tar file that has never been restored as a backup. Periodically restore into a separate Compose project and run the smoke suite.

## Upgrade procedure

1. Back up and test the backup.
2. Pin intended image tags in `.env`.
3. Pull images:

   ```bash
   ./hub compose pull
   ```

4. Rebuild local images:

   ```bash
   ./hub compose build --pull gateway config-init toolbox
   ```

5. Apply:

   ```bash
   ./hub up
   ```

6. Run:

   ```bash
   ./hub smoke
   ```

7. Test assistant chat, file/RAG behavior, story generation, and model switching.

## Destructive reset

```bash
./hub destroy --yes
```

This removes all managed volumes, including databases and model files. It is intentionally not part of normal troubleshooting.

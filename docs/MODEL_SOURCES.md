# Model Sources and Mounts

Agent UI treats model ownership and storage as operator concerns. Registration should not require moving a multi-gigabyte file merely to satisfy a project directory convention.

## Managed artifacts

```yaml
artifact:
  kind: managed
  filename: example.gguf
```

Managed artifacts live in the `MODELS_VOLUME` named Docker volume. They may be populated with:

```bash
./hub model import MODEL_ID /path/to/example.gguf
./hub model fetch MODEL_ID --url URL
```

This is convenient for self-contained deployments and backups, but it is optional.

## Existing host files or directories

```yaml
artifact:
  kind: host_path
  path: /srv/shared-models/example.gguf
  read_only: true
```

or:

```yaml
artifact:
  kind: host_path
  path: /home/user/project/models
  filename: example.gguf
  read_only: true
```

`./hub catalog apply` generates `.agent-ui/compose.generated.yaml`. The generated override bind-mounts the source read-only into the llama.cpp container at a stable location under:

```text
/models/external/MODEL_ID/
```

The source remains where it is. Paths may be in a shared model directory, under a project, on a mounted NAS filesystem, or anywhere Docker can bind-mount.

Convenience command:

```bash
./hub model link MODEL_ID /absolute/path/to/example.gguf \
  --capability chat
```

Relative paths are resolved before registration. The generated file is local state and is ignored by Git.

### Directory versus file mounts

A path ending in `.gguf` is mounted as a single file. Other paths are treated as directories, and `artifact.filename` identifies the model file inside that directory. Directory registration is useful for multi-shard models or adjacent tokenizer/projection artifacts.

## Existing Docker volumes

```yaml
artifact:
  kind: docker_volume
  volume: organization-model-cache
  sub_path: text/example.gguf
  read_only: true
```

The generated Compose override declares the volume as `external: true`; Agent UI will not create, delete, or back up that independently owned volume.

## Operator-provided container mounts

```yaml
artifact:
  kind: container_path
  path: /custom/models/example.gguf
```

Use this escape hatch when another Compose layer, a platform operator, or a storage plugin provides the mount. Agent UI records and uses the container path but does not generate a volume declaration.

## API-served models

```yaml
artifact:
  kind: none
```

This is the normal choice for an OpenAI-compatible backend, an image service, or another inference server. `upstream_model` specifies the backend's model name.

## Kubernetes existing PVC

```yaml
artifact:
  kind: pvc
  claim_name: shared-model-weights
  sub_path: text/example.gguf
  read_only: true
```

`./hub catalog k8s-values` generates a volume and mount beneath `llama.extraVolumes` and `llama.extraVolumeMounts`. The claim remains independently managed and is not deleted with the Helm release.

## Kubernetes hostPath

```yaml
artifact:
  kind: hostPath
  path: /srv/models
  sub_path: example.gguf
  read_only: true
```

HostPath is node-local. Production use normally also requires a `nodeSelector`, node affinity, or a dedicated GPU node so the pod is scheduled where the path exists. It should be an explicit operator decision rather than an automatic discovery result.

## Arbitrary Kubernetes storage

The chart exposes raw Kubernetes fields:

```yaml
llama:
  extraVolumes: []
  extraVolumeMounts: []
```

This supports CSI drivers, NFS, Ceph, object-store mounts, secrets, projected volumes, or organization-specific storage without requiring Agent UI to understand every provider. Set each model's `containerPath` to the resulting mount path.

## Kubernetes download/init pattern

For a cluster-managed copy, create or reuse a PVC and populate it through an init container, job, object-store sync, or `./hub k8s model-import`. Credentials should come from Kubernetes Secrets; URLs and tokens should not be committed to the catalog.

## Verification

Managed files can be hashed directly:

```bash
./hub model verify MODEL_ID --sha256 EXPECTED_HASH
```

External bind mounts and volumes are verified after container creation because the toolbox does not automatically receive arbitrary host mounts. Use:

```bash
./hub model render
./hub up
./hub doctor --gpu
./hub smoke
```

The gateway's setup endpoint reports declared source type and availability. A missing mount causes model loading to fail closed rather than silently selecting a different model.

## Backup ownership

Normal Agent UI backups include managed state and optionally managed model data. They do not silently copy:

- host paths;
- external Docker volumes;
- Kubernetes PVC contents;
- hostPath data;
- remote endpoint models.

The catalog references are backed up so an operator can restore the control plane and reconnect independently protected storage.

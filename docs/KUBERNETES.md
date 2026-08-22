# Kubernetes Deployment

The Helm chart uses the same model-neutral catalog contract as Docker Compose. `./hub catalog k8s-values` converts registered local model sources into model entries, volumes, and mounts.

## Prerequisites

- Kubernetes cluster
- Helm 3
- a working storage class or existing claims
- GPU device plugin/runtime when local GPU inference is enabled
- cluster access in `$HOME/.kube`

The `./hub` wrapper runs Helm and kubectl inside the toolbox container.

## Render before installation

```bash
./hub k8s render
```

This:

1. reads the checked-in base catalog;
2. merges the installation overlay;
3. validates references and storage requirements;
4. writes `.agent-ui/k8s.generated.yaml`;
5. renders the Helm chart.

Review generated volumes, Secrets, node placement, and Ingress before installation.

## Install and upgrade

```bash
./hub k8s install
./hub k8s upgrade
./hub k8s status
```

Defaults:

```text
release:   agent-ui
namespace: agent-ui
```

Override with:

```bash
AGENT_UI_RELEASE=my-release \
AGENT_UI_NAMESPACE=my-namespace \
./hub k8s install
```

## Empty installation

The chart supports an empty `models` map. PostgreSQL, gateway, and UIs may start while the gateway reports `setup_required`. This allows a cluster operator or setup agent to install the control plane before model storage is provisioned.

## Existing PVC

Catalog example:

```yaml
version: 2
models:
  cluster-model:
    backend: local-llama
    upstream_model: cluster-model
    capabilities: [chat, code]
    artifact:
      kind: pvc
      claim_name: shared-model-weights
      sub_path: text/example.gguf
      read_only: true
    runtime:
      ctx-size: 32768
```

Generated Helm values add:

```yaml
llama:
  extraVolumes:
    - name: model-cluster-model
      persistentVolumeClaim:
        claimName: shared-model-weights
  extraVolumeMounts:
    - name: model-cluster-model
      mountPath: /models/external/cluster-model
      readOnly: true
```

The claim is not owned by the Helm release and is not deleted during uninstall.

## hostPath

```yaml
artifact:
  kind: hostPath
  path: /srv/models
  sub_path: example.gguf
  read_only: true
```

HostPath is appropriate only when the operator understands node-local storage. Configure node selection or affinity so the inference pod lands on the node containing the path:

```yaml
llama:
  nodeSelector:
    accelerator-node: model-host
```

Do not let an automated discovery process enable hostPath without explicit operator approval.

## Arbitrary CSI or network storage

The chart exposes raw Kubernetes fields:

```yaml
llama:
  extraVolumes: []
  extraVolumeMounts: []
```

Example NFS/CSI/object-store volumes can be supplied through a normal values file. Point each model's `containerPath` at the corresponding mount. This is the escape hatch for organization-specific storage without forking the chart.

## Managed PVC

The chart creates a model PVC unless `llama.storage.existingClaim` is provided. Populate it using one of:

- `./hub k8s model-import`;
- a Kubernetes Job;
- an init container;
- object-store synchronization;
- a model registry controller;
- a storage snapshot or clone.

Example:

```bash
./hub k8s model-import MODEL_ID /path/to/example.gguf
```

This is intended for models declared with managed storage. Large production transfers should normally use a registry/object-store workflow rather than `kubectl cp`.

## Remote inference backends

Models with `artifact.kind=none` do not require the llama.cpp pod. Add their backend URL and API-key environment variable to the gateway through catalog/values and `gateway.extraEnv`.

For a remote-only cluster, set:

```yaml
llama:
  enabled: false
```

The gateway selects remote models by capability like any other deployment.

## Images and specialized services

An image service may run:

- in the same namespace;
- in another namespace;
- on a GPU node outside the cluster;
- behind an OpenAI-compatible internal endpoint.

Register the service as a backend, declare an image-capable model, and add network policy rules if network policies are enabled.

## GPU allocation

Default values request:

```yaml
llama:
  gpuResourceName: nvidia.com/gpu
  gpuCount: 1
```

Adjust the resource name for the cluster's device plugin. For CPU-only backends set `gpuCount: 0` and provide appropriate resources.

## Secrets

For tests the chart can create a Secret from values. Production deployments should use:

```yaml
secrets:
  create: false
  existingSecret: agent-ui-secrets
```

Expected keys:

```text
postgres-user
postgres-password
llama-api-key
gateway-api-key
webui-secret-key
hermes-api-key
```

Backend-specific credentials can be injected through `gateway.extraEnv` using `secretKeyRef`. Catalogs store only the environment-variable name.

## Optional components

```yaml
sillyTavern:
  enabled: true

hermes:
  enabled: true

serviceMonitor:
  enabled: true

ingress:
  enabled: true
```

The default chart leaves story and agent workspaces disabled so installations do not expose unused surfaces.

## Network policy

When enabled, verify that the gateway can reach every registered remote backend and that only intended clients can reach the gateway. The default policy cannot anticipate arbitrary endpoint addresses.

## Uninstall

```bash
./hub k8s uninstall
```

Confirm the retention policy for chart-owned PVCs. Existing claims, hostPath data, and remote model services remain outside the release's ownership.

## Validation

```bash
helm lint deploy/helm/local-ai-hub
./hub k8s render
./hub k8s install
./hub k8s status
```

After installation, test each declared capability and verify model switching, storage availability, memory isolation, and GPU allocation on the actual target cluster.

# Kubernetes deployment

## Positioning

Compose is the canonical single-host deployment. Kubernetes is maintained for the same components when orchestration, scheduling, storage classes, ingress, or cluster-level operations are valuable.

The Helm chart does not require PostgreSQL, Helm, or kubectl on the workstation. `./hub` invokes the toolbox image and mounts the current kubeconfig directory read-only.

## Prerequisites

- Kubernetes 1.28 or newer.
- A working default StorageClass, or `global.storageClass` configured.
- NVIDIA device plugin or GPU Operator exposing `nvidia.com/gpu`.
- A node able to satisfy the llama pod's CPU, RAM, storage, and GPU requests.
- A registry-accessible gateway image, unless every target node already has the local image.

## Publish the gateway image

Compose builds `local-ai-hub-gateway:0.2.0` locally. A remote cluster needs that image in a registry:

```bash
docker build \
  -f deploy/docker/gateway.Dockerfile \
  -t registry.example/local-ai-hub-gateway:0.2.0 .

docker push registry.example/local-ai-hub-gateway:0.2.0
```

Then pass:

```bash
--set-string gateway.image.repository=registry.example/local-ai-hub-gateway \
--set-string gateway.image.tag=0.2.0
```

## Render before applying

```bash
./hub k8s render \
  --set-string gateway.image.repository=registry.example/local-ai-hub-gateway \
  --set-string gateway.image.tag=0.2.0 \
  > /tmp/local-ai-hub.yaml
```

Review GPU resources, PVC sizes, image tags, secrets, services, and ingress.

## Install

```bash
export LOCAL_AI_HUB_NAMESPACE=local-ai-hub
export LOCAL_AI_HUB_RELEASE=local-ai-hub

./hub k8s install \
  --set-string gateway.image.repository=registry.example/local-ai-hub-gateway \
  --set-string gateway.image.tag=0.2.0
```

The installer:

1. Creates the namespace if necessary.
2. Creates or updates a Kubernetes Secret from `.env`.
3. Generates Helm model values from the same base catalog and local overlay used by Compose.
4. Runs `helm upgrade --install`.

Secrets are not stored in Helm values files.

## Storage

Default claims:

| Claim | Purpose | Default |
|---|---|---:|
| `<release>-models` | GGUF files | 50 GiB |
| PostgreSQL StatefulSet claim | DB data | 20 GiB |
| `<release>-openwebui` | Open WebUI local data | 10 GiB |
| `<release>-sillytavern` | campaign/config/plugin data | 10 GiB |
| `<release>-hermes` | agent sessions/memory/skills | 10 GiB |

Use existing claims where required:

```bash
--set-string llama.storage.existingClaim=my-model-pvc
--set-string openWebUI.storage.existingClaim=my-openwebui-pvc
```

## Model import

```bash
./hub k8s model-import gpt-oss-20b /path/to/gpt-oss-20b.gguf
./hub k8s model-import stheno-8b /path/to/Stheno.gguf
```

The importer resolves the canonical catalog filename, mounts the model PVC in a temporary pod, copies the GGUF, and restarts llama.cpp.

## GPU scheduling

Default request and limit:

```yaml
nvidia.com/gpu: 1
```

Override the resource name when using a partitioned or vendor-specific device plugin:

```bash
--set-string llama.gpu.resourceName=nvidia.com/mig-1g.10gb
```

A 10 GiB MIG slice is not sufficient for the default GPT-OSS configuration; the example only illustrates resource-name configuration.

Use node labels and tolerations:

```yaml
llama:
  nodeSelector:
    accelerator: tesla-t4
  tolerations:
    - key: dedicated
      operator: Equal
      value: ai
      effect: NoSchedule
```

## Access

Services are ClusterIP by default. PostgreSQL consumes no host or node port.

Port-forward Open WebUI:

```bash
kubectl -n local-ai-hub port-forward svc/local-ai-hub-openwebui 18080:8080
```

Port-forward SillyTavern:

```bash
kubectl -n local-ai-hub port-forward svc/local-ai-hub-sillytavern 18001:8000
```

For persistent access, enable ingress with separate hostnames. SillyTavern is not assumed to work correctly under an arbitrary URL subpath, so the chart uses host-based routing.

## Hermes

Hermes is disabled by default:

```bash
./hub k8s upgrade --set hermes.enabled=true
```

Its dashboard requires authentication and the agent loop has hard stops for repeated failures and no-progress tool calls. Do not grant broad Kubernetes RBAC or mount the container runtime socket by default.

## Upgrades

```bash
./hub k8s upgrade \
  --set-string gateway.image.tag=0.2.1 \
  --set-string openWebUI.image.tag=PINNED_VERSION
```

Pin third-party image tags in production. Back up PVCs and PostgreSQL before chart or application upgrades.

## Uninstall

```bash
./hub k8s uninstall
```

Helm uninstall does not necessarily delete StatefulSet/PVC data, depending on resource ownership and cluster retention behavior. Inspect claims explicitly before deleting them.

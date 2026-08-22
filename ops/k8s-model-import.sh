#!/usr/bin/env bash
set -Eeuo pipefail

release="${1:?release is required}"
namespace="${2:?namespace is required}"
model_id="${3:?model id is required}"
source_file="${4:?source file is required}"
destination_name="${5:?destination filename is required}"
shift 5

[[ -f "$source_file" ]] || { echo "Model file not found: $source_file" >&2; exit 2; }
[[ "$source_file" == *.gguf ]] || { echo "Only GGUF files can be imported" >&2; exit 2; }

pvc="${MODEL_PVC:-${release}-models}"
pod="${release}-model-loader-$(date +%s)"
filename="$destination_name"
[[ "$filename" != */* && "$filename" == *.gguf ]] || { echo "Unsafe destination filename: $filename" >&2; exit 2; }

cleanup() {
  kubectl -n "$namespace" delete pod "$pod" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

cat <<EOF | kubectl -n "$namespace" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${pod}
  labels:
    app.kubernetes.io/name: local-ai-hub
    app.kubernetes.io/component: model-loader
spec:
  restartPolicy: Never
  containers:
    - name: loader
      image: busybox:1.37
      command: ["sh", "-c", "sleep 3600"]
      volumeMounts:
        - name: models
          mountPath: /models
  volumes:
    - name: models
      persistentVolumeClaim:
        claimName: ${pvc}
EOF

kubectl -n "$namespace" wait --for=condition=Ready "pod/$pod" --timeout=180s
kubectl -n "$namespace" cp "$source_file" "$pod:/models/$filename"
kubectl -n "$namespace" exec "$pod" -- sh -c "chmod 0644 '/models/$filename' && sync"

kubectl -n "$namespace" rollout restart deployment "${release}-llama"
printf 'Imported %s for model id %s into PVC %s.\n' "$filename" "$model_id" "$pvc"

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in podman kind kubectl helm; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
export KIND_EXPERIMENTAL_PROVIDER=podman
KWOK_VERSION="${KWOK_VERSION:-v0.7.0}"
KUEUE_VERSION="${KUEUE_VERSION:-v0.13.4}"
if ! kind get clusters | grep -qx gpu-fleet; then
  kind create cluster --name gpu-fleet --config infra/kind.yaml --wait 120s
else
  # kind discovers stopped containers too; bring them back after a VM reboot.
  while IFS= read -r node_id; do
    if [[ -n "$node_id" ]]; then podman start "$node_id" >/dev/null; fi
  done < <(podman ps -a --filter label=io.x-k8s.kind.cluster=gpu-fleet --format '{{.ID}}')
fi
# Every command explicitly targets this lab, never your current context.
K=(kubectl --context kind-gpu-fleet)
api_ready=false
for attempt in {1..60}; do
  if "${K[@]}" --request-timeout=3s get --raw=/readyz >/dev/null 2>&1; then
    api_ready=true
    break
  fi
  sleep 2
done
if [[ "$api_ready" != true ]]; then
  echo 'The kind GPU Fleet API did not become ready.' >&2
  exit 1
fi
if ! "${K[@]}" -n kube-system get deployment kwok-controller >/dev/null 2>&1; then
  "${K[@]}" apply -f "https://github.com/kubernetes-sigs/kwok/releases/download/${KWOK_VERSION}/kwok.yaml"
fi
"${K[@]}" apply -f infra/kwok-stages.yaml
"${K[@]}" delete stage pod-complete --ignore-not-found
if ! "${K[@]}" -n kueue-system get deployment kueue-controller-manager >/dev/null 2>&1; then
  "${K[@]}" apply --server-side -f "https://github.com/kubernetes-sigs/kueue/releases/download/${KUEUE_VERSION}/manifests.yaml"
fi
"${K[@]}" -n kube-system rollout status deployment/kwok-controller --timeout=180s
"${K[@]}" -n kueue-system rollout status deployment/kueue-controller-manager --timeout=180s
helm upgrade --install gpu-fleet infra/chart --kube-context kind-gpu-fleet --wait --timeout 180s
"${K[@]}" wait --for=condition=Ready node -l type=kwok --timeout=120s
printf 'Lab ready. Inspect: kubectl --context kind-gpu-fleet get nodes,clusterqueues\n'

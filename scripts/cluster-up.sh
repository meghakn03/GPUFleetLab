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
fi
# Every command explicitly targets this lab, never your current context.
K=(kubectl --context kind-gpu-fleet)
"${K[@]}" apply -f "https://github.com/kubernetes-sigs/kwok/releases/download/${KWOK_VERSION}/kwok.yaml"
"${K[@]}" apply -f infra/kwok-stages.yaml
"${K[@]}" delete stage pod-complete --ignore-not-found
"${K[@]}" apply --server-side -f "https://github.com/kubernetes-sigs/kueue/releases/download/${KUEUE_VERSION}/manifests.yaml"
"${K[@]}" -n kube-system rollout status deployment/kwok-controller --timeout=180s
"${K[@]}" -n kueue-system rollout status deployment/kueue-controller-manager --timeout=180s
helm upgrade --install gpu-fleet infra/chart --kube-context kind-gpu-fleet --wait --timeout 180s
"${K[@]}" wait --for=condition=Ready node -l type=kwok --timeout=120s
printf 'Lab ready. Inspect: kubectl --context kind-gpu-fleet get nodes,clusterqueues\n'

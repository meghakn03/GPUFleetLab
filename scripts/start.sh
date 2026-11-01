#!/usr/bin/env bash
# Start the complete local lab; reuse existing services and preserve cluster jobs.
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in python3 node npm podman kind kubectl helm curl; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
export KIND_EXPERIMENTAL_PROVIDER=podman
mkdir -p .run

if ! .venv/bin/python -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
if [[ ! -d frontend/node_modules ]] || [[ frontend/package-lock.json -nt frontend/node_modules/.package-lock.json ]]; then
  npm --prefix frontend ci
fi
npm --prefix frontend run build

if ! podman info >/dev/null 2>&1; then
  if [[ "$(uname -s)" == Darwin ]]; then
    machine_name="${PODMAN_MACHINE:-podman-machine-default}"
    if ! podman machine inspect "$machine_name" >/dev/null 2>&1; then
      if [[ -n "${PODMAN_MACHINE_IMAGE:-}" ]]; then
        podman machine init --cpus 4 --memory 4096 --disk-size 30 --rootful --image "$PODMAN_MACHINE_IMAGE" "$machine_name"
      else
        podman machine init --cpus 4 --memory 4096 --disk-size 30 --rootful "$machine_name"
      fi
    fi
    # A stopped VM can retain proxy sockets after a host or terminal shutdown.
    if [[ "$(podman machine inspect "$machine_name" --format '{{.State}}')" == stopped ]]; then
      podman machine stop "$machine_name" >/dev/null
    fi
    if ! podman machine start "$machine_name"; then
      echo 'Retrying Podman after clearing its failed proxy startup...'
      podman machine stop "$machine_name"
      podman machine start "$machine_name"
    fi
  else
    echo 'Podman engine is unavailable. Start its service, then retry make start.' >&2
    exit 1
  fi
fi
bash scripts/cluster-up.sh
podman compose -f infra/compose.yaml up -d

api_pid=''
cleanup() {
  if [[ -n "$api_pid" ]]; then
    kill -TERM "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if curl -fsS --max-time 5 http://127.0.0.1:8017/healthz > .run/health.json 2>/dev/null; then
  if ! python3 -c 'import json; assert json.load(open(".run/health.json")).get("mode") == "kubernetes"'; then
    echo 'Port 8017 is serving another mode. Stop that API before running make start.' >&2
    exit 1
  fi
  echo 'Reusing the running Kubernetes-mode API.'
else
  FLEET_MODE=kubernetes .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8017 > .run/api.log 2>&1 &
  api_pid=$!
fi
wait_for() {
  local name="$1" url="$2" attempt
  for attempt in {1..60}; do
    if [[ -n "$api_pid" ]] && ! kill -0 "$api_pid" 2>/dev/null; then
      cat .run/api.log >&2
      echo 'API exited before startup completed.' >&2
      exit 1
    fi
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then return; fi
    sleep 2
  done
  echo "$name did not become ready: $url" >&2
  exit 1
}
wait_for 'Kubernetes-backed API' http://127.0.0.1:8017/metrics
wait_for Prometheus http://127.0.0.1:9090/-/ready
wait_for Grafana http://127.0.0.1:3000/api/health
cat <<'URLS'

GPU Fleet is ready:
  Dashboard   http://127.0.0.1:8017/
  Grafana     http://127.0.0.1:3000/d/gpu-fleet
  Prometheus  http://127.0.0.1:9090/
URLS
if [[ -n "$api_pid" ]]; then
  echo 'API logs: .run/api.log. Ctrl-C stops this API; containers and cluster remain available.'
  wait "$api_pid"
fi

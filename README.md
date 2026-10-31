# GPU Fleet Manager

A local GPU cluster operations lab with a React dashboard, FastAPI control plane, deterministic scheduler, Kueue/KWOK integration, and Prometheus/Grafana configuration. Four simulated A100 nodes expose 16 GPUs shared by two teams.

**GPU allocation is measurable. GPU utilization, training throughput, memory consumption, and hardware performance require real GPUs.** All hardware/model/memory labels here describe simulated inventory.

## Run locally

Prerequisites: Python 3.9+ and Node.js 22.12+ with npm.

```sh
make setup
make run
```

Open http://127.0.0.1:8017. API documentation is at `/docs`, health at `/healthz`, and Prometheus exposition at `/metrics`. One FastAPI process serves the compiled React app and the API. `make dev` runs Vite with an API proxy for frontend work; keep `make run` running separately. Port 8017 is used to avoid common development services on 8000.

The simulator needs **no containers, Kubernetes, cloud account, or GPU**. State is in memory and resets on server restart. Run one API worker. The API is a local trusted lab, without authentication; keep it bound to loopback.

## The five-minute demo

Use the interactive runbook on Overview:

1. **Fill & borrow:** Team A runs four 4-GPU jobs. Its nominal quota is 8 and it borrows Team B's unused 8.
2. **Advance 30s:** simulation time advances; jobs accumulate simulated runtime.
3. **Reclaim quota:** Team B submits an 8-GPU priority-100 job. Two eligible priority-20 Team A jobs are requeued atomically, returning borrowed capacity. Inspect each job's explanation and the event log.
4. **Fail a node:** capacity falls to 12. Every job touching that node releases its entire allocation and is requeued. Whole-job placement is retried across healthy nodes.
5. **Recover node:** capacity returns to 16 and queued jobs are reconsidered. Advance time three times to finish the urgent job and watch Team A borrow idle capacity again.

Submit your own jobs, filter/search workloads, cancel jobs, protect workloads against preemption, or request an H100 to inspect the unsupported-flavor explanation. Reset clears all simulator state after a confirmation. Runbook steps can be repeated and deliberately add workloads; reset first for a clean demo.

## Scheduling contract

- Team A and Team B each have 8 nominal GPUs; each may borrow the other team's unused quota up to fleet capacity.
- Waiting jobs are considered by descending priority, then FIFO. Lower-priority jobs that fit can backfill around larger waiting requests.
- Jobs are admitted as a whole and may span nodes. Placement uses deterministic first-fit.
- A job that fits within its team's nominal share may preempt lower-priority preemptible workloads. Cross-team victims must be above nominal quota, and reclaim cannot push that team below nominal. Whole-job granularity can prevent reclaim even when some borrowed GPUs exist.
- Victim selection is planned before mutation; insufficient reclaim never partially evicts jobs.
- Failed nodes release affected whole jobs. Simulated checkpoints preserve elapsed runtime. Admission wait measures first admission only; it excludes later requeue time.
- Explicit clock advances make the demo repeatable. A tick charges only jobs running at its start; jobs admitted at the end begin accumulating runtime on the next tick.
- The simulator is an educational implementation, not an emulation of all Kueue scheduling policies.

## Real Kubernetes control plane (optional)

Prerequisites: Podman with a running Linux VM (`podman machine init --cpus 4 --memory 4096 --disk-size 30 --rootful` then `podman machine start` if needed), kind, kubectl, Helm, and Internet access. Rootless Podman/kind may require host-specific setup; see [kind rootless guidance](https://kind.sigs.k8s.io/docs/user/rootless/).

```sh
make lab-up
# Or, after dependencies have already been installed:
# make cluster-up monitoring run-kubernetes
```

`cluster-up` pins Kubernetes v1.32.5 and explicitly targets `kind-gpu-fleet` and installs pinned KWOK v0.7.0 and Kueue v0.13.4 manifests, followed by the Helm chart containing four fake GPU nodes, a resource flavor, two namespaces, two LocalQueues, and two ClusterQueues in a cohort. Override `KWOK_VERSION`/`KUEUE_VERSION` deliberately when upgrading. It does not change or delete other clusters. Use `make cluster-down` to delete this disposable lab.

In Kubernetes mode the API uses `kubectl --context kind-gpu-fleet` via argument arrays (no shell). It creates suspended batch Jobs carrying the Kueue queue and priority labels. Inventory, allocation, admission explanations, queue waits, and events come from Kubernetes resources. Each requested GPU becomes one worker pod; KWOK simulates pod lifecycle. User names are stored in annotations. No submitted code or actual training runs.

Kubernetes-specific behavior:

- Priorities 80–100 map to `urgent` (100), others to `batch` (20). Quotas and reclaim are enforced by Kueue.
- A duration-aware KWOK completion stage uses the submitted duration in wall-clock seconds. Each replacement pod starts a fresh duration; real checkpoints are not implemented. Per-job preemptible protection is simulator-only; Kueue's configured lower-priority reclaim policy applies in Kubernetes mode.
- Node failure is an explicit **cordon + affected fake-pod deletion** fault injection. This simulates loss of schedulable capacity, rather than a physical heartbeat timeout. Recovery uncordons the node. Kubernetes reconciles replacement pods asynchronously.
- Simulator runbook, reset, and manual clock are disabled. Submit jobs manually and inspect controller events. A Kueue-admitted job may still wait for physical capacity because quota admission and Kubernetes placement are distinct.
- The browser keeps the latest 100 observed allocation samples in API-process memory. Prometheus provides longer time-series in Kubernetes mode. This adapter is designed for this chart's 8-GPU-per-team configuration; changing chart quotas also requires updating the adapter's display constants.

**Live verification:** the local Podman/kind/KWOK/Kueue stack is now deployed. Repeat the isolated borrow/reclaim/fault/recovery/completion scenario with `make verify-live` on an idle lab. It creates and cleans up verification jobs and writes `docs/live-verification.json`. The separate API-outage check writes `docs/alert-verification.json`. These checks exercise simulated Kubernetes nodes, not physical GPU performance.

## Monitoring

With the app running, use `make monitoring`. Prometheus listens on http://127.0.0.1:9090 and Grafana on http://127.0.0.1:3000. Grafana provisions a read-only anonymous dashboard named “GPU Fleet — SIMULATED allocations”. Container images are version pinned; volumes are provisioned read-only. Stop using `podman compose -f infra/compose.yaml down`.

Prometheus scrapes `host.containers.internal:8017`. On hosts where container-to-host loopback is unavailable, run the app on the Podman-reachable host interface and adjust the scrape target; avoid exposing this unauthenticated lab to an untrusted network. The supplied Containerfile can alternatively package the app for a shared container network.

Metrics cover healthy GPU capacity, team allocations/borrowing/quota, job counts, first-admission wait sum/count, preemption and recovery counts. In simulator mode, event times and waits use simulation seconds; Prometheus uses wall-clock scrape times. Alerts cover reduced capacity, queued jobs, and unavailable API. Alerts are visible in Prometheus; external notification delivery is not configured. Simulator reset resets metric counters. Kubernetes preemption counts come from Kueue events; recovery counts come from annotations on jobs interrupted through the API. Process-level totals retain counts across job deletion but reset on API restart; historical events are bounded by Kubernetes event retention. First-admission wait is reconstructed from the earliest available admission event, falling back to the current admission condition if events have expired.

## Verification

```sh
make test
make build
```

Tests cover the full borrow/reclaim/failure/recovery path, non-preemptible workloads, transactional reclaim, incompatible flavors, FIFO waits, API validation, metrics, and seeded randomized capacity invariants. CI builds the dashboard and runs these tests.

## Layout

- `backend/engine.py`: deterministic allocation and scheduling rules
- `backend/app.py`: serialized API mutations, validation, metrics, static serving
- `backend/kubernetes.py`: optional real Kubernetes adapter
- `frontend/src/`: React dashboard, job form, inventory, quotas, activity, runbook
- `infra/chart/`: Helm inventory, Kueue resources, duration-aware KWOK completion
- `scripts/verify-live.py`: isolated live integration scenario
- `scripts/verify-api-alert.py`: controlled API pause/resume alert check
- `infra/`: kind, sample Job, monitoring configuration
- `docs/architecture.md`: design and troubleshooting

References: [Kueue ClusterQueues](https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/), [KWOK in-cluster installation](https://kwok.sigs.k8s.io/docs/user/kwok-in-cluster/), [KWOK Kueue example](https://kwok.sigs.k8s.io/docs/examples/kueue/).

## Verified local deployment

See [the verification summary](docs/verification.md) and its machine-readable reports.

The checked-in verification reports record a successful real-control-plane run on 31 October 2026 with Podman 6.1.4, kind 0.33.0, Kubernetes 1.32.5, KWOK 0.7.0, and Kueue 0.13.4. Prometheus 3.2.1 and Grafana 11.6.0 run in Podman with loopback ports. GPU workers and their telemetry remain simulated. The API outage check pauses the PID you explicitly pass and always resumes it in a `finally` block; only run it against your local lab API.

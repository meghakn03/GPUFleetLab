# Live verification — 31 October 2026

The complete local stack was exercised on macOS arm64 using Podman 6.1.4, kind 0.33.0, Kubernetes 1.32.5, KWOK 0.7.0, Kueue 0.13.4, Prometheus 3.2.1, and Grafana 11.6.0.

- All four fake workers reported Ready and advertised 16 total GPUs.
- Team A borrowed 8 GPUs; an urgent Team B workload reclaimed its nominal share by preempting two jobs. Kueue emitted preemption events.
- Fault injection cordoned one fake worker and removed its pods. Healthy capacity fell to 12; recovery restored 16 and replacement pod allocations.
- Cancellation released capacity. Duration-aware KWOK completion and unsupported H100 queueing were observed.
- The queued-job and reduced-capacity alerts fired; reduced-capacity cleared after recovery.
- The API process was briefly paused. The 30-second API-unavailable alert fired, then cleared after the process resumed.
- Prometheus scraped healthy metrics, including 8 borrowed GPUs. Grafana queried the Prometheus datasource and returned 16 allocated GPUs for Team A.
- Test workloads were removed. Final state: four healthy workers, empty queues, no active alerts.
- Ten Python tests pass; the React production build and Helm rendering pass.

Machine-readable evidence: [live scenario](live-verification.json), [outage alert](alert-verification.json), [borrowed allocation metrics](metrics-verification.json).

Open the running services: [dashboard](http://127.0.0.1:8017/), [Grafana](http://127.0.0.1:3000/d/gpu-fleet), [Prometheus alerts](http://127.0.0.1:9090/alerts).

Reproduce with `make verify-live` on an idle lab. `make lab-up` installs dependencies, builds the dashboard, deploys the cluster, launches monitoring, then serves Kubernetes mode; Podman and its Linux VM must already be ready. The API-outage test is `scripts/verify-api-alert.py --pid <local-api-pid>` using the virtualenv's Python.

Stop the API with Ctrl-C, monitoring with `make monitoring-down`, and the VM with `podman machine stop`. `make cluster-down` deletes only the disposable gpu-fleet kind cluster. The GPU workers remain simulated: actual hardware utilization and training performance are not measured.

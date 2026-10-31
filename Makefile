.PHONY: setup build run run-kubernetes dev test cluster-up cluster-down monitoring monitoring-down verify-live lab-up
setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt
	npm --prefix frontend ci
	$(MAKE) build
build:
	npm --prefix frontend run build
run:
	.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8017
run-kubernetes:
	FLEET_MODE=kubernetes $(MAKE) run
verify-live:
	.venv/bin/python scripts/verify-live.py
monitoring-down:
	podman compose -f infra/compose.yaml down
dev:
	npm --prefix frontend run dev
test:
	.venv/bin/python -m pytest backend/tests -q
cluster-up:
	bash scripts/cluster-up.sh
cluster-down:
	KIND_EXPERIMENTAL_PROVIDER=podman kind delete cluster --name gpu-fleet
monitoring:
	podman compose -f infra/compose.yaml up -d

# One command after prerequisites and the Podman VM are ready.
lab-up:
	$(MAKE) setup
	$(MAKE) cluster-up
	$(MAKE) monitoring
	$(MAKE) run-kubernetes

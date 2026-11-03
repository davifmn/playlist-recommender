.PHONY: install test lint images kind-up kind-deploy smoke clean

CHART := deploy/helm/playlist-recommender

install:            ## dev environment
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .
	helm lint $(CHART) -f deploy/environments/values-dev.yaml

images:
	docker build -f services/api/Dockerfile -t local/playlist-recommender-api:ci .
	docker build -f services/trainer/Dockerfile -t local/playlist-recommender-trainer:ci .

kind-up:
	kind create cluster --config deploy/kind/cluster.yaml

kind-deploy: images  ## same flow as the CI end-to-end job
	kind load docker-image local/playlist-recommender-api:ci local/playlist-recommender-trainer:ci --name recommender
	helm upgrade --install rec $(CHART) -n recommender --create-namespace \
	  -f deploy/environments/values-ci.yaml --wait --wait-for-jobs --timeout 10m

smoke:
	kubectl -n recommender port-forward svc/rec-playlist-recommender-api 8080:80 & \
	  sleep 3; python3 scripts/smoke_test.py http://localhost:8080; kill %1

clean:
	kind delete cluster --name recommender

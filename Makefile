PYTHON := .venv/bin/python

.PHONY: test test-ui test-e2e test-phase-2 lint demo docker-build verify-benchmarks verify-phase-1

test:
	$(PYTHON) -m pytest

test-ui:
	$(PYTHON) -m pytest tests/test_ui.py -q

test-e2e:
	$(PYTHON) -m pytest tests/test_e2e_smoke.py -q

test-phase-2:
	$(PYTHON) -m pytest tests/phase2 -q

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

demo:
	$(PYTHON) -m streamlit run src/issueflow/ui.py

docker-build:
	docker build --tag issueflow-micrograd:dev --file docker/Dockerfile.micrograd .

verify-benchmarks:
	$(PYTHON) scripts/verify_benchmarks.py --catalog benchmarks/catalogs/compatibility.yaml

verify-phase-1:
	$(MAKE) lint
	$(MAKE) docker-build
	$(MAKE) test
	$(MAKE) verify-benchmarks

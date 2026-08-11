PYTHON := .venv/bin/python

.PHONY: test lint demo docker-build verify-benchmarks

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

demo:
	$(PYTHON) -m streamlit run src/issueflow/ui.py

docker-build:
	docker build --tag issueflow-micrograd:dev --file docker/Dockerfile.micrograd .

verify-benchmarks:
	$(PYTHON) scripts/verify_benchmarks.py --catalog benchmarks/micrograd.yaml

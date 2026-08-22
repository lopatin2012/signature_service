.PHONY: help lint test format typecheck selftest dev docker-build docker-up docker-down docker-test

help:
	@echo "Available commands:"
	@echo "  make install    Install dependencies"
	@echo "  make selftest   Run API unit tests (no crypto provider)"
	@echo "  make test       Run all pytest tests (excluding cert by default)"
	@echo "  make test-cert  Run integration tests with -m cert marker"
	@echo "  make lint       Lint the codebase"
	@echo "  make format     Format code (ruff + black)"
	@echo "  make typecheck  Type check with mypy"
	@echo "  make docker-up  Start Docker container with CryptoPro environment"
	@echo "  make docker-down Stop Docker containers"
	@echo "  make clean      Remove build artifacts"

install:
	pip install -r requirements.txt

format:
	ruff check --fix . || true
	ruff format .

lint:
	ruff check .
	mypy .
	pylint signers/ main.py

typecheck:
	mypy .

selftest:
	python main.py --selftest

test:
	pytest -q tests/ -m 'not cert'

test-cert:
	pytest tests/test_certificate.py -m cert -v

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	find . -type f -name '*.pyo' -delete 2>/dev/null || true
	find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.mypy_cache' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.ruff_cache' -exec rm -rf {} + 2>/dev/null || true

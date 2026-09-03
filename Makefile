.PHONY: install run test lint format check seed clean

install:
	python -m pip install -e ".[dev]"

run:
	uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

check: lint test

seed:
	python scripts/seed_demo.py

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage

.PHONY: install run worker test lint typecheck format format-check eval check seed clean

install:
	python -m pip install -e ".[dev]"

run:
	uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

worker:
	python -m app.worker --watch

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy app evals tests

format:
	ruff format .

format-check:
	ruff format --check .

eval:
	python -m evals.run

check: lint format-check typecheck test eval

seed:
	python scripts/seed_demo.py

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage

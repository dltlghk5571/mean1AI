#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d .venv ]]; then
  python -m venv .venv
fi
source .venv/bin/activate
python -m pip install -e '.[dev]'
[[ -f .env ]] || cp .env.example .env
exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

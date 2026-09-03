#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"

curl -sS -X POST "$BASE_URL/api/v1/complaints" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "가로등이 꺼져 있습니다",
    "content": "정자동 공원 입구 가로등 두 개가 꺼졌습니다. 제 번호는 010-1234-5678입니다.",
    "location_text": "정자동 공원 입구",
    "channel": "web"
  }' | python -m json.tool

#!/usr/bin/env bash
# Phase 3 model race: evaluate every extractor whose backend is available, then build the leaderboard.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
[ -f .env ] && set -a && . ./.env && set +a
python scripts/evaluate.py --extractor rules
curl -sf "${OLLAMA_BASE_URL:-http://localhost:11434}/api/version" >/dev/null \
  && python scripts/evaluate.py --extractor ollama:qwen2.5:1.5b --workers 2
[ -n "${GEMINI_API_KEY:-}" ] && python scripts/evaluate.py --extractor gemini:gemini-2.5-flash --workers 2
[ -n "${GROQ_API_KEY:-}" ] && python scripts/evaluate.py --extractor groq:llama-3.3-70b-versatile --workers 1
python scripts/leaderboard.py

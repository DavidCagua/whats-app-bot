#!/usr/bin/env bash
# =============================================================================
# scripts/test.sh — run pytest tiers locally
#
# Usage:
#   ./scripts/test.sh                  # unit (default, no LLM, no $)
#   ./scripts/test.sh unit             # same as above
#   ./scripts/test.sh integration      # router classification, hits OpenAI (~$0.01)
#   ./scripts/test.sh evals            # router + v2 multi-intent evals (~$0.05)
#   ./scripts/test.sh all              # everything (~$0.10, slow)
#
#   ./scripts/test.sh -- -k cerveza    # forward extra args to pytest
#   ./scripts/test.sh integration -v   # verbose pytest output
#
# Environment:
#   DATABASE_URL — defaults to local supabase if not set
#   OPENAI_API_KEY — required for integration/evals (loaded from .env)
#
# Conventions:
#   - Unit tests run on every push via GitHub Actions; this script is for the
#     heavier tiers (integration/evals) which are NOT in CI to avoid OpenAI
#     costs and flakiness on every push.
#   - Run integration before pushing risky changes (prompt edits, router rules,
#     search ranking).
#   - Run all before onboarding a new restaurant or shipping a major refactor.
# =============================================================================

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Default DATABASE_URL points at local supabase. Override by exporting your
# own (e.g. a staging DB) before running. Never point at prod for tests.
export DATABASE_URL="${DATABASE_URL:-postgresql://postgres:postgres@localhost:54322/postgres}"

# Find the venv python. Prefer .venv/bin/python so we don't accidentally
# run against system Python.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
  echo "⚠️  .venv/bin/python not found, falling back to: $PY"
fi

TIER="${1:-unit}"
shift || true

# Drop a leading "--" so users can pass extra pytest args after it.
if [ "${1:-}" = "--" ]; then
  shift
fi

case "$TIER" in
  unit)
    "$PY" -m pytest tests/unit -q "$@"
    ;;
  integration)
    "$PY" -m pytest tests/integration -m "integration or not integration" -q "$@"
    ;;
  evals)
    "$PY" -m pytest tests/evals -m eval -q "$@"
    ;;
  all)
    "$PY" -m pytest tests -m "" -q "$@"
    ;;
  -h|--help|help)
    sed -n '3,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "Unknown tier: $TIER"
    echo "Usage: $0 [unit|integration|evals|all] [-- pytest args]"
    exit 1
    ;;
esac

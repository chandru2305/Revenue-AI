#!/usr/bin/env bash
# CI-quality validation: run every lint/typecheck/test suite in the repo.
# Usage: ./scripts/validate.sh   (run from repo root)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail=0
step() {
  echo ""
  echo "==> $1"
}

# --- Backend ---
if [ -d backend/.venv ]; then
  PY="backend/.venv/Scripts/python.exe"
  [ -f "$PY" ] || PY="backend/.venv/bin/python"

  step "backend: ruff"
  "$PY" -m ruff check backend/app backend/tests backend/alembic backend/scripts || fail=1

  step "backend: mypy"
  (cd backend && "../$PY" -m mypy app scripts) || fail=1

  step "backend: pytest"
  (cd backend && "../$PY" -m pytest -q) || fail=1
else
  echo "SKIP backend checks — backend/.venv not found. See README Local Setup."
fi

# --- Evaluation ---
if [ -d .venv-eval ]; then
  PYE=".venv-eval/Scripts/python.exe"
  [ -f "$PYE" ] || PYE=".venv-eval/bin/python"

  step "evaluation: ruff"
  "$PYE" -m ruff check evaluation --config evaluation/pyproject.toml || fail=1

  step "evaluation: pytest"
  "$PYE" -m pytest evaluation/tests -q || fail=1
else
  echo "SKIP evaluation checks — .venv-eval not found. See README Local Setup."
fi

# --- Frontend ---
if [ -d frontend/node_modules ]; then
  step "frontend: typecheck"
  (cd frontend && npx tsc -b) || fail=1

  step "frontend: lint"
  (cd frontend && npx oxlint) || fail=1

  step "frontend: build"
  (cd frontend && npm run build) || fail=1
else
  echo "SKIP frontend checks — frontend/node_modules not found. Run 'npm install' in frontend/."
fi

echo ""
if [ "$fail" -eq 0 ]; then
  echo "All checks passed."
else
  echo "One or more checks FAILED — see output above."
  exit 1
fi

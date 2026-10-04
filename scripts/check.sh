#!/usr/bin/env bash
# Every check CI runs, runnable locally the same way (Git Bash on Windows).
#
#   scripts/check.sh lint       ruff lint + format check
#   scripts/check.sh types      pyright (basic)
#   scripts/check.sh test       pytest on SQLite
#   scripts/check.sh test-pg    pytest on Postgres (PI_TEST_DATABASE_URL, default: the compose service on :5440)
#   scripts/check.sh frontend   oxlint + type-checked Vite build
#   scripts/check.sh docs       Docusaurus build (fails on broken links)
#   scripts/check.sh all        everything above except test-pg, which runs when PI_TEST_DATABASE_URL is set
#
# Python comes from $PYTHON, else .venv (created by `uv sync --extra dev`).
set -euo pipefail

cd "$(dirname "$0")/.."

python_bin() {
  if [[ -n "${PYTHON:-}" ]]; then echo "$PYTHON"
  elif [[ -x .venv/bin/python ]]; then echo .venv/bin/python
  elif [[ -x .venv/Scripts/python.exe ]]; then echo .venv/Scripts/python.exe
  else
    echo "No Python environment: run 'uv sync --extra dev' or set PYTHON." >&2
    exit 1
  fi
}

npm_install_if_missing() {
  if [[ ! -d "$1/node_modules" ]]; then (cd "$1" && npm ci); fi
}

run_lint() {
  local py; py=$(python_bin)
  "$py" -m ruff check src tests scripts
  "$py" -m ruff format --check src tests scripts
}

run_types() {
  local py; py=$(python_bin)
  "$py" -m pyright --pythonpath "$py"
}

run_test() {
  "$(python_bin)" -m pytest -q
}

run_test_pg() {
  PI_TEST_DATABASE_URL="${PI_TEST_DATABASE_URL:-postgresql://pijira:pijira@localhost:5440/pijira}" \
    "$(python_bin)" -m pytest -q
}

run_frontend() {
  npm_install_if_missing frontend
  (cd frontend && npm run lint && npm run build)
}

run_docs() {
  npm_install_if_missing docs
  (cd docs && npm run build)
}

case "${1:-all}" in
  lint) run_lint ;;
  types) run_types ;;
  test) run_test ;;
  test-pg) run_test_pg ;;
  frontend) run_frontend ;;
  docs) run_docs ;;
  all)
    run_lint
    run_types
    run_test
    if [[ -n "${PI_TEST_DATABASE_URL:-}" ]]; then run_test_pg; fi
    run_frontend
    run_docs
    ;;
  *)
    echo "Unknown check '$1'. Use: lint | types | test | test-pg | frontend | docs | all" >&2
    exit 2
    ;;
esac

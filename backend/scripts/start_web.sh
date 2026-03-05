#!/usr/bin/env sh
set -eu

# Support both root checkout and backend rootDir execution.
if [ -d backend ]; then
  cd backend
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "No python interpreter found in PATH" >&2
  exit 1
fi

exec "$PYTHON_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"

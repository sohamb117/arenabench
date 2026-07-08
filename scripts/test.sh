#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Pre-flight: uv is required (dependency management + task runner).
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: 'uv' not found on PATH." >&2
  echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh   (docs: https://docs.astral.sh/uv/)" >&2
  exit 1
fi
uv run pytest -m "not e2e" -q "$@"

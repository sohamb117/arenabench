#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Pre-flight: uv is required (dependency management + task runner).
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: 'uv' not found on PATH." >&2
  echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh   (docs: https://docs.astral.sh/uv/)" >&2
  exit 1
fi
if [[ -z "${ARENABENCH_E2E:-}" ]]; then
  echo "SKIP: set ARENABENCH_E2E=1 to run end-to-end suite"
  exit 0
fi
uv run pytest -m e2e -q "$@"

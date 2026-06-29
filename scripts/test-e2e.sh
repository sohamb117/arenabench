#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${ARENABENCH_E2E:-}" ]]; then
  echo "SKIP: set ARENABENCH_E2E=1 to run end-to-end suite"
  exit 0
fi
uv run pytest -m e2e -q "$@"

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --all-groups
uv pip install -e .
echo "OK: dev environment ready"

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo ">>> ruff check"
uv run ruff check .
echo ">>> ruff format --check"
uv run ruff format --check .
echo ">>> basedpyright"
uv run basedpyright
echo "OK: lint clean"

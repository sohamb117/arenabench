#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Pre-flight: Docker + Compose v2 are required.
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: 'docker' not found on PATH. Install Docker Desktop (macOS) or colima." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: 'docker compose' (Compose v2) not available. Update Docker or install the compose plugin." >&2
  exit 1
fi
docker compose build "$@"
echo "OK: image built — arenabench:dev"

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

EXTRA_FILES=()
if VSOCK_OK=$(./scripts/probe-vsock.sh 2>/dev/null | grep -o '"vsock_available":true' || true); then
  if [[ -n "$VSOCK_OK" ]]; then
    EXTRA_FILES+=(-f docker-compose.yml -f docker-compose.vsock.yml)
    echo "INFO: vsock detected on host, layering docker-compose.vsock.yml"
  fi
fi

docker compose "${EXTRA_FILES[@]}" run --rm orchestrator "$@"

#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."

UNAME=$(uname -s)
VSOCK="false"
KVM="false"
HVF="false"
NOTES=""

case "$UNAME" in
  Linux)
    [[ -e /dev/vhost-vsock ]] && VSOCK="true"
    [[ -e /dev/kvm ]] && KVM="true"
    ;;
  Darwin)
    if /usr/sbin/sysctl -n kern.hv_support 2>/dev/null | grep -q '^1$'; then
      HVF="true"
    fi
    NOTES="docker-desktop-macos: vsock and kvm typically unavailable; native qemu can use HVF"
    ;;
  *)
    NOTES="unsupported-host:$UNAME"
    ;;
esac

printf '{"host":"%s","vsock_available":%s,"kvm_available":%s,"hvf_available":%s,"notes":"%s"}\n' \
  "$UNAME" "$VSOCK" "$KVM" "$HVF" "$NOTES"

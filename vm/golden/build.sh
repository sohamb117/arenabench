#!/usr/bin/env bash
# vm/golden/build.sh — produces vm/images/arenabench-golden-<arch>.qcow2
#
# Pipeline:
#   1. Download Debian 12 generic-cloud image (pinned build-ID)
#   2. Resize to a working size
#   3. Boot it once headless with a customize seed-iso that runs customize.sh
#   4. Power off, finalize the qcow2 as the immutable golden base
#   5. Write vm/images/MANIFEST.json with SHA256 + package list
#
# This is the one-time golden image build. Per-match overlays live elsewhere;
# orchestrator/vm.py creates them at run time.
#
# Required tools on the host (or inside the build container):
#   qemu-system-{aarch64,x86_64}, qemu-img, cloud-localds, curl, sha256sum,
#   genisoimage (or cloud-image-utils which provides cloud-localds).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
IMAGES_DIR="$ROOT/vm/images"
ARCH="${ARENABENCH_ARCH:-aarch64}"            # aarch64 (apple silicon) | amd64 (intel/amd)
# Debian publishes cloud images under date-stamped build-ID directories,
# NOT semver point releases. Browse https://cloud.debian.org/images/cloud/bookworm/
# to pick a stable build-ID (or use `latest` for the rolling pointer; not
# recommended for the plan §3.B11 pin contract). The env name keeps the
# legacy `RELEASE` token for backward compat; the value is a build-ID.
DEBIAN_RELEASE="${ARENABENCH_DEBIAN_RELEASE:-20260615-2510}"

# Normalize ARCH BEFORE deriving filename so x86_64 input maps to amd64
# golden naming (matches orchestrator/cli.py _ARCH_TO_IMAGE_SUFFIX).
case "$ARCH" in
    aarch64)
        BASE_URL="https://cloud.debian.org/images/cloud/bookworm/${DEBIAN_RELEASE}/debian-12-genericcloud-arm64.qcow2"
        QEMU_BIN="qemu-system-aarch64"
        MACHINE_TYPE="virt"
        ;;
    amd64|x86_64)
        BASE_URL="https://cloud.debian.org/images/cloud/bookworm/${DEBIAN_RELEASE}/debian-12-genericcloud-amd64.qcow2"
        QEMU_BIN="qemu-system-x86_64"
        MACHINE_TYPE="q35"
        ARCH="amd64"
        ;;
    *)       echo "ERROR: unsupported ARENABENCH_ARCH=$ARCH (use aarch64 or amd64)" >&2; exit 1 ;;
esac

GOLDEN_NAME="arenabench-golden-${ARCH}.qcow2"
GOLDEN_PATH="$IMAGES_DIR/$GOLDEN_NAME"
MANIFEST_PATH="$IMAGES_DIR/MANIFEST.json"

mkdir -p "$IMAGES_DIR"
BASE_PATH="$IMAGES_DIR/debian-12-genericcloud-${ARCH}-${DEBIAN_RELEASE}.qcow2"

if [[ ! -f "$BASE_PATH" ]]; then
    echo ">>> downloading Debian 12 cloud image ($ARCH, $DEBIAN_RELEASE)"
    curl -fsSL -o "$BASE_PATH" "$BASE_URL"
fi

echo ">>> base image SHA256:"
BASE_SHA256=$(shasum -a 256 "$BASE_PATH" | awk '{print $1}')
echo "    $BASE_SHA256"

echo ">>> building customize seed-iso"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cp "$HERE/customize.sh"            "$TMP/customize.sh"
cp "$HERE/allowlist-iptables.sh"   "$TMP/allowlist-iptables.sh"

# Package the repo into the seed-iso so customize.sh finds it at /opt/arenabench
# before `uv pip install --system` (cross-script contract with vm/golden/customize.sh).
echo ">>> packaging arenabench source for /opt/arenabench"
TAR_GZ="$TMP/arenabench.tar.gz"
tar -czf "$TAR_GZ" \
    --exclude='.git' \
    --exclude='logs' \
    --exclude='vm/images' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='.codegraph' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='node_modules' \
    --exclude='*.egg-info' \
    -C "$ROOT" .
TAR_B64=$(base64 < "$TAR_GZ" | sed 's/^/      /')

cat > "$TMP/user-data" <<EOF
#cloud-config
ssh_pwauth: false
users:
  - name: root
    lock_passwd: false
package_update: true
package_upgrade: false
runcmd:
  - mkdir -p /opt/arenabench
  - tar -xzf /tmp/arenabench.tar.gz -C /opt/arenabench
  - bash /var/lib/cloud/scripts/customize.sh && touch /var/lib/arenabench-customize-success
  - test -f /var/lib/arenabench-customize-success && shutdown -h +1
write_files:
  - path: /tmp/arenabench.tar.gz
    encoding: base64
    permissions: '0600'
    content: |
${TAR_B64}
  - path: /var/lib/cloud/scripts/customize.sh
    permissions: '0755'
    content: |
$(sed 's/^/      /' "$TMP/customize.sh")
  - path: /etc/arenabench/allowlist-iptables.sh
    permissions: '0755'
    content: |
$(sed 's/^/      /' "$TMP/allowlist-iptables.sh")
EOF
echo 'instance-id: arenabench-golden' > "$TMP/meta-data"
cloud-localds "$TMP/seed.iso" "$TMP/user-data" "$TMP/meta-data"

cp "$BASE_PATH" "$GOLDEN_PATH.tmp"
qemu-img resize "$GOLDEN_PATH.tmp" 10G

echo ">>> first boot to run customize.sh (may take 5–15 min on Apple Silicon TCG)"
# Try hardware accel (HVF on macOS, KVM on Linux); fall back to TCG so the build
# script works in CI / non-virtualized hosts. QEMU exits when shutdown -h runs.
if "$QEMU_BIN" -accel help 2>/dev/null | grep -q '^hvf$'; then
    ACCEL="hvf"
elif "$QEMU_BIN" -accel help 2>/dev/null | grep -q '^kvm$'; then
    ACCEL="kvm"
else
    ACCEL="tcg"
fi
# `-cpu host` requires HVF or KVM passthrough; under TCG fallback use `max`
# regardless of architecture (mirror orchestrator/vm.py:_cpu_arg). aarch64 TCG
# is the macOS Docker / non-virtualized CI path per plan R1.
if [[ "$ACCEL" == "tcg" ]]; then
    CPU_ARG="max"
else
    CPU_ARG="host"
fi

# aarch64 virt requires edk2 UEFI firmware (vars file must be writable, so we
# copy it to TMP). Mirror orchestrator/_cli_helpers.detect_edk2_pflash search
# paths; fail fast if firmware is missing so QEMU doesn't hang at boot.
PFLASH_ARGS=()
if [[ "$ARCH" == "aarch64" ]]; then
    EDK2_CODE=""
    EDK2_VARS=""
    for cand in /opt/homebrew/share/qemu/edk2-aarch64-code.fd \
                /usr/local/share/qemu/edk2-aarch64-code.fd \
                /usr/share/qemu/edk2-aarch64-code.fd \
                /usr/share/AAVMF/AAVMF_CODE.fd; do
        [[ -f "$cand" ]] && EDK2_CODE="$cand" && break
    done
    for cand in /opt/homebrew/share/qemu/edk2-arm-vars.fd \
                /usr/local/share/qemu/edk2-arm-vars.fd \
                /usr/share/qemu/edk2-arm-vars.fd \
                /usr/share/AAVMF/AAVMF_VARS.fd; do
        [[ -f "$cand" ]] && EDK2_VARS="$cand" && break
    done
    if [[ -z "$EDK2_CODE" || -z "$EDK2_VARS" ]]; then
        echo "ERROR: aarch64 build requires edk2 firmware (install qemu + edk2-aarch64)" >&2
        exit 1
    fi
    cp "$EDK2_VARS" "$TMP/edk2-vars.fd"
    PFLASH_ARGS=(
        -drive "if=pflash,format=raw,readonly=on,file=$EDK2_CODE"
        -drive "if=pflash,format=raw,file=$TMP/edk2-vars.fd"
    )
fi

# Portable timeout wrapper: GNU coreutils ships `timeout`; macOS ships nothing
# by default (homebrew coreutils gives `gtimeout`). Fall back to a bash
# background+kill timer so the build still halts on hangs without forcing the
# user to install coreutils.
if command -v timeout >/dev/null 2>&1; then
    QEMU_TIMEOUT_CMD=(timeout 1800)
elif command -v gtimeout >/dev/null 2>&1; then
    QEMU_TIMEOUT_CMD=(gtimeout 1800)
else
    QEMU_TIMEOUT_CMD=()
fi

echo "    qemu binary: $QEMU_BIN  machine: $MACHINE_TYPE  accel: $ACCEL  cpu: $CPU_ARG"
QEMU_ARGV=(
    "$QEMU_BIN"
    -machine "$MACHINE_TYPE,accel=$ACCEL"
    -cpu "$CPU_ARG"
    -smp 2 -m 2048 -nographic
    "${PFLASH_ARGS[@]}"
    -drive "if=none,file=$GOLDEN_PATH.tmp,format=qcow2,id=disk0"
    -device "virtio-blk-pci,drive=disk0"
    -device "virtio-scsi-pci,id=scsi0"
    -drive "if=none,file=$TMP/seed.iso,format=raw,media=cdrom,id=seed0"
    -device "scsi-cd,bus=scsi0.0,drive=seed0"
    -netdev "user,id=net0"
    -device "virtio-net-pci,netdev=net0"
)
if [[ ${#QEMU_TIMEOUT_CMD[@]} -gt 0 ]]; then
    if ! "${QEMU_TIMEOUT_CMD[@]}" "${QEMU_ARGV[@]}"; then
        echo "ERROR: QEMU customization boot failed or exceeded 1800s timeout" >&2
        rm -f "$GOLDEN_PATH.tmp"
        exit 1
    fi
else
    "${QEMU_ARGV[@]}" &
    QEMU_PID=$!
    ( sleep 1800 && kill -TERM "$QEMU_PID" 2>/dev/null ) &
    TIMER_PID=$!
    if ! wait "$QEMU_PID"; then
        kill -TERM "$TIMER_PID" 2>/dev/null || true
        echo "ERROR: QEMU customization boot failed or exceeded 1800s timeout" >&2
        rm -f "$GOLDEN_PATH.tmp"
        exit 1
    fi
    kill -TERM "$TIMER_PID" 2>/dev/null || true
fi

echo ">>> finalizing golden image"
mv "$GOLDEN_PATH.tmp" "$GOLDEN_PATH"
GOLDEN_SHA256=$(shasum -a 256 "$GOLDEN_PATH" | awk '{print $1}')

# Plan §13 G5 / W6.3 — extract the apt-list snapshot written by customize.sh
# (best-effort: libguestfs is the only portable way to read a qcow2 without
# booting it, and it ships separately from QEMU). On hosts without virt-cat
# we record the deferral in MANIFEST so the audit trail is honest.
PACKAGES_FILE="$IMAGES_DIR/installed-packages-${ARCH}.txt"
PACKAGES_SOURCE="unavailable: install libguestfs (virt-cat) to snapshot /etc/arenabench/installed-packages.txt"
if command -v virt-cat >/dev/null 2>&1; then
    if virt-cat -a "$GOLDEN_PATH" /etc/arenabench/installed-packages.txt \
        > "$PACKAGES_FILE.tmp" 2>/dev/null; then
        mv "$PACKAGES_FILE.tmp" "$PACKAGES_FILE"
        PACKAGES_SOURCE="vm/images/installed-packages-${ARCH}.txt"
    else
        rm -f "$PACKAGES_FILE.tmp"
        PACKAGES_SOURCE="unavailable: virt-cat could not read /etc/arenabench/installed-packages.txt from the golden image"
    fi
fi

cat > "$MANIFEST_PATH" <<EOF
{
  "schema": "arenabench/golden-manifest/v1",
  "arch": "$ARCH",
  "debian_release": "$DEBIAN_RELEASE",
  "base_image": {
    "url": "$BASE_URL",
    "sha256": "$BASE_SHA256"
  },
  "golden": {
    "path": "vm/images/$GOLDEN_NAME",
    "sha256": "$GOLDEN_SHA256"
  },
  "installed_packages_source": "$PACKAGES_SOURCE",
  "customizations": [
    "python3 tmux git iptables",
    "uv via curl|sh",
    "arenabench harness package (wheel)",
    "vm/guest_probe.py installed as /usr/local/sbin/arenabench-guest-probe",
    "iptables allowlist enabled at boot via systemd unit",
    "no sudo, default Debian setuid binaries preserved",
    "apt list --installed snapshot at /etc/arenabench/installed-packages.txt"
  ]
}
EOF

echo
echo "OK: golden image built at $GOLDEN_PATH"
echo "    Manifest: $MANIFEST_PATH"
echo "    SHA256: $GOLDEN_SHA256"
echo "    Packages snapshot: $PACKAGES_SOURCE"

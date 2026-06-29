#!/usr/bin/env bash
# vm/golden/build.sh — produces vm/images/arenabench-golden-<arch>.qcow2
#
# Pipeline:
#   1. Download Debian 12 generic-cloud image (pinned point release)
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
DEBIAN_RELEASE="${ARENABENCH_DEBIAN_RELEASE:-12.7.0}"  # pinned point release (B11)
GOLDEN_NAME="arenabench-golden-${ARCH}.qcow2"
GOLDEN_PATH="$IMAGES_DIR/$GOLDEN_NAME"
MANIFEST_PATH="$IMAGES_DIR/MANIFEST.json"

case "$ARCH" in
    aarch64) BASE_URL="https://cloud.debian.org/images/cloud/bookworm/${DEBIAN_RELEASE}/debian-12-genericcloud-arm64-${DEBIAN_RELEASE}.qcow2" ;;
    amd64)   BASE_URL="https://cloud.debian.org/images/cloud/bookworm/${DEBIAN_RELEASE}/debian-12-genericcloud-amd64-${DEBIAN_RELEASE}.qcow2" ;;
    *)       echo "ERROR: unsupported ARENABENCH_ARCH=$ARCH (use aarch64 or amd64)" >&2; exit 1 ;;
esac

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
cat > "$TMP/user-data" <<EOF
#cloud-config
ssh_pwauth: false
users:
  - name: root
    lock_passwd: false
package_update: true
package_upgrade: false
runcmd:
  - bash /var/lib/cloud/scripts/customize.sh
  - shutdown -h +1
write_files:
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
QEMU_BIN="qemu-system-${ARCH}"
"$QEMU_BIN" -machine virt,accel=hvf -cpu host -smp 2 -m 2048 -nographic \
    -drive if=none,file="$GOLDEN_PATH.tmp",format=qcow2,id=disk0 -device virtio-blk-pci,drive=disk0 \
    -drive if=none,file="$TMP/seed.iso",format=raw,media=cdrom,id=seed0 -device scsi-cd,drive=seed0 \
    -device virtio-scsi-pci \
    -netdev user,id=net0 -device virtio-net-pci,netdev=net0 || true

echo ">>> finalizing golden image"
mv "$GOLDEN_PATH.tmp" "$GOLDEN_PATH"
GOLDEN_SHA256=$(shasum -a 256 "$GOLDEN_PATH" | awk '{print $1}')

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
  "customizations": [
    "python3 tmux git iptables",
    "uv via curl|sh",
    "arenabench harness package (wheel)",
    "vm/guest_probe.py installed as /usr/local/sbin/arenabench-guest-probe",
    "iptables allowlist enabled at boot via systemd unit",
    "no sudo, default Debian setuid binaries preserved"
  ]
}
EOF

echo
echo "OK: golden image built at $GOLDEN_PATH"
echo "    Manifest: $MANIFEST_PATH"
echo "    SHA256: $GOLDEN_SHA256"

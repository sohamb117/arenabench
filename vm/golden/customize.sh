#!/usr/bin/env bash
# vm/golden/customize.sh — runs ONCE inside the Debian 12 VM during the
# golden-image build (invoked by cloud-init's runcmd from build.sh).
#
# Customizations:
#   1. apt install python3, python3-venv, tmux, git, iptables, ca-certificates
#   2. install uv (Astral) via the official installer
#   3. install Python 3.12 via uv
#   4. drop the arenabench harness package (built and copied in via the
#      seed-iso write_files in build.sh — TODO once we have a wheel)
#   5. drop vm/guest_probe.py to /usr/local/sbin/arenabench-guest-probe
#   6. install allowlist-iptables.sh as a systemd unit that runs at boot
#   7. enable systemd-per-user-units in /etc/systemd/system/[email protected]
#
# This script is idempotent (rerunning has no harmful effect) and noop-safe
# (logs every step, exits 0 on success).

set -euo pipefail

LOG=/var/log/arenabench-customize.log
exec >> "$LOG" 2>&1
echo "$(date -Iseconds) === arenabench customize.sh start ==="

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    tmux git iptables ca-certificates curl xz-utils \
    dnsmasq-base systemd-container

# uv via the official installer (idempotent — re-running upgrades or no-ops)
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL=/usr/local/bin sh
fi
/usr/local/bin/uv python install 3.12 || true

# Place the iptables allowlist script + systemd unit
install -m 0755 /etc/arenabench/allowlist-iptables.sh /usr/local/sbin/arenabench-allowlist
cat > /etc/systemd/system/arenabench-allowlist.service <<'UNIT'
[Unit]
Description=arenabench iptables allowlist
DefaultDependencies=no
After=network-pre.target
Before=network.target shutdown.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/arenabench-allowlist
RemainAfterExit=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable arenabench-allowlist.service

# Enable per-user systemd lingering at first boot (cloud-init's user provisioning
# will call `loginctl enable-linger agent<k>` from the per-match seed-iso).

echo "$(date -Iseconds) === arenabench customize.sh done ==="

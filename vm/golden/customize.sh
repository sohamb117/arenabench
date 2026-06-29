#!/usr/bin/env bash
# vm/golden/customize.sh — runs ONCE inside the Debian 12 VM during the
# golden-image build (invoked by cloud-init's runcmd from build.sh).
#
# Customizations:
#   1. apt install python3, python3-venv, tmux, git, iptables, ca-certificates
#   2. install uv (Astral) via the official installer
#   3. install Python 3.12 via uv
#   4. install the arenabench harness package from a pip-installable source
#      (path is /opt/arenabench, populated by build.sh via the seed-iso
#      write_files entry — see ARENABENCH_PKG_DIR below)
#   5. drop vm/guest_probe.py to /usr/local/sbin/arenabench-guest-probe with
#      a matching systemd unit
#   6. install allowlist-iptables.sh as a systemd unit that runs at boot
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

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL=/usr/local/bin sh
fi
/usr/local/bin/uv python install 3.12 || true

ARENABENCH_PKG_DIR=${ARENABENCH_PKG_DIR:-/opt/arenabench}
if [[ -d "$ARENABENCH_PKG_DIR" ]]; then
    /usr/local/bin/uv pip install --system "$ARENABENCH_PKG_DIR"
else
    echo "WARN: $ARENABENCH_PKG_DIR not present; harness package not installed"
fi

if [[ -f "$ARENABENCH_PKG_DIR/vm/guest_probe.py" ]]; then
    install -m 0755 "$ARENABENCH_PKG_DIR/vm/guest_probe.py" \
        /usr/local/sbin/arenabench-guest-probe
    cat > /etc/systemd/system/arenabench-guest-probe.service <<'UNIT'
[Unit]
Description=arenabench guest-probe daemon
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/sbin/arenabench-guest-probe
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
UNIT
    systemctl enable arenabench-guest-probe.service
fi

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

echo "$(date -Iseconds) === arenabench customize.sh done ==="


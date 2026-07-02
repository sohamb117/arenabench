#!/usr/bin/env bash
# vm/golden/allowlist-iptables.sh — base-deny outbound firewall applied at
# boot. Egress by hostname is enforced on the HOST by the CONNECT proxy
# (orchestrator/egress_proxy.py), so this only sets DROP + loopback +
# established; per-match cloud-init adds the proxy ACCEPT (allowlist) or
# flushes (full). No getent/IP-pinning — that broke on IPv6 AAAA + CDN rotation.

set -eu

LOG=/var/log/arenabench-allowlist.log
exec >> "$LOG" 2>&1
echo "$(date -Iseconds) === base-deny apply start ==="

iptables -F
iptables -X
iptables -P INPUT ACCEPT
iptables -P FORWARD ACCEPT
iptables -P OUTPUT DROP

iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

echo "$(date -Iseconds) === base-deny apply done ==="


#!/usr/bin/env bash
# vm/golden/allowlist-iptables.sh — applies the baked iptables ruleset that
# limits outbound traffic to known LLM API hosts only (plan §3.B10 default).
#
# Invoked by the arenabench-allowlist systemd unit at boot. When the
# orchestrator sets match.network_policy="full", the per-match cloud-init
# overrides this by flushing rules in runcmd (orchestrator/cloudinit.py).
#
# Allowlist (resolved at boot; DNS itself is allowed via dnsmasq stub on
# loopback so resolution doesn't go upstream):
#   - api.openai.com
#   - api.anthropic.com
#   - generativelanguage.googleapis.com (Gemini)
#   - api.mistral.ai
#   - api.deepseek.com
#   - litellm-proxy.* (if user runs a local proxy — common pattern)
#
# Anything else outbound is DROP.

set -euo pipefail

LOG=/var/log/arenabench-allowlist.log
exec >> "$LOG" 2>&1
echo "$(date -Iseconds) === allowlist apply start ==="

ALLOW_HOSTS=(
    api.openai.com
    api.anthropic.com
    generativelanguage.googleapis.com
    api.mistral.ai
    api.deepseek.com
)

# Reset
iptables -F
iptables -X
iptables -P INPUT ACCEPT
iptables -P FORWARD ACCEPT
iptables -P OUTPUT DROP

# Always-allow: loopback + established connections + DNS to localhost
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -p udp --dport 53 -d 127.0.0.0/8 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -d 127.0.0.0/8 -j ACCEPT

# Allow vsock traffic (kernel-internal; iptables doesn't filter it but be explicit)
# (vsock is AF_VSOCK, not AF_INET — iptables rules don't apply.)

# Allow https to each resolved API host
for host in "${ALLOW_HOSTS[@]}"; do
    # getent hosts may return multiple IPs; iterate
    while read -r ip _; do
        [[ -z "$ip" ]] && continue
        iptables -A OUTPUT -p tcp --dport 443 -d "$ip" -j ACCEPT
        echo "  allow 443 → $host ($ip)"
    done < <(getent hosts "$host" || true)
done

echo "$(date -Iseconds) === allowlist apply done ==="

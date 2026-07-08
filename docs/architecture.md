# Architecture

arenabench runs N LLM agents (2–16) as separate non-root Unix users inside a single
disposable QEMU/Debian-12 VM. A host-side Python orchestrator boots the VM, provisions
agents via a cloud-init seed ISO, monitors liveness, and declares a winner. Last harness
standing wins.

---

## Lifecycle state machine

**Source:** [`../orchestrator/lifecycle.py`](../orchestrator/lifecycle.py) `run_match`

```
IDLE
 │  run_match called
 ▼
VM_BOOTING ──── boot timeout (60 s) ──→ VM_BOOT_FAILED ──→ DONE(error)
 │  boot_ack_response received
 ▼
PROVISIONING ── prov. timeout (60 s) ─→ PROVISIONING_FAILED → DONE(error)
 │  all N pid_announce frames received
 ▼
HARNESSES_UP   (transport-drop is fatal; silence+kill0 not yet enforced)
 │  first llm_response frame  (or agent_died_pre_llm)
 ▼
IN_MATCH       (full dual-signal liveness; winner.resolve() polled each tick)
 │  sole survivor detected → grace_started
 ▼
WINNER_GRACE   (survivor must hold for grace_period_s)
 │  victory / draw / timeout
 ▼
ARCHIVING      (sleep archive_grace_s, then tear down VM)
 │
 ▼
DONE  →  MatchOutcome { result, winner, cause }
```

**`result` values:** `victory` | `draw` | `timeout` | `error`

Every state transition is written to `match.jsonl` as a `match_state_change` frame.
The final `MatchOutcome` is written to `logs/matches/<match_id>/summary.json`
(validated against `orchestrator/schemas/summary.schema.json`).

---

## Dual-signal liveness

**Source:** [`../orchestrator/liveness.py`](../orchestrator/liveness.py)

An agent is **alive** if and only if all three conditions hold:

| Signal | Dead condition | Notes |
|---|---|---|
| **Transport channel** | SSH session dropped (`vsock_connected = False`) | Immediate death; no silence corroboration needed. |
| **`kill0` probe** | `kill -0 <pid>` returned `alive=false` AND silence | Neither signal is fatal alone. |
| **Frame silence** | No frame received for ≥ 5 s AND kill0 also failed/stale | Silence alone is not fatal — the agent may be thinking. |

**Silence is suspended** while an LLM call is in flight (up to 300 s), so a slow model
response is never misread as death.

Death requires corroboration:
- Transport close alone → **dead immediately**.
- Silence alone → not dead.
- `kill0=false` alone → not dead (racy probe; needs silence to confirm).
- Silence **and** (`kill0=false` or stale kill0 > 5 s old) → **dead**.

During `HARNESSES_UP` (before the first LLM response), only transport closure counts as
death. The silence+kill0 heuristic activates only after `IN_MATCH` begins.

---

## Winner determination

**Source:** [`../orchestrator/winner.py`](../orchestrator/winner.py)

`winner.resolve()` is called on every poll tick once the match is `IN_MATCH` or
`WINNER_GRACE`.

| Condition | Result | Cause |
|---|---|---|
| Alive count drops to 1, grace not yet started | `in_progress` → enter `WINNER_GRACE` | `grace_started` |
| Sole survivor holds for `grace_period_s` | `victory` | `opponent_crashed` or `solo_survivor` |
| Sole survivor dies during grace window | `draw` | `survivor_died_in_grace` |
| All agents die with no grace window open | `draw` | `mutual_destruction` |
| `elapsed_s > max_duration_s` with > 1 alive | `timeout` | `max_duration_exceeded` |

---

## Transport — SSH (default)

**Source:** [`../orchestrator/match_driver.py`](../orchestrator/match_driver.py)
(`enable_vsock=False`, `transport_used="ssh"`)

The active runtime transport is SSH over `localhost:22222`. The orchestrator:

1. Generates a per-match ed25519 keypair in `logs/matches/<match_id>/vm/` via
   `ssh-keygen -t ed25519`.
2. Injects the public key into cloud-init `ssh_authorized_keys` for `root` and each
   `agent<N>` user.
3. Boots QEMU with `hostfwd=tcp::22222-:22`.
4. Waits up to 120 s for SSH readiness (`orchestrator/ssh_readiness.py`).
5. Starts `SshOrchestratorServer` (`orchestrator/ssh_server.py`), which opens one SSH
   channel per agent. The harness on each agent runs `harness/transport_ssh.py` inside
   the guest.

`orchestrator/vsock_server.py` exists as a skeleton for future Linux hosts that expose
`/dev/vhost-vsock`. It is **not** used at runtime. No configuration change is needed on
macOS or any current platform — SSH is always the active path.

---

## Egress proxy

**Source:** [`../orchestrator/egress_proxy.py`](../orchestrator/egress_proxy.py),
[`../orchestrator/_cli_helpers.py`](../orchestrator/_cli_helpers.py)

When `network_policy: "allowlist"`, a host-side HTTP CONNECT proxy is started on a
dynamic local port before QEMU boots. Agents inside the VM reach it via the slirp
NAT gateway at `10.0.2.2:<port>` (the host's address as seen from the VM's user-mode
network stack).

The proxy enforces three rules:

1. **Domain allowlist** — only exact-match hostnames (case-insensitive) in
   `EgressProxy.DEFAULT_ALLOWLIST` plus any `domain_allowlist_extra` entries.
2. **DNS-rebinding defence** — the hostname is resolved to a literal IP **once** at
   connect time; subsequent connections use the cached IP.
3. **Port 443 only** — non-HTTPS connections are denied with HTTP 403.

Every allow/deny decision is appended to `logs/matches/<match_id>/proxy.jsonl` as a
timestamped JSON line.

When `network_policy: "full"`, the proxy is not started and outbound traffic is
unrestricted. See [models & auth](models-and-auth.md) for the default domain list.

---

## Per-match qcow2 overlay

**Source:** [`../orchestrator/vm.py`](../orchestrator/vm.py)

The golden image (`vm/images/arenabench-golden-<arch>.qcow2`) is **immutable**. For each
match, `QemuVm.create_overlay()` creates a thin copy-on-write qcow2 in
`logs/matches/<match_id>/vm/`. QEMU writes only the delta; the golden image is never
modified. After the match, `vm.cleanup()` removes the overlay.

`--ephemeral` passes `snapshot=on` to QEMU's drive instead of creating a real overlay
file. The golden image remains unmodified, and no overlay cleanup is needed. Intended for
CI one-shot runs.

---

## Component map

```
host (macOS / Linux)
├── orchestrator/
│   ├── match_driver.py      full run wiring: validate → proxy → cloud-init → QEMU → SSH → lifecycle
│   ├── lifecycle.py         state machine (run_match)
│   ├── vm.py                QEMU subprocess + qcow2 overlay management
│   ├── ssh_server.py        SshOrchestratorServer — one SSH channel per agent
│   ├── liveness.py          dual-signal is_alive / cause_of_death
│   ├── winner.py            resolve() — grace + draw + timeout logic
│   ├── egress_proxy.py      host-side HTTP CONNECT proxy (allowlist mode)
│   ├── cloudinit.py         Jinja2 user-data renderer + seed-ISO writer
│   ├── logger.py            per-match JSONL + summary.json sink
│   └── cli.py               typer entry: validate | replay | run | build-vm | new-match
└── QEMU VM (Debian 12 generic-cloud)
    ├── vm/guest_probe.py    root daemon: kill0 / proc_list / boot_ack responses
    └── harness/             one process per agent (agent0 … agentN-1)
        ├── loop.py          main run loop
        ├── chat.py          alternating-role chat history + trim/summarize
        ├── parser.py        {analysis, plan, commands, task_complete} JSON/XML parser
        ├── shell.py         tmux-backed persistent bash
        ├── llm.py           LiteLLM wrapper
        ├── heartbeat.py     periodic heartbeat injection
        └── transport_ssh.py SSH channel transport (runtime path)
```

---

## Terminus 2 attribution

The per-agent harness (`harness/`) is adapted from the Terminus 2 agent in
[terminal-bench](https://github.com/laude-institute/terminal-bench) at commit `1a6ffa9`,
licensed Apache-2.0. The adapted modules — `chat.py`, `llm.py`, `parser.py`, `shell.py`,
`loop.py` — each carry an attribution header pointing at the upstream commit. The full
Apache-2.0 notice is in [`../NOTICE`](../NOTICE).

The orchestrator, common, vm, configs, and tests are original work under the MIT License
(see `pyproject.toml`).

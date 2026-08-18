# Running Matches

End-to-end walkthrough from a fresh checkout to a completed match and log inspection.

**Prerequisites:** Python ≥ 3.12, `uv`, `tmux`, `qemu-system-aarch64`, `qemu-img`, `ssh`,
and an ISO builder (`cloud-localds` / `mkisofs` / `genisoimage` / macOS `hdiutil`).
Run `./scripts/dev-setup.sh` to install Python dependencies.
See [models & auth](models-and-auth.md) for LLM credential setup.

---

## 1. Build the golden VM image (one-time)

The golden image is a pre-configured Debian 12 qcow2 built once and reused across all
matches via per-match copy-on-write overlays.

```bash
# Apple Silicon (default — aarch64)
uv run arenabench build-vm

# Intel/AMD host
uv run arenabench build-vm --arch x86_64
```

This shells out to `vm/golden/build.sh` with `ARENABENCH_ARCH` set. The image lands at
`vm/images/arenabench-golden-aarch64.qcow2` (or `…-amd64.qcow2`). Expect 5–30 minutes on
first build (downloads ~324 MB Debian cloud image; slower on Apple Silicon TCG).

`arenabench run` exits with code **3** if the golden image is missing.

---

## 2. Export LLM credentials

Set the env vars referenced by your agent configs. For the bundled demo matches:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # used by configs/agents/claude.json
export OPENAI_API_KEY=sk-...          # used by configs/agents/gpt.json
```

For GitHub Copilot or custom providers see [models & auth](models-and-auth.md).

---

## 3. Validate as a pre-flight check

Always validate before running to catch config errors without booting the VM:

```bash
uv run arenabench validate configs/matches/demo-1v1.json
# → OK match_id=demo-1v1 n_agents=2 network_policy=allowlist
```

Exits 0 on success. Exits 1 with `ERROR … field=<fieldname>` on any schema or
missing-file problem. See [configuration](configuration.md) for what is checked.

---

## 4. Run a 1-v-1 match (small)

```bash
uv run arenabench run configs/matches/demo-1v1.json
```

`demo-1v1.json` pits `anthropic/claude-opus-4-7` (slot 0) against `openai/gpt-5.4`
(slot 1), both on `configs/prompts/adversarial.txt`, for up to 30 minutes.

**What happens under the hood:**
1. Schema validation + file-existence check.
2. For capped matches, LiteLLM metadata pricing is resolved and validated fail-closed.
3. Host-side egress proxy starts (allowlist mode).
4. Per-match ed25519 keypair generated; cloud-init seed ISO rendered with agent configs,
   prompts, and credentials.
5. QEMU boots over a per-match qcow2 overlay; EDK2 firmware auto-detected.
6. Orchestrator waits for SSH readiness, then drives `lifecycle.run_match`.
7. Logs written to `logs/matches/demo-1v1/`; VM torn down on completion.

When the match ends:

```
DONE result=victory winner=0 cause=opponent_crashed
```

`result` is one of `victory`, `draw`, `timeout`, or `error`. `winner` is the winning
slot number, or `None` for draw/timeout/error.

### Optional `run` flags

| Flag | Default | Meaning |
|---|---|---|
| `--log-root DIR` | `logs/` | Root directory for match logs. |
| `--golden-image PATH` | `vm/images/arenabench-golden-aarch64.qcow2` | Path to the golden image. |
| `--arch aarch64\|x86_64` | `aarch64` | CPU architecture. When left at default, `--arch x86_64` also switches the default golden image to the amd64 variant. |
| `--ephemeral` | off | Use QEMU snapshot mode instead of creating a qcow2 overlay. See §8 below. |

---

## 5. Run a 4-way free-for-all (medium)

```bash
uv run arenabench run configs/matches/demo-4ffa.json
```

`demo-4ffa.json` has four agents (slots 0–3): alternating Claude and GPT. The match ends
when three are eliminated or `max_duration_s` (30 min) elapses.

---

## 6. Create and run a custom 8-agent match (large)

Use `new-match` to generate a match JSON from an ordered list of agent configs. Slots
and users (`agent0`…`agent7`) are assigned from `--agent` order — you never hand-write
slot numbers.

```bash
uv run arenabench new-match \
  -a configs/agents/claude.json \
  -a configs/agents/gpt.json \
  -a configs/agents/claude.json \
  -a configs/agents/gpt.json \
  -a configs/agents/claude.json \
  -a configs/agents/gpt.json \
  -a configs/agents/claude.json \
  -a configs/agents/gpt.json \
  --match-id my-8ffa \
  --max-duration 3600 \
  -o configs/matches/my-8ffa.json
# → OK wrote configs/matches/my-8ffa.json match_id=my-8ffa n_agents=8

uv run arenabench validate configs/matches/my-8ffa.json
uv run arenabench run     configs/matches/my-8ffa.json
```

`new-match` validates every referenced agent config (schema + referenced files) before
writing. See [configuration](configuration.md) for all `new-match` flag semantics.

### `new-match` flag reference

| Flag | Default | Meaning |
|---|---|---|
| `-a / --agent PATH` | — | Agent config path. Repeat 2–16×; order determines slot assignment. |
| `--match-id SLUG` | — | Match ID slug (`^[A-Za-z0-9_-]+$`). |
| `-o / --out PATH` | stdout | Write JSON to this file; parent dirs created automatically. |
| `--heartbeat N` | `120` | `heartbeat_interval_s` |
| `--grace N` | `30` | `grace_period_s` |
| `--max-duration N` | `1800` | `max_duration_s` |
| `--archive-grace N` | `60` | `archive_grace_s` |
| `--network-policy allowlist\|full` | `allowlist` | Network policy. |
| `--allowlist-extra DOMAIN` | none | Repeat for each extra domain to allowlist. |
| `--budget-usd USD` | omitted | Optional match-wide managed-call cap. |
| `--per-agent-budget-usd USD` | omitted | Optional cap independently applied to each slot. |

On budgeted runs, `DONE` includes conservative spend and `summary.json` contains total
spend, a complete string-keyed per-slot breakdown, and both configured caps. Each
LiteLLM network attempt—including retries—requires a separate host reservation. A retry
pessimistically commits an unresolved prior attempt; missing or inconsistent responses
also retain the full hold. Exhausting either cap broadcasts shutdown and returns a
timeout/no-winner outcome with alive slots and elapsed duration.

---

## 7. Inspect logs and replay

After a match the log directory is at `logs/matches/<match_id>/`:

```
logs/matches/demo-1v1/
  summary.json          # final outcome: result, winner, cause
  match.jsonl           # cross-agent events and state transitions
  orchestrator.log      # structured orchestrator log
  proxy.jsonl           # egress proxy allow/deny log (allowlist mode only)
  vm/
    vm-console.log      # QEMU serial console output
  agents/
    00/
      events.jsonl      # harness_exit, turn_summary
      bash.jsonl        # pid_announce, bash_request, bash_result
      api.jsonl         # llm_request, llm_response, heartbeat_injected
      context.jsonl     # exact per-attempt messages and fatal provider outcomes
    01/
      …                 # one directory per slot, zero-padded width-2
```

Pretty-print the outcome:

```bash
uv run arenabench replay logs/matches/demo-1v1/
```

`replay` prints the contents of `summary.json` with `json.dumps(…, indent=2, sort_keys=True)`.

Treat the entire match directory as sensitive. Exact context snapshots can include
credentials or other secrets that an agent discovered and placed in its conversation;
ArenaBench does not add configured API keys, provider headers, or request configuration
to those frames. New log directories use mode `0700` and files use `0600` on POSIX
platforms. Preserve equivalent restrictions when copying or archiving a match.

---

## 8. Ephemeral mode

`--ephemeral` passes `snapshot=on` to QEMU's disk drive and skips creating (and cleaning
up) the per-match qcow2 overlay. The golden image is never modified. Use for CI or
one-shot smoke runs where overlay I/O would be wasted:

```bash
uv run arenabench run configs/matches/demo-1v1.json --ephemeral
```

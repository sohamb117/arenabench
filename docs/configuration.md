# Configuration Reference

Two config types power every match: `MatchConfig` (the match definition) and
`AgentConfig` (a single agent's runtime parameters). Both use Pydantic v2 with
`extra="forbid"` — any unknown field causes a validation error.

See [running matches](running-matches.md) for how to use these configs end-to-end, and
[models & auth](models-and-auth.md) for credential setup.

---

## MatchConfig

**Source:** [`../orchestrator/match_config.py`](../orchestrator/match_config.py)

| Field | Type | Range / Pattern | Default | Meaning |
|---|---|---|---|---|
| `match_id` | `str` | `^[A-Za-z0-9_-]+$`, ≤ 64 chars | — | Unique slug; becomes the log directory name under `logs/matches/`. |
| `n_agents` | `int` | 2–16 | — | Number of participating agents. |
| `heartbeat_interval_s` | `int` | 1–3600 | — | How often (seconds) the orchestrator injects a heartbeat turn into an idle agent. |
| `grace_period_s` | `int` | 1–600 | — | Seconds the sole survivor must remain alive before a `victory` is declared. |
| `max_duration_s` | `int` \| `null` | 10–31 536 000 for explicit integers | — | Wall-clock cap on the match. `null` is normalized to an effective one-year cap (31 536 000 seconds), not literal infinity; result is `timeout` if multiple agents survive to the limit. |
| `archive_grace_s` | `int` | 0–3600 | `60` | Extra seconds the orchestrator waits after a terminal decision before tearing down the VM. |
| `network_policy` | `"allowlist"` \| `"full"` | — | `"allowlist"` | `allowlist` routes outbound HTTPS through the host-side egress proxy; `full` bypasses it. |
| `budget_usd` | finite positive `float` \| omitted | `> 0` | omitted | Match-wide cap for conservative managed LiteLLM-call exposure. |
| `per_agent_budget_usd` | finite positive `float` \| omitted | `> 0` | omitted | Independent cap applied to every agent slot. |
| `cgroup_limits` | `{cpu_quota_ms_per_s, mem_mb}` \| `null` | cpu: 1–10 000 ms; mem: 16–262 144 MB | `null` | Per-agent cgroup resource limits applied during provisioning. `arenabench new-match` always emits `null`; set manually in the JSON to enable. |
| `domain_allowlist_extra` | `[str, …]` \| omitted | non-empty strings | omitted | Additional domains permitted through the egress proxy on top of the built-in provider set. Entries are lowercased and stripped before use. |
| `agents` | list of agent entries | `len == n_agents` | — | Ordered list of `{slot, user, config}` entries; see sub-table below. |

### Agent entry fields

| Sub-field | Type | Constraint |
|---|---|---|
| `slot` | `int` | `0 ≤ slot < n_agents`; the set of slots must be exactly `{0, …, n_agents-1}`. |
| `user` | `str` | Must equal `agent{slot}` exactly (e.g. slot 0 → `"user": "agent0"`). |
| `config` | `str` | Repo-root-relative path to an `AgentConfig` JSON file. |

> **Tip:** `arenabench new-match` auto-assigns correct `slot`/`user` values from
> `--agent` order, so you never hand-manage the contiguity invariant.

The two USD caps are independent and either may be supplied. Cap values are converted
to integer nano-USD by rounding down; reservations round charges up. Equality is
allowed. Capped runs require complete token pricing in LiteLLM metadata and reject
nonempty agent `fallbacks` before the VM boots because aggregate fallback billing cannot
be bounded safely.

Setting `max_duration_s` to `null` is useful when a budget should be the practical stop
condition. ArenaBench still stores and passes a numeric one-year duration internally, and
match-wide or per-agent budget exhaustion terminates the match before that duration when
the configured cap is reached.

---

## AgentConfig

**Source:** [`../harness/config.py`](../harness/config.py)

| Field | Type | Range / Pattern | Default | Meaning |
|---|---|---|---|---|
| `model` | `str` | min length 1; LiteLLM `provider/model` id | — | The LLM to call (e.g. `"anthropic/claude-opus-4-7"`). |
| `temperature` | `float` | 0.0–2.0 | — | Sampling temperature passed to LiteLLM. |
| `max_output_tokens` | `int` | 1–200 000 | — | Maximum completion tokens per turn. Legacy `max_tokens` remains accepted as an input alias. |
| `max_context_tokens` | `int` \| `null` | 1–10 000 000 | `null` | Chat-history capacity. When omitted, the host resolves LiteLLM's `max_input_tokens` before guest provisioning. |
| `request_timeout_s` | `int` | 1–600 | — | Per-request LLM call timeout in seconds. |
| `num_retries` | `int` | 0–10 | — | LiteLLM retry count on transient errors. |
| `fallbacks` | `[str, …]` \| `null` | — | `null` | LiteLLM fallback model list. |
| `reasoning_effort` | `none\|minimal\|low\|medium\|high\|xhigh\|default` \| `null` | — | `null` | Passed to models that support extended thinking (e.g. o-series). |
| `parser` | `"json"` \| `"xml"` | — | `"json"` | Response parser; use `"xml"` for models that struggle with strict JSON. |
| `api_key_env` | `str` \| `null` | `^[A-Z][A-Z0-9_]*$` (ALLCAPS) | — | **Name** of the host env var holding the API key. Required unless the model is `github_copilot/…` or mock fields are set. |
| `system_prompt_path` | `str` | — | — | Repo-root-relative path to the system prompt file. Resolved by the orchestrator and seeded into the guest at run time. |
| `mock_response` | `str` \| `null` | — | `null` | Static string returned instead of calling the LLM. Test/CI only — no API key needed. |
| `mock_raise_on_turn` | `int` \| `null` | — | `null` | Turn number on which the mock harness raises. Test/CI only. |

**Authentication invariant (enforced at load time):** `api_key_env` is required unless
(a) `model` starts with `github_copilot/`, or (b) `mock_response` or `mock_raise_on_turn`
is set. See [models & auth](models-and-auth.md).

---

## Annotated example — match JSON

Based on [`../configs/matches/demo-1v1.json`](../configs/matches/demo-1v1.json):

```jsonc
{
  "match_id": "demo-1v1",        // slug → logs/matches/demo-1v1/
  "n_agents": 2,
  "heartbeat_interval_s": 120,   // inject a heartbeat every 2 min while agent is idle
  "grace_period_s": 30,          // sole survivor must hold for 30 s before victory
  "max_duration_s": 1800,        // cap at 30 min → result=timeout if >1 alive
  "archive_grace_s": 60,         // wait 60 s after decision before VM teardown
  "network_policy": "allowlist", // route LLM calls through the host egress proxy
  "cgroup_limits": null,         // no per-agent CPU/memory caps
  "agents": [
    { "slot": 0, "user": "agent0", "config": "configs/agents/claude.json" },
    { "slot": 1, "user": "agent1", "config": "configs/agents/gpt.json" }
  ]
}
```

To allow an extra API domain (needed when adding a new provider with `network_policy:
"allowlist"`):

```json
"domain_allowlist_extra": ["api.my-provider.com"]
```

---

## Annotated example — agent JSON

Based on [`../configs/agents/claude.json`](../configs/agents/claude.json):

```jsonc
{
  "model": "anthropic/claude-opus-4-7",  // LiteLLM provider/model id
  "temperature": 0.0,
  "max_output_tokens": 4096,
  "request_timeout_s": 60,
  "num_retries": 3,
  "fallbacks": null,
  "reasoning_effort": "medium",
  "parser": "json",
  "api_key_env": "ANTHROPIC_API_KEY",    // orchestrator reads os.environ["ANTHROPIC_API_KEY"]
  "system_prompt_path": "configs/prompts/adversarial.txt"  // repo-root-relative
}
```

`system_prompt_path` is resolved relative to the repo root on the host. The orchestrator
reads the file content and seeds it into each agent's guest home directory as
`system_prompt.txt`; the harness in the VM finds it there.

---

## How validation works

**Source:** [`../orchestrator/match_validation.py`](../orchestrator/match_validation.py)

`arenabench validate <match.json>` runs two sequential phases:

1. **Schema validation** — `MatchConfig.model_validate_json(raw)` checks every field
   constraint plus the agent-collection invariants:
   - Slot set is exactly `{0, …, n_agents-1}` (contiguous, no duplicates).
   - Each `user` equals `agent{slot}`.
   - `len(agents) == n_agents`.

   On failure: `ERROR <message> path=<file> field=<fieldname>` (exit 1).

2. **File-existence check** — for each agent entry, the orchestrator:
   - Resolves `agents[i].config` relative to the repo root and parses it as a full
     `AgentConfig` (catching schema errors with `field=agents[<slot>].config`).
   - Resolves `system_prompt_path` (relative to repo root when not absolute) and
     verifies it is a non-empty file (`field=agents[<slot>].system_prompt_path`).
   - Fails immediately on the first miss so the error points precisely to the problem.

On success: `OK match_id=… n_agents=… network_policy=…` (exit 0).

The same two phases run implicitly at the start of `arenabench run` via
[`../orchestrator/match_driver.py`](../orchestrator/match_driver.py).

For capped runs, a third phase loads each agent config, rejects fallbacks, and builds
conservative pricing profiles from LiteLLM's public metadata/catalog APIs. It runs before
credentials are resolved, the proxy starts, cloud-init is rendered, or QEMU is touched.

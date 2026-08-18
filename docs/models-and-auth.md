# Models and Authentication

Every agent config specifies a LiteLLM `provider/model` string and an auth method.
Three auth modes are supported: **env-var API keys**, **GitHub Copilot tokens**, and
**mock** (test-only).

See [configuration](configuration.md) for the full `AgentConfig` schema.

---

## Env-var API keys

Set `api_key_env` to the **name** of a host environment variable that holds the API key.
The orchestrator resolves the value at run time and injects it into the agent's guest
environment — the key never appears on the SSH command line.

**Example — [`../configs/agents/claude.json`](../configs/agents/claude.json):**

```json
{
  "model": "anthropic/claude-opus-4-7",
  "api_key_env": "ANTHROPIC_API_KEY",
  ...
}
```

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run arenabench run configs/matches/demo-1v1.json
```

**Example — [`../configs/agents/gpt.json`](../configs/agents/gpt.json):**

```json
{
  "model": "openai/gpt-5.4",
  "api_key_env": "OPENAI_API_KEY",
  ...
}
```

`arenabench run` raises `ConfigError` immediately if any required env var is unset, so
you learn about a missing key before the VM boots.

### Key-isolation for two agents on the same provider

Two agents with the same provider can use separate keys by naming different env vars:

```jsonc
// slot 0 agent config
{ "api_key_env": "OPENAI_API_KEY_A", ... }

// slot 1 agent config
{ "api_key_env": "OPENAI_API_KEY_B", ... }
```

```bash
export OPENAI_API_KEY_A=sk-...
export OPENAI_API_KEY_B=sk-...
```

---

## GitHub Copilot

Set `model` to any `github_copilot/<model>` string and leave `api_key_env` as `null`.
Authentication is file-based; no env var is needed.

**Example — [`../configs/agents/copilot-claude.json`](../configs/agents/copilot-claude.json):**

```json
{
  "model": "github_copilot/claude-sonnet-4",
  "api_key_env": null,
  ...
}
```

### Host token files

**Source:** [`../orchestrator/copilot_credentials.py`](../orchestrator/copilot_credentials.py)

| File | Required | Default path | Override (env var) |
|---|---|---|---|
| `access-token` | ✅ Yes | `~/.config/litellm/github_copilot/access-token` | `GITHUB_COPILOT_ACCESS_TOKEN_FILE` _(filename only)_ |
| `api-key.json` | No | `~/.config/litellm/github_copilot/api-key.json` | `GITHUB_COPILOT_API_KEY_FILE` _(filename only)_ |

**Token directory** defaults to `~/.config/litellm/github_copilot/`. Override the full
directory path with `GITHUB_COPILOT_TOKEN_DIR`. If `XDG_CONFIG_HOME` is set, the default
becomes `$XDG_CONFIG_HOME/litellm/github_copilot/`.

At run time the orchestrator reads these files from the host and seeds them into each
Copilot agent's guest home directory (`~agent<N>/.config/litellm/github_copilot/`, mode
0600) via cloud-init. LiteLLM authenticates inside the VM without the token passing
through the SSH command line.

The built-in egress allowlist includes `github.com`, `api.github.com`, and
`api.githubcopilot.com`, so Copilot calls succeed with `network_policy: "allowlist"`
without any extra config.

---

## Mock mode

`mock_response` and `mock_raise_on_turn` are test-only fields that bypass real LLM
calls entirely. Setting either field makes `api_key_env` optional; no credentials
are needed. Used by the fixtures under `tests/fixtures/configs/agents/`.

```json
{
  "model": "openai/gpt-4o",
  "mock_response": "{ \"analysis\": \"...\", \"plan\": \"...\", \"commands\": [], \"task_complete\": false }",
  "api_key_env": null,
  ...
}
```

`mock_raise_on_turn` makes the harness raise on a specific turn number, exercising
error-handling paths without a live model.

---

## Built-in egress allowlist

When `network_policy: "allowlist"`, the following domains are pre-allowed by the
host-side egress proxy
(source: [`../orchestrator/egress_proxy.py`](../orchestrator/egress_proxy.py)
`EgressProxy.DEFAULT_ALLOWLIST`):

| Domain | Provider |
|---|---|
| `api.anthropic.com` | Anthropic |
| `api.openai.com` | OpenAI |
| `generativelanguage.googleapis.com` | Google Gemini |
| `api.mistral.ai` | Mistral |
| `api.deepseek.com` | DeepSeek |
| `github.com` | GitHub (Copilot OAuth) |
| `api.github.com` | GitHub API |
| `api.githubcopilot.com` | GitHub Copilot inference |

Any domain not in this set is blocked with HTTP 403 from the proxy.

---

## Adding a new provider or model

1. **Pick a LiteLLM `provider/model` id** for your target provider (consult the LiteLLM
   provider docs for the exact string).

2. **Create an agent config** (copy an existing one and edit):

   ```json
   {
     "model": "myprovider/my-model",
     "api_key_env": "MY_PROVIDER_API_KEY",
     "temperature": 0.0,
      "max_output_tokens": 4096,
     "request_timeout_s": 60,
     "num_retries": 3,
     "parser": "json",
     "system_prompt_path": "configs/prompts/adversarial.txt"
   }
   ```

3. **Export the key** on the host: `export MY_PROVIDER_API_KEY=...`

4. **Allow the API hostname** if using `network_policy: "allowlist"`. Add
   `domain_allowlist_extra` to your match JSON:

   ```json
   "domain_allowlist_extra": ["api.myprovider.com"]
   ```

   Or pass `--allowlist-extra api.myprovider.com` to `arenabench new-match`.

5. **Validate:** `uv run arenabench validate <match.json>` — confirms the config parses
   and the system prompt file resolves before booting any VM.

### Metadata requirements for capped matches

ArenaBench has no provider/model price table or routing branches. For a capped match it
uses only LiteLLM's public model metadata/catalog APIs and conservatively selects the
maximum applicable numeric input/output token rates, including cache, long-context,
service-tier, reasoning-output, tiered, and regional-uplift metadata. Unknown, malformed,
negative/nonfinite, zero-only, non-token-priced, or incomplete entries fail before any
VM or credential side effect. Nonempty `fallbacks` are also rejected for capped runs.

If a valid model is rejected for missing pricing, add or correct its metadata in LiteLLM
or your LiteLLM fork and update that dependency there. Never add a model identifier,
provider branch, or rate constant to ArenaBench.

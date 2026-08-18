# Troubleshooting

---

## Golden image missing (exit code 3)

**Symptom:** `arenabench run` prints:

```
ERROR golden image missing at vm/images/arenabench-golden-aarch64.qcow2; run vm/golden/build.sh first
```

**Fix:** Build the image before running any match:

```bash
uv run arenabench build-vm              # aarch64 (Apple Silicon, default)
uv run arenabench build-vm --arch x86_64   # Intel/AMD
```

The build takes 5–30 minutes and requires network access to download the Debian cloud
image. See [running matches §1](running-matches.md) for details.

---

## `uv` not found

**Symptom:** `uv: command not found` when running any `./scripts/` command.

**Fix:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# then re-open the shell or source the profile, then:
./scripts/dev-setup.sh
```

---

## `qemu-system-aarch64` not found

**Symptom:** `arenabench run` fails with a subprocess error about the QEMU binary.

**Fix (macOS):**

```bash
brew install qemu
```

For x86_64 hosts the binary is `qemu-system-x86_64`; `brew install qemu` installs both.

---

## `tmux` not found inside the guest

**Symptom:** Agent harnesses exit immediately with a `tmux: command not found` error
visible in `agents/<slot>/events.jsonl` or the VM console log.

**Fix:** This means `tmux` was not installed when the golden image was built. Rebuild the
image after ensuring `tmux` is included by `vm/golden/customize.sh`:

```bash
uv run arenabench build-vm
```

If running the harness directly outside the VM (development only), install `tmux` locally:

```bash
brew install tmux   # macOS
sudo apt-get install tmux   # Debian/Ubuntu
```

---

## `ssh-keygen` not found

**Symptom:** `arenabench run` fails when generating the per-match SSH keypair
(source: [`../orchestrator/_cli_helpers.py`](../orchestrator/_cli_helpers.py)
`ensure_ssh_keypair`).

**Fix:** Install OpenSSH. On macOS `ssh-keygen` is preinstalled. On Debian/Ubuntu:

```bash
sudo apt-get install openssh-client
```

---

## macOS: vsock unavailable

**Symptom:** You see references to `/dev/vhost-vsock` in logs or module docstrings.

**Action:** Nothing — this is expected and requires no fix. SSH is the default runtime
transport (`enable_vsock=False` in
[`../orchestrator/match_driver.py`](../orchestrator/match_driver.py)).
`orchestrator/vsock_server.py` is a skeleton for future Linux hosts that expose
`/dev/vhost-vsock`; it is not active at runtime on any current platform.

---

## EDK2 firmware not found (aarch64)

**Symptom:** QEMU exits immediately on aarch64 with a firmware-related error such as
`pflash file not found`.

**Cause:** The orchestrator searches for EDK2 code and vars firmware at
(source: [`../orchestrator/_cli_helpers.py`](../orchestrator/_cli_helpers.py)):

```
/opt/homebrew/share/qemu/edk2-aarch64-code.fd   (Homebrew macOS)
/usr/local/share/qemu/edk2-aarch64-code.fd
/usr/share/qemu/edk2-aarch64-code.fd            (Linux)
/usr/share/AAVMF/AAVMF_CODE.fd                  (Debian AAVMF package)
```

**Fix (macOS):** `brew install qemu` places the firmware alongside the QEMU binaries.

**Fix (Debian/Ubuntu):**

```bash
sudo apt-get install qemu-efi-aarch64
```

---

## Cloud-init / PROVISIONING timeout

**Symptom:** The match exits with `result=error cause=provisioning_timeout`, or
`orchestrator.log` shows the state stuck at `PROVISIONING`.

**Cause:** One or more agent harnesses did not send a `pid_announce` frame within the
60-second provisioning window. This almost always means cloud-init did not complete
successfully inside the VM.

**Debug — inspect the VM serial console:**

```bash
cat logs/matches/<match-id>/vm/vm-console.log
```

Look for cloud-init errors (failed package downloads, mounting issues, missing files).
Common causes:
- The golden image was built without network access (package installs failed silently).
- The seed ISO was not mounted — check the QEMU command in `orchestrator.log`.
- The source tarball embedded in cloud-init is corrupted — rebuild the golden image.

---

## Egress proxy blocked a request

**Symptom:** Agent inference fails; `logs/matches/<match-id>/proxy.jsonl` contains
`"event": "deny"` entries for your provider's domain.

**Fix:** Add the blocked domain to `domain_allowlist_extra` in the match JSON:

```json
"domain_allowlist_extra": ["api.myprovider.com"]
```

Or regenerate the match with `arenabench new-match … --allowlist-extra api.myprovider.com`.

See [models & auth](models-and-auth.md) for the full built-in allowlist.

---

## LLM 429 / auth error

**Symptom:** `agents/<slot>/api.jsonl` shows `llm_response` frames with an error, or
agents appear to die from silence shortly after the match starts.

**Cause and fix by sub-case:**

| Sub-case | Fix |
|---|---|
| Missing env var | `arenabench run` raises `ConfigError` with the var name before booting. Check that you exported the key in the same shell session. |
| Rate limit (429) | Increase `num_retries` in the agent config, or reduce `n_agents`. |
| GitHub Copilot token missing | Ensure `~/.config/litellm/github_copilot/access-token` exists and is valid. See [models & auth](models-and-auth.md). |
| Wrong model ID | Verify `model` in your agent config is a valid LiteLLM `provider/model` string. |
| Domain not allowlisted | See "Egress proxy blocked a request" above. |

---

## Capped match rejected before VM boot

**Symptom:** `arenabench run` exits with an unsafe LiteLLM pricing or fallback error,
before credentials, proxy, cloud-init, or QEMU activity.

**Cause:** At least one primary model is unknown, unpriced, malformed,
non-token-priced, or zero-only in LiteLLM's metadata, or the capped agent config has a
nonempty fallback list.

**Fix:** Remove fallbacks for the capped run. For missing/incorrect prices, add the model
metadata to LiteLLM or the LiteLLM fork used by your installation. Do not patch a model
name, provider rule, or price into ArenaBench. Uncapped legacy runs do not perform this
metadata preflight.

---

## Repeated match ID fails on an archive collision

**Symptom:** Starting a previously used match ID raises `FileExistsError` for
`logs/archive/<match-id>/<run-id>/` before VM startup.

**Cause:** The archive destination already exists. ArenaBench fails closed rather than
merging, overwriting, or appending logs. The current directory remains at
`logs/matches/<match-id>/`.

**Fix:** Inspect both directories and resolve the duplicate outside ArenaBench while
preserving owner-only access. Do not combine their JSONL files; each directory is one
execution identified by its `run.json` manifest.

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast

from common.errors import ConfigError
from orchestrator.match_config import AgentEntry

_GUEST_SYSTEM_PROMPT_NAME = "system_prompt.txt"
_REPO_ROOT = Path(__file__).resolve().parent.parent

_EDK2_CODE_CANDIDATES = (
    Path("/opt/homebrew/share/qemu/edk2-aarch64-code.fd"),
    Path("/usr/local/share/qemu/edk2-aarch64-code.fd"),
    Path("/usr/share/qemu/edk2-aarch64-code.fd"),
    Path("/usr/share/AAVMF/AAVMF_CODE.fd"),
)
_EDK2_VARS_CANDIDATES = (
    Path("/opt/homebrew/share/qemu/edk2-arm-vars.fd"),
    Path("/usr/local/share/qemu/edk2-arm-vars.fd"),
    Path("/usr/share/qemu/edk2-arm-vars.fd"),
    Path("/usr/share/AAVMF/AAVMF_VARS.fd"),
)


def load_agent_blobs(
    agents: list[AgentEntry],
) -> tuple[dict[int, str], dict[int, str]]:
    """Return (config_blobs, prompt_blobs) keyed by slot, ready to seed via cloud-init.

    Each config blob's `system_prompt_path` is rewritten to the basename
    `system_prompt.txt` so harness/config.py finds it next to the seeded
    config at /home/<user>/system_prompt.txt inside the guest.
    """
    config_blobs: dict[int, str] = {}
    prompt_blobs: dict[int, str] = {}
    for agent in agents:
        agent_cfg_path = _REPO_ROOT / agent.config
        cfg_json = _parse_agent_config(agent_cfg_path)
        prompt_rel = cfg_json.get("system_prompt_path")
        if not isinstance(prompt_rel, str):
            raise ConfigError(
                "system_prompt_path missing from agent config",
                path=str(agent_cfg_path),
                field="system_prompt_path",
            )
        prompt_blobs[agent.slot] = (_REPO_ROOT / prompt_rel).read_text(encoding="utf-8")
        cfg_json["system_prompt_path"] = _GUEST_SYSTEM_PROMPT_NAME
        config_blobs[agent.slot] = json.dumps(cfg_json, indent=2, sort_keys=True)
    return config_blobs, prompt_blobs


def _parse_agent_config(path: Path) -> dict[str, object]:
    raw_text = path.read_text(encoding="utf-8")
    cfg_raw = cast(object, json.loads(raw_text))
    if not isinstance(cfg_raw, dict):
        raise ConfigError(
            "agent config must be a JSON object",
            path=str(path),
            field="<root>",
        )
    return cast(dict[str, object], cfg_raw)


def detect_edk2_pflash() -> tuple[Path | None, Path | None]:
    code = next((p for p in _EDK2_CODE_CANDIDATES if p.is_file()), None)
    vars_ = next((p for p in _EDK2_VARS_CANDIDATES if p.is_file()), None)
    return code, vars_


def ensure_ssh_keypair(overlay_dir: Path) -> tuple[Path, str]:
    """Generate (or reuse) an ed25519 keypair in overlay_dir; return (private, pubkey).

    The public key string is injected into cloud-init's ssh_authorized_keys for
    root and each agent user. The private key path is passed to
    SshOrchestratorServer / SshTransport for key-based ssh login.
    """
    private = overlay_dir / "ssh_key"
    public = overlay_dir / "ssh_key.pub"
    if private.is_file() and public.is_file():
        return private, public.read_text(encoding="utf-8").strip()
    for path in (private, public):
        if path.is_file():
            path.unlink()
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(private), "-q"],
        check=True,
    )
    private.chmod(0o600)
    return private, public.read_text(encoding="utf-8").strip()


def resolve_agent_env_vars(
    agents: list[AgentEntry],
) -> dict[int, tuple[tuple[str, str], ...]]:
    """Resolve each agent's `api_key_env` to its current value on the host.

    Returns slot → ordered tuple of (var, value) pairs so SshConfig (frozen
    dataclass) can store it. Raises ConfigError if any required env var
    is missing on the host so the orchestrator never starts a match the
    harness cannot complete.

    Agents with `mock_response` or `mock_raise_on_turn` set are skipped —
    they will not call the real LLM, so no api_key is needed.
    """
    out: dict[int, tuple[tuple[str, str], ...]] = {}
    for agent in agents:
        agent_cfg_path = _REPO_ROOT / agent.config
        cfg_json = _parse_agent_config(agent_cfg_path)
        if (
            cfg_json.get("mock_response") is not None
            or cfg_json.get("mock_raise_on_turn") is not None
        ):
            out[agent.slot] = ()
            continue
        api_key_env = cfg_json.get("api_key_env")
        if not isinstance(api_key_env, str):
            raise ConfigError(
                "api_key_env missing from agent config",
                path=str(agent_cfg_path),
                field="api_key_env",
            )
        value = os.environ.get(api_key_env)
        if value is None:
            raise ConfigError(
                f"env var {api_key_env!r} not set for slot {agent.slot}",
                path=str(agent_cfg_path),
                field=api_key_env,
            )
        out[agent.slot] = ((api_key_env, value),)
    return out

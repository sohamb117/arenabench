"""Cloud-init YAML structural tests + seed iso smoke — split from test_cloudinit_render.py."""

import json
import pathlib
import re
import shutil
from typing import cast

import pytest
import yaml

from orchestrator.cloudinit import GuestProxyTarget, render_user_data, write_seed_iso
from orchestrator.match_config import MatchConfig

_HEARTBEAT = 120
_GRACE = 30
_MAX_DURATION = 1800
_ARCHIVE_GRACE = 60
_TEST_SSH_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItestkey arenabench-test"
_EXPECTED_WRITE_FILES_PER_AGENT = 3
_AGENT_COUNT_4 = 4
_AGENT_COUNT_2 = 2
_ROOT_TRAVERSAL_RUNCMDS = ["chmod o+x /root", "chmod -R o+rX /root/.local"]
_ROOT_TRAVERSAL_COUNT = len(_ROOT_TRAVERSAL_RUNCMDS)
_PROXY = GuestProxyTarget(guest_addr="10.0.2.100", port=54321)


def _match_config(*, n_agents: int, network_policy: str = "allowlist") -> MatchConfig:
    payload: dict[str, object] = {
        "match_id": "demo-test",
        "n_agents": n_agents,
        "heartbeat_interval_s": _HEARTBEAT,
        "grace_period_s": _GRACE,
        "max_duration_s": _MAX_DURATION,
        "archive_grace_s": _ARCHIVE_GRACE,
        "network_policy": network_policy,
        "cgroup_limits": None,
        "agents": [
            {"slot": i, "user": f"agent{i}", "config": f"configs/agents/a{i}.json"}
            for i in range(n_agents)
        ],
    }
    return MatchConfig.model_validate(payload)


def _blobs(n: int) -> tuple[dict[int, str], dict[int, str]]:
    config_blobs = {i: json.dumps({"slot": i, "model": "test"}) for i in range(n)}
    prompt_blobs = {i: f"prompt for agent{i}\n" for i in range(n)}
    return config_blobs, prompt_blobs


@pytest.mark.skipif(
    not any(shutil.which(b) for b in ("cloud-localds", "mkisofs", "genisoimage", "hdiutil")),
    reason="no seed-iso builder installed (cloud-localds | mkisofs | genisoimage | hdiutil)",
)
def test_seed_iso_smoke(tmp_path: pathlib.Path) -> None:
    cfg = _match_config(n_agents=2)
    cb, pb = _blobs(2)
    user_data = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )
    out_iso = tmp_path / "seed.iso"

    write_seed_iso(user_data=user_data, out_path=out_iso)

    assert out_iso.exists()
    assert out_iso.stat().st_size > 0


def test_rendered_yaml_parses_and_structure_is_correct() -> None:
    """Round-4 lock: Jinja whitespace must not collapse YAML list items.

    Catches the collapse pattern `":  -"` directly AND parses the rendered
    YAML with PyYAML to ensure cloud-init can ingest it.
    """
    cfg = _match_config(n_agents=_AGENT_COUNT_2)
    cb, pb = _blobs(_AGENT_COUNT_2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert not re.search(r":[ \t]{2,}-[ \t]", out), (
        f"YAML list item collapsed onto map key line:\n{out}"
    )
    assert not re.search(r'"[ \t]+-[ \t]+name:', out), (
        f"sequence item collapsed after quoted scalar:\n{out}"
    )

    parsed = cast(object, yaml.safe_load(out))
    assert isinstance(parsed, dict)
    doc = cast(dict[str, object], parsed)
    users = cast(list[dict[str, object]], doc["users"])
    assert len(users) == _AGENT_COUNT_2 + 1
    assert users[0]["name"] == "root"
    assert [u["name"] for u in users[1:]] == ["agent0", "agent1"]
    for user in users:
        assert user["ssh_authorized_keys"] == [_TEST_SSH_PUBKEY]
    write_files = cast(list[dict[str, object]], doc["write_files"])
    assert len(write_files) == _AGENT_COUNT_2 * _EXPECTED_WRITE_FILES_PER_AGENT
    assert write_files[0]["path"] == "/home/agent0/config.json"
    assert write_files[1]["path"] == "/home/agent0/system_prompt.txt"
    runcmd = cast(list[str], doc["runcmd"])
    assert runcmd == [
        "chmod 0700 /home/agent0",
        "chmod 0700 /home/agent1",
        *_ROOT_TRAVERSAL_RUNCMDS,
    ]
    assert doc["ssh_pwauth"] is False
    assert doc["disable_root"] is False


def test_rendered_yaml_parses_for_four_agents() -> None:
    """Same guard against the collapse pattern at the §3.A7 max N=4 default scale."""
    cfg = _match_config(n_agents=_AGENT_COUNT_4)
    cb, pb = _blobs(_AGENT_COUNT_4)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    parsed = cast(object, yaml.safe_load(out))
    assert isinstance(parsed, dict)
    doc = cast(dict[str, object], parsed)
    users = cast(list[dict[str, object]], doc["users"])
    assert [u["name"] for u in users] == ["root", "agent0", "agent1", "agent2", "agent3"]
    runcmd = cast(list[str], doc["runcmd"])
    assert len(runcmd) == _AGENT_COUNT_4 + _ROOT_TRAVERSAL_COUNT


def test_rendered_yaml_with_network_full_appends_iptables_runcmd() -> None:
    """network_policy=full must add iptables -F line as a SEPARATE runcmd entry."""
    cfg = _match_config(n_agents=_AGENT_COUNT_2, network_policy="full")
    cb, pb = _blobs(_AGENT_COUNT_2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    parsed = cast(object, yaml.safe_load(out))
    assert isinstance(parsed, dict)
    doc = cast(dict[str, object], parsed)
    runcmd = cast(list[str], doc["runcmd"])
    assert runcmd[:2] == ["chmod 0700 /home/agent0", "chmod 0700 /home/agent1"]
    assert _ROOT_TRAVERSAL_RUNCMDS[0] in runcmd
    assert _ROOT_TRAVERSAL_RUNCMDS[1] in runcmd
    assert "iptables -F" in runcmd
    assert "iptables -P INPUT ACCEPT" in runcmd


def test_allowlist_mode_with_proxy_renders_env_and_accept_rule() -> None:
    """Option-B: allowlist + proxy_target injects /etc/profile.d proxy env AND
    a runtime iptables ACCEPT to the proxy address (base-deny added at boot).
    """
    cfg = _match_config(n_agents=_AGENT_COUNT_2, network_policy="allowlist")
    cb, pb = _blobs(_AGENT_COUNT_2)

    out = render_user_data(
        match_config=cfg,
        config_blobs=cb,
        prompt_blobs=pb,
        ssh_pubkey=_TEST_SSH_PUBKEY,
        proxy_target=_PROXY,
    )

    doc = cast(dict[str, object], cast(object, yaml.safe_load(out)))
    write_files = cast(list[dict[str, object]], doc["write_files"])
    by_path = {cast(str, w["path"]): cast(str, w["content"]) for w in write_files}
    assert "/etc/profile.d/arenabench-proxy.sh" in by_path
    content = by_path["/etc/profile.d/arenabench-proxy.sh"]
    assert "HTTPS_PROXY=http://10.0.2.100:54321" in content
    assert "AIOHTTP_TRUST_ENV=True" in content
    runcmd = cast(list[str], doc["runcmd"])
    assert any("10.0.2.100" in c and "54321" in c and "ACCEPT" in c for c in runcmd)


def test_full_mode_omits_proxy_env_even_with_target() -> None:
    cfg = _match_config(n_agents=_AGENT_COUNT_2, network_policy="full")
    cb, pb = _blobs(_AGENT_COUNT_2)

    out = render_user_data(
        match_config=cfg,
        config_blobs=cb,
        prompt_blobs=pb,
        ssh_pubkey=_TEST_SSH_PUBKEY,
        proxy_target=_PROXY,
    )

    doc = cast(dict[str, object], cast(object, yaml.safe_load(out)))
    paths = {cast(str, w["path"]) for w in cast(list[dict[str, object]], doc["write_files"])}
    assert "/etc/profile.d/arenabench-proxy.sh" not in paths
    assert "iptables -F" in cast(list[str], doc["runcmd"])


def test_allowlist_without_proxy_target_omits_env_and_rule() -> None:
    cfg = _match_config(n_agents=_AGENT_COUNT_2, network_policy="allowlist")
    cb, pb = _blobs(_AGENT_COUNT_2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    doc = cast(dict[str, object], cast(object, yaml.safe_load(out)))
    paths = {cast(str, w["path"]) for w in cast(list[dict[str, object]], doc["write_files"])}
    assert "/etc/profile.d/arenabench-proxy.sh" not in paths
    runcmd = cast(list[str], doc["runcmd"])
    assert not any("ACCEPT" in c for c in runcmd)

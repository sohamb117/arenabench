import json
import pathlib
import subprocess
from typing import cast

import pytest
from typer.testing import CliRunner

from orchestrator import match_driver as match_driver_module
from orchestrator._cli_helpers import AgentCredentials
from orchestrator.cli import app
from orchestrator.cloudinit import GuestProxyTarget
from orchestrator.match_config import AgentEntry, MatchConfig

_HEARTBEAT = 120
_GRACE = 30
_MAX_DURATION = 1800
_ARCHIVE_GRACE = 60
_EXIT_CONFIG_ERROR = 1
_EXIT_RUNTIME_ERROR = 3
# Real shipped configs so the referenced-file existence check resolves them.
_REAL_AGENT_CONFIGS = ("configs/agents/claude.json", "configs/agents/gpt.json")


def _valid_match_payload(n_agents: int = 2) -> dict[str, object]:
    return {
        "match_id": "demo-1v1",
        "n_agents": n_agents,
        "heartbeat_interval_s": _HEARTBEAT,
        "grace_period_s": _GRACE,
        "max_duration_s": _MAX_DURATION,
        "archive_grace_s": _ARCHIVE_GRACE,
        "network_policy": "allowlist",
        "cgroup_limits": None,
        "agents": [
            {"slot": i, "user": f"agent{i}", "config": _REAL_AGENT_CONFIGS[i % 2]}
            for i in range(n_agents)
        ],
    }


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_validate_accepts_valid_config(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    path = tmp_path / "match.json"
    path.write_text(json.dumps(_valid_match_payload()), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 0
    assert "OK match_id=demo-1v1" in result.stdout
    assert "n_agents=2" in result.stdout


def test_validate_rejects_bad_config(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    payload = _valid_match_payload()
    payload["n_agents"] = 20
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 1


def test_validate_rejects_negative_heartbeat(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    payload = _valid_match_payload()
    payload["heartbeat_interval_s"] = -1
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 1


def test_validate_missing_file_fails(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "absent.json")])
    assert result.exit_code == 1


def test_replay_pretty_prints_summary(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    match_dir = tmp_path / "match-demo"
    match_dir.mkdir()
    summary = {"result": "victory", "winner": 0, "cause": "opponent_crashed"}
    (match_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    result = runner.invoke(app, ["replay", str(match_dir)])

    assert result.exit_code == 0
    parsed = cast(object, json.loads(result.stdout))
    assert parsed == summary


def test_replay_missing_summary_fails(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    result = runner.invoke(app, ["replay", str(tmp_path)])
    assert result.exit_code == 1


def test_run_command_reports_missing_golden_image(
    runner: CliRunner, tmp_path: pathlib.Path
) -> None:
    path = tmp_path / "match.json"
    path.write_text(json.dumps(_valid_match_payload()), encoding="utf-8")
    missing_golden = tmp_path / "no-such-golden.qcow2"

    result = runner.invoke(
        app, ["run", str(path), "--log-root", str(tmp_path), "--golden-image", str(missing_golden)]
    )

    assert result.exit_code == _EXIT_RUNTIME_ERROR
    assert "golden image missing" in (result.output + (result.stderr or ""))


def test_run_stops_proxy_when_render_user_data_fails(
    runner: CliRunner, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped: list[str] = []

    class FakeProxy:
        port = 12345

        def stop(self) -> None:
            stopped.append("proxy.stop")

    def fake_load_agent_blobs(
        agents: list[AgentEntry],
    ) -> tuple[dict[int, str], dict[int, str]]:
        _ = agents
        return {0: "{}", 1: "{}"}, {0: "p", 1: "p"}

    def fake_resolve_agent_credentials(
        agents: list[AgentEntry],
    ) -> AgentCredentials:
        _ = agents
        return AgentCredentials(env_vars={}, secret_files={})

    def fake_ensure_ssh_keypair(overlay_dir: pathlib.Path) -> tuple[pathlib.Path, str]:
        _ = overlay_dir
        return tmp_path / "ssh_key", "ssh-ed25519 test"

    def fake_build_egress_proxy(
        cfg: MatchConfig, log_dir: pathlib.Path
    ) -> tuple[FakeProxy, GuestProxyTarget]:
        _ = (cfg, log_dir)
        return FakeProxy(), GuestProxyTarget("10.0.2.2", 12345)

    monkeypatch.setattr(match_driver_module, "load_agent_blobs", fake_load_agent_blobs)
    monkeypatch.setattr(
        match_driver_module, "resolve_agent_credentials", fake_resolve_agent_credentials
    )
    monkeypatch.setattr(
        match_driver_module,
        "ensure_ssh_keypair",
        fake_ensure_ssh_keypair,
    )
    monkeypatch.setattr(
        match_driver_module,
        "build_egress_proxy",
        fake_build_egress_proxy,
    )

    def boom(**_: object) -> str:
        raise RuntimeError("render failed")

    monkeypatch.setattr(match_driver_module, "render_user_data", boom)

    path = tmp_path / "match.json"
    path.write_text(json.dumps(_valid_match_payload()), encoding="utf-8")
    golden = tmp_path / "golden.qcow2"
    golden.write_bytes(b"qcow2-placeholder")

    with pytest.raises(RuntimeError, match="render failed"):
        runner.invoke(
            app,
            ["run", str(path), "--log-root", str(tmp_path), "--golden-image", str(golden)],
            catch_exceptions=False,
        )

    assert stopped == ["proxy.stop"]


def test_build_vm_shells_out_to_build_script(
    runner: CliRunner, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.invoke(app, ["build-vm", "--arch", "aarch64"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0][0].endswith("vm/golden/build.sh")


def test_help_text_lists_all_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["validate", "replay", "run", "build-vm"]:
        assert cmd in result.stdout

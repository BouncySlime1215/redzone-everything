"""Service preparation and lifecycle checks; never load a job or contact a device."""
import asyncio
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest

from redzone.config import ConfigError
from scripts import service
from scripts.service_runtime import ServiceConfig


@pytest.fixture
def service_root(tmp_path):
    root = tmp_path / "A & B's <redzone>"
    (root / "redzone").mkdir(parents=True)
    (root / "deploy").mkdir()
    (root / "data").mkdir()
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(sys.executable)
    for name in ("redzone/default_config.yaml", "deploy/com.redzone.everything.plist.template"):
        shutil.copy(service.ROOT / name, root / name)
    return root


def test_plist_paths_escape_and_default_service_has_no_live_opt_in(service_root):
    payload = service.make_plist(service_root, logs=service_root / "logs")
    job = plistlib.loads(payload)
    assert job["ProgramArguments"][:3] == [str(service_root / ".venv/bin/python"), "-u", "-c"]
    assert "WorkingDirectory" not in job          # ~/Documents is closed to launchd (TCC): EX_CONFIG 78
    assert job["StandardOutPath"] == job["StandardErrorPath"] == str(service_root / "logs/service-bootstrap.log")
    assert b"__LOGS__" not in payload and b"__LAUNCHER__" not in payload
    assert job["KeepAlive"] is True
    assert job["ThrottleInterval"] == 15
    assert job["Umask"] == 0o077
    assert b"__ROOT__" not in payload
    live = plistlib.loads(service.make_plist(service_root, allow_live=True, port="8778"))
    assert live["ProgramArguments"][-1] == "--allow-live"
    assert live["EnvironmentVariables"]["PORT"] == "8778"
    assert "SMARTTHINGS_TOKEN" not in live["EnvironmentVariables"]
    with pytest.raises(ValueError):
        service.make_plist(service_root, port="bad")


@pytest.mark.parametrize("text", ["mode: [", "- list", "mode:\n  dry_run: nope\n", "made_up: true"])
def test_preflight_rejects_invalid_saved_config_without_fallback(service_root, text):
    path = service_root / "data/config.yaml"
    path.write_text(text)
    result = service.check(service_root, check_port=False)
    assert any("Invalid data/config.yaml" in item for item in result.errors)
    assert path.read_text() == text


def test_readiness_does_not_connect_or_modify_files(service_root, monkeypatch, capsys):
    path = service_root / "data/config.yaml"
    path.write_text("mode:\n  dry_run: false\n")
    (service_root / "data/devices.json").write_text(json.dumps({"sticks": [{"host": "example.invalid:5555"}] * 2}))
    (service_root / ".env").write_text("SMARTTHINGS_TOKEN=secret-test-value\n")
    before = {p.relative_to(service_root): p.read_bytes() for p in service_root.rglob("*")
              if p.is_file() and not p.is_symlink()}

    def no_commands(*args, **kwargs):
        raise AssertionError("preflight must not launch/connect to devices")

    monkeypatch.setattr(service.subprocess, "run", no_commands)
    result = service.check(service_root, check_port=False)
    result.show()
    assert not result.errors
    assert "enforced dry-run" in capsys.readouterr().out
    assert not any("secret-test-value" in item for item in result.details + result.warnings + result.errors)
    assert before == {p.relative_to(service_root): p.read_bytes() for p in service_root.rglob("*")
                      if p.is_file() and not p.is_symlink()}


def test_safe_service_pins_saved_live_mode_and_rollback_without_rewriting_on_load(tmp_path):
    path = tmp_path / "config.yaml"
    text = "mode:\n  dry_run: false\n"
    path.write_text(text)
    cfg = ServiceConfig(user_path=path, history_dir=tmp_path / "history")
    assert cfg.get("mode.dry_run") is True and cfg.data["mode"]["dry_run"] is True
    assert path.read_text() == text
    with pytest.raises(ConfigError, match="enforces dry-run"):
        cfg.update({"mode": {"dry_run": False}})
    cfg.update({"server": {"port": 8779}}, record=False)
    cfg.history_dir.mkdir()
    (cfg.history_dir / "20261101-120000-001.yaml").write_text(text + "server:\n  port: 8778\n")
    (cfg.history_dir / "20261101-120001-001.yaml").write_text(text + "server:\n  port: 8779\n")
    assert cfg.rollback()
    assert cfg.get("server.port") == 8778
    assert cfg.get("mode.dry_run") is True
    assert ServiceConfig(user_path=path, history_dir=tmp_path / "history", allow_live=True).get("mode.dry_run") is False


def test_service_config_bad_mode_still_uses_config_validation(tmp_path):
    cfg = ServiceConfig(user_path=tmp_path / "config", history_dir=tmp_path / "history")
    with pytest.raises(ConfigError):
        cfg.update({"mode": "invalid"})


def test_default_command_only_checks(monkeypatch):
    monkeypatch.setattr(service, "check", lambda **kw: service.Readiness())
    monkeypatch.setattr(service, "install", lambda *a: pytest.fail("default must not install"))
    monkeypatch.setattr(service, "launchctl", lambda *a, **kw: pytest.fail("default must not change launchd"))
    assert service.main([]) == 0


def test_port_listener_blocks_installation(service_root, monkeypatch):
    original = service.shutil.which
    monkeypatch.setattr(service.shutil, "which", lambda name, **kw: "/usr/sbin/lsof" if name == "lsof" else original(name, **kw))
    calls = []

    def occupied(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "123\n", "")

    monkeypatch.setattr(service.subprocess, "run", occupied)
    assert any("already has a listener" in x for x in service.check(service_root).errors)
    assert len(calls) == 1 and calls[0][0] == "lsof"


def test_existing_service_is_not_overwritten(service_root, monkeypatch):
    path = service_root / "installed.plist"
    path.write_bytes(b"existing definition")
    monkeypatch.setattr(service, "launchctl", lambda *a, **kw: pytest.fail("must not touch existing job"))
    with pytest.raises(RuntimeError, match="already exists"):
        service.install(service.make_plist(service_root), path, "gui/501")
    assert path.read_bytes() == b"existing definition"


def test_unknown_job_status_does_not_delete_definition(tmp_path, monkeypatch):
    path = tmp_path / "installed.plist"
    path.write_bytes(b"existing definition")
    monkeypatch.setattr(service, "installed_path", lambda: path)
    monkeypatch.setattr(service, "launchctl", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "permission denied"))
    with pytest.raises(RuntimeError, match="determine launchd"):
        service.main(["remove"])
    assert path.exists()


def test_status_does_not_print_launchd_environment(monkeypatch, capsys):
    status = "state = running\npid = 123\nenvironment = {\nSMARTTHINGS_TOKEN => secret-test-value\n}\n"
    monkeypatch.setattr(service, "launchctl", lambda *a, **kw: subprocess.CompletedProcess(a, 0, status, ""))
    assert service.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "state = running" in output and "pid = 123" in output
    assert "secret-test-value" not in output and "SMARTTHINGS_TOKEN" not in output


def test_failed_bootstrap_keeps_definition_for_outcome_inspection(service_root, monkeypatch):
    path = service_root / "installed.plist"
    monkeypatch.setattr(service, "ROOT", service_root)
    monkeypatch.setattr(service, "job_loaded", lambda domain: False)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(5, ["launchctl", *args])

    monkeypatch.setattr(service, "launchctl", fail)
    with pytest.raises(subprocess.CalledProcessError):
        service.install(service.make_plist(service_root), path, "gui/501")
    assert plistlib.loads(path.read_bytes())["Label"] == service.LABEL


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["web", "producer"])
async def test_unexpected_clean_exit_is_failure_and_closes_engine(which):
    from redzone.__main__ import run_services

    class Server:
        should_exit = False

        async def serve(self):
            if which == "web":
                return
            while not self.should_exit:
                await asyncio.sleep(0)

    class Engine:
        closed = False

        async def run(self):
            if which == "producer":
                return
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    server, engine = Server(), Engine()
    with pytest.raises(RuntimeError, match="stopped unexpectedly"):
        await run_services(server, engine, asyncio.Event())
    assert server.should_exit and engine.closed


@pytest.mark.asyncio
async def test_requested_shutdown_closes_cleanly():
    from redzone.__main__ import run_services
    stop = asyncio.Event()

    class Server:
        should_exit = False

        async def serve(self):
            stop.set()
            while not self.should_exit:
                await asyncio.sleep(0)

    class Engine:
        closed = False

        async def run(self):
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    engine = Engine()
    await run_services(Server(), engine, stop)
    assert engine.closed


def test_launcher_changes_into_the_checkout_and_passes_the_live_flag(tmp_path):
    """The -c launcher runs scripts/service_runtime.py as __main__ from inside the checkout, with
    --allow-live (appended by make_plist) still in sys.argv, even for a path with spaces and quotes."""
    root = tmp_path / "Codex's dir" / "redzone"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/service_runtime.py").write_text(
        "import os, sys, json\nprint(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv, 'name': __name__}))\n")
    job = plistlib.loads(service.make_plist(_template_root(root), allow_live=True, logs=tmp_path / "logs"))
    args = [sys.executable, *job["ProgramArguments"][1:]]
    out = subprocess.run(args, cwd="/", capture_output=True, text=True, timeout=30, check=True).stdout
    got = json.loads(out)
    assert got["cwd"] == str(root) and got["name"] == "__main__"
    assert got["argv"] == [str(root / "scripts/service_runtime.py"), "--allow-live"]


def _template_root(root):
    (root / "deploy").mkdir(exist_ok=True)
    shutil.copy(service.ROOT / "deploy/com.redzone.everything.plist.template", root / "deploy")
    return root

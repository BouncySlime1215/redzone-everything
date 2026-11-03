"""Prepare/manage the per-user launchd job; check/render never contact devices."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LABEL = "com.redzone.everything"
SERVICE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


@dataclass
class Readiness:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def show(self) -> None:
        for kind, rows in (("OK", self.details), ("WARN", self.warnings), ("ERROR", self.errors)):
            for row in rows:
                print(f"{kind}: {row}")


def strict_config(root: Path = ROOT) -> dict:
    """Service starts must not silently discard invalid saved settings."""
    import yaml
    from redzone.config import _deep_merge, validate, without_retired

    defaults = yaml.safe_load((root / "redzone/default_config.yaml").read_text())
    path = root / "data/config.yaml"
    overrides = yaml.safe_load(path.read_text()) if path.exists() else {}
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValueError("saved configuration must be a mapping")
    overrides = without_retired(overrides)
    validate(overrides, defaults)
    return _deep_merge(defaults, overrides)


def credential_present(root: Path, name: str) -> bool:
    # launchd does not inherit a Terminal's exports. Match the app's .env reader.
    path = root / ".env"
    if not path.exists():
        return False
    return any(line.startswith(name + "=") and line.split("=", 1)[1].strip()
               for line in path.read_text().splitlines())


def check(root: Path = ROOT, allow_live: bool = False, check_port: bool = True) -> Readiness:
    result = Readiness()
    python = root / ".venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        result.errors.append("Missing executable .venv/bin/python; create the project venv and install requirements.txt.")
    if sys.version_info < (3, 12):
        result.errors.append("Python 3.12 or newer is required.")
    for name in ("yaml", "uvicorn", "fastapi", "httpx", "numpy", "websockets", "samsungtvws"):
        if importlib.util.find_spec(name) is None:
            result.errors.append(f"Missing Python module {name}; use this project's venv and requirements.txt.")
    if result.errors:
        return result
    try:
        cfg = strict_config(root)
    except Exception as exc:
        # YAML/parser errors can include saved values: do not log their text.
        result.errors.append(f"Invalid data/config.yaml ({type(exc).__name__}); fix it before starting the service.")
        return result
    result.details.append("Saved configuration validates; it has not been changed.")
    saved_live = not cfg.get("mode", {}).get("dry_run", True)
    result.details.append("Service mode: " + ("saved mode (live control permitted)" if allow_live else "enforced dry-run"))
    if saved_live and not allow_live:
        result.warnings.append("Saved live mode will be overridden in memory by the dry-run service.")
    if cfg.get("screen", {}).get("mic_calibration"):
        result.warnings.append("Microphone calibration is configured; macOS permission and unattended audio need validation.")
    try:
        port = int(os.environ.get("PORT") or cfg.get("server", {}).get("port", 8777))
        if not 1024 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        result.errors.append("The dashboard port must be an integer from 1024 through 65535.")
        return result
    result.details.append(f"Dashboard port: {port}. Feed connectivity and device readiness are not checked here.")
    if check_port and shutil.which("lsof"):
        probe = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                               capture_output=True, text=True, timeout=5)
        if probe.returncode == 0 and probe.stdout.strip():
            result.errors.append(f"Port {port} already has a listener; stop the existing app before installation.")
    data = root / "data"
    if not os.access(data if data.exists() else root, os.W_OK):
        result.errors.append("The project data directory is not writable.")
    try:
        path = data / "devices.json"
        devices = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(devices, dict) or not isinstance(devices.get("sticks", []), list):
            raise ValueError
        sticks = devices.get("sticks", [])
        if any(not isinstance(s, dict) or not isinstance(s.get("host"), str) or not s["host"] for s in sticks):
            raise ValueError
        tv = devices.get("tv") or {}
        if not isinstance(tv, dict):
            raise ValueError
    except (ValueError, OSError):
        result.errors.append("Invalid data/devices.json; repair selected-device records before starting.")
        return result
    result.details.append(f"Saved stick records: {len(sticks)} (not connected or verified).")
    if not sticks:
        (result.errors if allow_live and saved_live else result.warnings).append("No Fire TV stick selected.")
    if not shutil.which("adb", path=SERVICE_PATH):
        (result.errors if allow_live and saved_live else result.warnings).append("adb is missing from the service PATH.")
    selected = tv.get("smartthings_device_id") or tv.get("st_device")
    if cfg.get("two_stick", {}).get("enabled") and len(sticks) >= 2:
        if not selected:
            result.warnings.append("Two-stick switching has no selected SmartThings input-readback device.")
        if not credential_present(root, "SMARTTHINGS_TOKEN"):
            result.warnings.append("SMARTTHINGS_TOKEN is absent from .env; input readback will be unavailable.")
    if cfg.get("fantasy", {}).get("league_id"):
        if not all(credential_present(root, k) for k in ("ESPN_S2", "ESPN_SWID")):
            result.warnings.append("Fantasy is configured without both .env cookies; private leagues need them.")
    result.warnings.append("Provider sign-ins, subscriptions, token expiry, channel identity and screen timing remain device checks.")
    result.warnings.append("The Mac must stay powered on and logged in; launchd cannot operate while it sleeps.")
    return result


def make_plist(root: Path = ROOT, allow_live: bool = False, port: str | None = None) -> bytes:
    template = plistlib.loads((root / "deploy/com.redzone.everything.plist.template").read_bytes())

    def expand(value):
        if isinstance(value, str):
            return value.replace("__ROOT__", str(root))
        if isinstance(value, list):
            return [expand(v) for v in value]
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        return value

    config = expand(template)
    if allow_live:
        config["ProgramArguments"].append("--allow-live")
    if port is not None:
        if not port.isdecimal() or not 1024 <= int(port) <= 65535:
            raise ValueError("PORT must be an integer from 1024 through 65535")
        config["EnvironmentVariables"]["PORT"] = port
    return plistlib.dumps(config, sort_keys=False)


def installed_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def launchctl(*args: str, checked: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=20, check=checked)


def job_loaded(domain: str) -> bool:
    status = launchctl("print", f"{domain}/{LABEL}", checked=False)
    if status.returncode == 0:
        return True
    if f'Could not find service "{LABEL}"' in status.stderr:
        return False
    raise RuntimeError("Could not determine launchd job state; no installation/removal performed.")


def install(payload: bytes, path: Path, domain: str) -> None:
    # Never silently replace a running job or another checkout's installation.
    if path.exists() or job_loaded(domain):
        raise RuntimeError("A service already exists; inspect status and explicitly remove it before installing again.")
    path.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{LABEL}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    try:
        launchctl("bootstrap", domain, str(path))
    except Exception:
        # Failed bootstrap can still register a job. Keep the definition and
        # require outcome inspection before any retry/removal.
        print(f"Bootstrap did not confirm success. Definition retained at {path}; run status before retrying.",
              file=sys.stderr)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="check", choices=("check", "render", "install", "status", "remove"))
    parser.add_argument("--allow-live", action="store_true", help="permit saved live mode; only after physical validation")
    args = parser.parse_args(argv)
    path = installed_path()
    domain = f"gui/{os.getuid()}"
    if args.action == "render":
        sys.stdout.buffer.write(make_plist(allow_live=args.allow_live, port=os.environ.get("PORT")))
        return 0
    if args.action == "status":
        print(f"Definition: {path} ({'present' if path.exists() else 'absent'})")
        status = launchctl("print", f"{domain}/{LABEL}", checked=False)
        if status.returncode:
            if f'Could not find service "{LABEL}"' in status.stderr:
                print("Job is not loaded in this login session; a foreground app may still be running.")
            else:
                print("Job state could not be determined; launchctl reported an error.")
        else:
            # launchctl's full dump can contain inherited environment secrets.
            for line in status.stdout.splitlines():
                if any(line.strip().startswith(key + " =") for key in
                       ("state", "pid", "runs", "last exit code", "program", "path")):
                    print(line.strip())
            print("Process state does not establish source freshness or device readiness.")
        return 0 if status.returncode == 0 else 1
    if args.action == "remove":
        if job_loaded(domain):
            launchctl("bootout", f"{domain}/{LABEL}")
        if job_loaded(domain):
            raise RuntimeError("Service is still loaded; definition was not deleted.")
        path.unlink(missing_ok=True)
        print("Service unloaded and login definition removed. Runtime data retained.")
        return 0
    result = check(allow_live=args.allow_live)
    result.show()
    if result.errors:
        return 1
    if args.action == "check":
        print("Read-only check complete; nothing installed or started.")
        return 0
    install(make_plist(allow_live=args.allow_live, port=os.environ.get("PORT")), path, domain)
    print("Service registered for this login and future logins; launchd will attempt to start it now.")
    print("Confirm process/source health with status, data/service.log and the dashboard.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Service action failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

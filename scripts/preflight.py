"""Preflight: a read-only go/no-go for the Mac before a TV session.

    python scripts/preflight.py            # table, exit 0 = GO, 1 = NO-GO
    python scripts/preflight.py --json     # the same, machine-readable

Why: WP-10 / major-considerations step 7: the Mac (8 GB, on battery, 11 GB swap, apps competing) cost
nights before the first game started, and nobody was told until the morning. This says so before the
first game: AC power, swap, free memory, pressure, heat, disk, the app's own memory, the app version
(git commit), whether TV control is on, which build-plan flags are on, and what to quit.

Read-only. It runs the same few read commands the governor does (pmset, sysctl, vm_stat, ps, footprint,
git rev-parse / status) and reads two YAML files for named flag keys. It never presses a key, tunes,
flips an input, starts or stops anything, touches adb, the TV or the network, and writes nothing. It does
not read or print secrets or stick addresses: it opens no device file, prints only the whitelisted flag
keys as on/off, and lists other processes by command NAME only (never their arguments).

A check is ok, warn (go, but look) or fail (no-go). Any fail means NO-GO.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import governor as gv  # noqa: E402

BASE_BRANCH = "codex/resume-redzone"          # what the Mac runs (CLAUDE.md)
SWAP_WARN_MB = 6000.0                          # an 8 GB Mac's normal evening is 3-6 GB (mac_health)
SWAP_FAIL_MB = 8000.0                          # ...8+ is where it fell over
AVAIL_WARN_MB = 1000.0
AVAIL_FAIL_MB = 500.0
FOOTPRINT_STALE_S = 900.0                      # the log writes every 5 minutes
QUIT_MIN_MB = 300.0                            # a process under this is not worth quitting
FLAGS = (                                      # dotted key -> shown as on/off. Nothing else is printed.
    "mode.dry_run", "queue.enabled", "queue.r3_flippable", "commit_gate.enabled", "plan.sweep",
    "plan.verify", "discovery.st_wait", "yttv_seek.enabled", "yttv_seek.skip_open_read",
    "tuners.prime_fast_confirm", "tuners.espn_link_form", "hardening.sticks", "hardening.feeds",
    "hardening.mac", "hardening.tv", "hardening.smartthings", "supervisor.mac_health.enabled",
)

Run = Callable[..., "str | None"]


def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    """One read-only command; None when it is missing, slow or fails (same contract as governor._run)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def parse_vm_stat(text: str | None) -> float | None:
    """`vm_stat` -> MB the Mac can hand out without swapping: free + inactive + speculative pages."""
    if not text:
        return None
    m = re.search(r"page size of (\d+) bytes", text)
    size = int(m.group(1)) if m else 16384
    pages = 0
    seen = False
    for key in ("Pages free", "Pages inactive", "Pages speculative"):
        m = re.search(rf"{key}:\s+(\d+)", text)
        if m:
            pages += int(m.group(1))
            seen = True
    return round(pages * size / 1e6) if seen else None


def big_processes(text: str | None, own: tuple[str, ...] = ("python", "Python")) -> list[tuple[str, float]]:
    """`ps -axo rss=,comm=` -> [(command name, MB)], biggest first, over QUIT_MIN_MB. Names only: the
    full command line (which can hold an address or a key) is never asked for."""
    got: dict[str, float] = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*(\d+)\s+(.+)$", line)
        if not m:
            continue
        name = Path(m.group(2).strip()).name or m.group(2).strip()
        mb = int(m.group(1)) / 1024
        if name in own:
            continue
        got[name] = got.get(name, 0.0) + mb
    rows = sorted(((n, round(mb)) for n, mb in got.items() if mb >= QUIT_MIN_MB), key=lambda r: -r[1])
    return rows


def _flatten(d: dict, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def read_flags(root: Path) -> dict[str, bool | None]:
    """The whitelisted flags: default_config.yaml, then data/config.yaml over it. None = not set or
    unreadable. Only these keys are ever looked at."""
    try:
        import yaml
    except ImportError:
        return dict.fromkeys(FLAGS)
    flat: dict = {}
    for path in (root / "redzone" / "default_config.yaml", root / "data" / "config.yaml"):
        try:
            d = yaml.safe_load(path.read_text()) or {}
            if isinstance(d, dict):
                flat.update(_flatten(d))
        except Exception:
            continue
    return {k: (flat[k] if isinstance(flat.get(k), bool) else None) for k in FLAGS}


def _row(name: str, status: str, detail: str, fix: str = "") -> dict:
    return {"check": name, "status": status, "detail": detail, "fix": fix}


def checks(s: gv.Sample, *, avail_mb: float | None, version: dict, flags: dict, quit_list: list,
           footprint_age_s: float | None, opts: dict) -> list[dict]:
    """Pure: every check from one sample. Unknown (None) is a warn, never a pass."""
    rows: list[dict] = []
    if s.on_battery is None:
        rows.append(_row("power", "warn", "could not read the power source", "plug in the charger"))
    elif s.on_battery:
        rows.append(_row("power", "fail", f"on battery ({s.battery_pct}%)" if s.battery_pct is not None
                         else "on battery", "plug in the charger; macOS sleeps on battery with the lid shut"))
    else:
        rows.append(_row("power", "ok", "on AC"))
    if s.swap_used_mb is None:
        rows.append(_row("swap", "warn", "could not read swap"))
    elif s.swap_used_mb >= SWAP_FAIL_MB:
        rows.append(_row("swap", "fail", f"{s.swap_used_mb / 1000:.1f} GB swapped out",
                         "restart the Mac or quit the big apps below"))
    elif s.swap_used_mb >= SWAP_WARN_MB:
        rows.append(_row("swap", "warn", f"{s.swap_used_mb / 1000:.1f} GB swapped out", "quit the big apps below"))
    else:
        rows.append(_row("swap", "ok", f"{s.swap_used_mb / 1000:.1f} GB swapped out"))
    if avail_mb is None:
        rows.append(_row("free memory", "warn", "could not read memory"))
    elif avail_mb < AVAIL_FAIL_MB:
        rows.append(_row("free memory", "fail", f"{avail_mb:.0f} MB free", "quit the big apps below"))
    elif avail_mb < AVAIL_WARN_MB:
        rows.append(_row("free memory", "warn", f"{avail_mb:.0f} MB free", "quit the big apps below"))
    else:
        rows.append(_row("free memory", "ok", f"{avail_mb:.0f} MB free"))
    p = s.mem_pressure
    rows.append(_row("memory pressure", "warn", "could not read it") if p is None else
                _row("memory pressure", "fail", "critical", "quit the big apps below") if p >= 4 else
                _row("memory pressure", "warn", "tight", "quit the big apps below") if p >= 2 else
                _row("memory pressure", "ok", "normal"))
    if s.disk_free_mb is None:
        rows.append(_row("disk", "warn", "could not read free disk"))
    elif s.disk_free_mb < opts["disk_critical_mb"]:
        rows.append(_row("disk", "fail", f"{s.disk_free_mb / 1000:.1f} GB free", "free some space (scripts/disk_guard.py)"))
    elif s.disk_free_mb < opts["disk_low_mb"]:
        rows.append(_row("disk", "warn", f"{s.disk_free_mb / 1000:.1f} GB free", "free some space"))
    else:
        rows.append(_row("disk", "ok", f"{s.disk_free_mb / 1000:.1f} GB free"))
    if (s.cpu_speed_limit is not None and s.cpu_speed_limit < 70) or (s.thermal_level or 0) >= 2:
        rows.append(_row("heat", "fail", "the Mac is throttled or hot", "let it cool; move it off soft surfaces"))
    elif s.thermal_level:
        rows.append(_row("heat", "warn", "the Mac is running warm"))
    else:
        rows.append(_row("heat", "ok", "no heat warning"))
    if s.low_power:
        rows.append(_row("low power mode", "warn", "on", "turn Low Power Mode off"))
    if s.load_per_core is not None and s.load_per_core > opts["busy_load"]:
        rows.append(_row("cpu", "warn", f"busy ({s.load_per_core:.2f} load per core)", "quit the big apps below"))
    svc = s.procs.get("service")
    fp = s.footprint.get("service")
    mb = max((v for v in (svc, fp) if v is not None), default=None)
    if mb is None:
        rows.append(_row("app", "fail", "the RedZone app is not running", "start it before the game"))
    elif mb > opts["service_hard_mb"]:
        rows.append(_row("app", "fail", f"running, {mb:.0f} MB (over {opts['service_hard_mb']:.0f})", "restart the app"))
    elif mb > opts["service_soft_mb"]:
        rows.append(_row("app", "warn", f"running, {mb:.0f} MB (over {opts['service_soft_mb']:.0f})"))
    else:
        rows.append(_row("app", "ok", f"running, {mb:.0f} MB"))
    ver = version.get("commit")
    if not ver:
        rows.append(_row("app version", "warn", "could not read the git commit"))
    else:
        note = [f"commit {ver}", f"branch {version.get('branch') or '?'}"]
        if version.get("dirty"):
            note.append("uncommitted changes")
        off_base = version.get("branch") not in (BASE_BRANCH, None)
        rows.append(_row("app version", "warn" if off_base or version.get("dirty") else "ok", ", ".join(note),
                         f"the Mac runs {BASE_BRANCH}" if off_base else ""))
    if flags.get("mode.dry_run") is not False:
        rows.append(_row("tv control", "fail", "dry run is on (or unset): the app will not touch the TV",
                         "turn dry run off on the phone before the game"))
    else:
        rows.append(_row("tv control", "ok", "live (dry run off)"))
    on = sorted(k for k, v in flags.items() if v is True and k != "mode.dry_run")
    rows.append(_row("flags on", "ok", ", ".join(on) if on else "none (all defaults)"))
    if footprint_age_s is None:
        rows.append(_row("footprint log", "warn", "no data/footprint.jsonl yet (written every 5 minutes while the app runs)"))
    elif footprint_age_s > FOOTPRINT_STALE_S:
        rows.append(_row("footprint log", "warn", f"last line {footprint_age_s / 60:.0f} min old"))
    else:
        rows.append(_row("footprint log", "ok", f"last line {footprint_age_s / 60:.0f} min old"))
    if quit_list:
        rows.append(_row("quit these", "warn" if any(r["status"] != "ok" for r in rows if r["check"] in (
            "swap", "free memory", "memory pressure")) else "ok",
            ", ".join(f"{n} {m} MB" for n, m in quit_list[:5])))
    return rows


def footprint_age(root: Path, now: float) -> float | None:
    """Seconds since the last footprint line (None = no log). Reads one number from the last line only."""
    try:
        last = (root / "data" / "footprint.jsonl").read_text().strip().splitlines()[-1]
        return max(0.0, now - float(json.loads(last)["at"]))
    except Exception:
        return None


def git_version(run: Run, root: Path) -> dict:
    out: dict = {}
    sha = (run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"]) or "").strip()
    br = (run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"]) or "").strip()
    st = run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"])
    if sha:
        out["commit"] = sha
    if br:
        out["branch"] = None if br == "HEAD" else br
    out["dirty"] = bool((st or "").strip())
    return out


def preflight(root: Path = ROOT, run: Run = _run, clock: Callable[[], float] = time.time) -> dict:
    """Run every check. `run` is injectable so tests never touch the real Mac."""
    opts = gv.load_options(root)
    s = gv.sample(root, run=run, clock=clock)
    rows = checks(s, avail_mb=parse_vm_stat(run(["vm_stat"])), version=git_version(run, root),
                  flags=read_flags(root), quit_list=big_processes(run(["ps", "-axo", "rss=,comm="])),
                  footprint_age_s=footprint_age(root, clock()), opts=opts)
    bad = [r["check"] for r in rows if r["status"] == "fail"]
    return {"go": not bad, "failed": bad, "warned": [r["check"] for r in rows if r["status"] == "warn"],
            "checks": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only go/no-go for the Mac before a TV session.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = preflight()
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print("GO" if res["go"] else "NO-GO: " + ", ".join(res["failed"]))
        for r in res["checks"]:
            tag = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}[r["status"]]
            print(f"  {tag} {r['check']:16} {r['detail']}" + (f"  -> {r['fix']}" if r["fix"] and r["status"] != "ok" else ""))
    return 0 if res["go"] else 1


if __name__ == "__main__":
    sys.exit(main())

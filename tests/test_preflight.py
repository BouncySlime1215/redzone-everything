"""Preflight (scripts/preflight.py) and the governor's footprint log (WP-10).

Why: the preflight must say NO-GO for a Mac that will cost the night, and must never act: no key, no tune,
no state change, no secret or stick address in its output. The footprint log must write one line per 5 min.
"""
import importlib.util
import json
import sys
from pathlib import Path

from redzone import governor as gv

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("preflight", ROOT / "scripts" / "preflight.py")
pf = importlib.util.module_from_spec(spec)
sys.modules["preflight"] = pf
spec.loader.exec_module(pf)

READ_ONLY = {"pmset", "sysctl", "ps", "footprint", "vm_stat", "git"}
VM_OK = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 100000.\n"
         "Pages inactive: 50000.\nPages speculative: 10000.\n")
VM_LOW = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 1000.\nPages inactive: 500.\n"
PS = ("  101 1 /Users/n/redzone/.venv/bin/python -m redzone\n  900000 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"
      "  50 /usr/bin/ssh -i /Users/n/.ssh/key secret-host\n")


def fake(batt="Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t95%; charged; 0:00 remaining present: true",
         swap="total = 4096.00M  used = 1000.00M  free = 3096.00M  (encrypted)", pressure="1", vm=VM_OK,
         ps="  101 300000 /usr/bin/python -m redzone\n", seen=None):
    def run(cmd, timeout=5.0):
        if seen is not None:
            seen.append(list(cmd))
        c = cmd[0]
        if c == "pmset":
            return batt if cmd[-1] == "batt" else ""
        if c == "sysctl":
            return pressure if "pressure" in cmd[-1] else swap
        if c == "vm_stat":
            return vm
        if c == "ps":
            return ps if "pid=" in cmd[-1] else PS
        if c == "git":
            return "codex/resume-redzone\n" if "--abbrev-ref" in cmd else "abc1234\n" if "--short" in cmd else ""
        return None
    return run


def _live_root(tmp_path):
    (tmp_path / "redzone").mkdir(exist_ok=True)
    (tmp_path / "redzone" / "default_config.yaml").write_text("mode:\n  dry_run: false\n")
    return tmp_path


def test_a_healthy_mac_is_go(tmp_path):
    r = pf.preflight(_live_root(tmp_path), run=fake())
    assert r["go"] and r["failed"] == []
    ver = next(c for c in r["checks"] if c["check"] == "app version")
    assert "abc1234" in ver["detail"] and ver["status"] == "ok"


def test_on_battery_is_no_go():
    s = gv.Sample(on_battery=True, battery_pct=40)
    rows = pf.checks(s, avail_mb=4000, version={}, flags={"mode.dry_run": False}, quit_list=[],
                     footprint_age_s=10, opts=gv.options())
    assert {r["check"]: r["status"] for r in rows}["power"] == "fail"


def test_swap_and_memory_thresholds():
    def status(swap, avail, pressure):
        s = gv.Sample(on_battery=False, swap_used_mb=swap, mem_pressure=pressure)
        rows = pf.checks(s, avail_mb=avail, version={}, flags={"mode.dry_run": False}, quit_list=[],
                         footprint_age_s=10, opts=gv.options())
        return {r["check"]: r["status"] for r in rows}
    assert status(2000, 3000, 1)["swap"] == "ok"
    assert status(7000, 3000, 1)["swap"] == "warn"
    assert status(10000, 3000, 1)["swap"] == "fail"
    assert status(2000, 400, 1)["free memory"] == "fail"
    assert status(2000, 800, 1)["free memory"] == "warn"
    assert status(2000, 3000, 4)["memory pressure"] == "fail"
    assert status(None, None, None)["swap"] == "warn"      # unknown is never a pass


def test_dry_run_and_stopped_app_are_no_go(tmp_path):
    (tmp_path / "redzone").mkdir()
    (tmp_path / "redzone" / "default_config.yaml").write_text("mode:\n  dry_run: true\nqueue:\n  enabled: true\n")
    r = pf.preflight(tmp_path, run=fake(ps=""))
    assert not r["go"] and {"tv control", "app"} <= set(r["failed"])
    flags = next(c for c in r["checks"] if c["check"] == "flags on")
    assert "queue.enabled" in flags["detail"]


def test_it_only_runs_read_commands_and_never_touches_a_stick(tmp_path):
    seen: list = []
    pf.preflight(tmp_path, run=fake(seen=seen))
    assert seen and {c[0] for c in seen} <= READ_ONLY
    joined = " ".join(" ".join(c) for c in seen)
    for bad in ("adb", "keyevent", "am start", "input ", "ssh", "curl", "kill", "launchctl"):
        assert bad not in joined
    git = [c for c in seen if c[0] == "git"]
    assert git and all(c[3] in ("rev-parse", "status") for c in git)


def test_source_imports_nothing_that_can_reach_a_device_or_the_network():
    import ast
    tree = ast.parse((ROOT / "scripts" / "preflight.py").read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
    assert mods <= {"__future__", "argparse", "json", "re", "subprocess", "sys", "time", "pathlib", "typing",
                    "redzone", "yaml"}, mods
    src = (ROOT / "scripts" / "preflight.py").read_text()
    assert "write_text" not in src and "open(" not in src


def test_no_secret_or_stick_address_is_printed(tmp_path):
    root = _live_root(tmp_path)
    (root / "data").mkdir()
    (root / "data" / "config.yaml").write_text(
        "sticks: [192.168.7.77:5555]\nsmartthings:\n  token: SECRET-TOKEN-123\nmode:\n  dry_run: false\n")
    (root / "data" / "devices.json").write_text('{"a": "192.168.7.77"}')
    out = json.dumps(pf.preflight(root, run=fake(ps=PS)))
    assert "SECRET-TOKEN-123" not in out and "192.168.7.77" not in out
    assert "secret-host" not in out and "/.ssh/" not in out
    assert "Google Chrome" in out          # a process NAME is shown; its arguments never are


def test_big_processes_lists_names_only_and_skips_small_ones():
    rows = pf.big_processes(PS)
    assert rows == [("Google Chrome", 879)] or rows[0][0] == "Google Chrome"
    assert all("/" not in n and "secret" not in n for n, _ in rows)


def test_parse_vm_stat():
    assert pf.parse_vm_stat(VM_OK) == round(160000 * 16384 / 1e6)
    assert pf.parse_vm_stat("") is None and pf.parse_vm_stat("nonsense") is None


# ---- footprint log ---------------------------------------------------------------------------

def _gov(tmp_path, clock, **kw):
    s = gv.Sample(at=clock(), procs={"service": 400.0}, footprint={"service": 650.0}, swap_used_mb=1200.0,
                  mem_pressure=1, disk_free_mb=9000, on_battery=False)
    return gv.Governor({}, probe=lambda: s, clock=clock, footprint_log=tmp_path / "data" / "footprint.jsonl", **kw)


def test_footprint_is_logged_every_five_minutes_not_every_look(tmp_path):
    t = [1000.0]
    g = _gov(tmp_path, lambda: t[0])
    for _ in range(11):          # a look every 30 s for 5 minutes
        g.refresh(True)
        t[0] += 30
    rows = [json.loads(x) for x in (tmp_path / "data" / "footprint.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and rows[0]["service_footprint_mb"] == 650.0 and rows[0]["service_mb"] == 650.0
    assert rows[0]["level"] == "normal" and rows[0]["swap_used_mb"] == 1200.0


def test_no_path_no_log_and_a_bad_path_never_raises(tmp_path):
    g = gv.Governor({}, probe=lambda: gv.Sample(at=1.0))
    g.refresh(True)
    assert g.footprint_log is None and g.log_footprint(force=True) is False
    blocker = tmp_path / "file"
    blocker.write_text("x")
    g2 = gv.Governor({}, probe=lambda: gv.Sample(at=1.0), footprint_log=blocker / "sub" / "f.jsonl")
    g2.refresh(True)             # parent is a file: the write fails and is swallowed
    assert g2.level == "normal"


def test_log_is_trimmed_past_its_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(gv, "FOOTPRINT_LOG_MAX_BYTES", 400)
    t = [1000.0]
    g = _gov(tmp_path, lambda: t[0])
    for _ in range(12):
        g.refresh(True)
        t[0] += 400
    size = (tmp_path / "data" / "footprint.jsonl").stat().st_size
    assert size < 400 + 2 * 400


def test_from_config_logs_to_data_unless_turned_off(tmp_path):
    class Cfg:
        def __init__(self, v):
            self.v = v

        def get(self, k, d=None):
            return self.v if k == "governor.footprint_log" else d
    assert gv.Governor.from_config(Cfg(True), root=tmp_path).footprint_log == tmp_path / "data" / "footprint.jsonl"
    assert gv.Governor.from_config(Cfg(False), root=tmp_path).footprint_log is None


def test_governor_defaults_are_unchanged():
    assert gv.DEFAULTS["every_s"] == 30 and gv.DEFAULTS["service_hard_mb"] == 1200 and "footprint_log" not in gv.DEFAULTS

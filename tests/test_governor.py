"""Resource governor (redzone/governor.py), its watchdog hooks and the disk guard script."""
from __future__ import annotations

import os
import time

import pytest

from agent import watchdog
from agent.policy import Obs
from redzone import governor as gv
from redzone.governor import Governor, Sample, assess, options
from scripts import disk_guard


BATT = """Now drawing from 'Battery Power'
 -InternalBattery-0 (id=1234)\t15%; discharging; 1:10 remaining present: true
"""
AC = """Now drawing from 'AC Power'
 -InternalBattery-0 (id=1234)\t100%; charged; 0:00 remaining present: true
"""
PS = """  101 512000 /Users/n/redzone/.venv/bin/python -u /Users/n/redzone/scripts/service_runtime.py
  102 1048576 /Users/n/redzone/.venv-agent/bin/python -u -m agent.llm_server
  103 204800 /Users/n/redzone/.venv-agent/bin/python -u -m agent.watchdog --no-model
  104 999999 /bin/zsh -c grep scripts/service_runtime.py
  105 409600 /usr/bin/python3 -m redzone.demo 20261101
"""


def test_parsers_read_real_pmset_sysctl_and_ps_output():
    assert gv.parse_batt(BATT) == (True, 15)
    assert gv.parse_batt(AC) == (False, 100)
    assert gv.parse_batt(None) == (None, None)
    assert gv.parse_lowpower(" sleep 1\n lowpowermode         1\n") is True
    assert gv.parse_lowpower(" sleep 1\n") is None
    assert gv.parse_therm("Note: No thermal warning level has been recorded\n") == (0, None)
    assert gv.parse_therm("Thermal warning level set to 2.\nCPU_Speed_Limit \t= 60\n") == (2, 60)
    assert gv.parse_swap("total = 2048.00M  used = 1536.50M  free = 511.50M  (encrypted)") == 1536.5
    assert gv.parse_swap("total = 4.00G  used = 2.00G  free = 2.00G") == 2048.0
    procs = gv.parse_ps(PS)
    # the shell line isn't python, and `-m redzone.demo` isn't the service
    assert procs == {"service": 500.0, "llm_server": 1024.0, "watchdog": 200.0}


def test_sample_never_raises_when_commands_are_missing(tmp_path):
    s = gv.sample(tmp_path, run=lambda cmd: None, clock=lambda: 5.0)
    assert s.at == 5.0 and s.on_battery is None and s.procs == {}
    assess(s, options())


@pytest.mark.parametrize("s, level", [
    (Sample(disk_free_mb=10000, on_battery=False, mem_pressure=1, load_per_core=0.2), "normal"),
    (Sample(on_battery=True, battery_pct=80), "eco"),
    (Sample(on_battery=True, battery_pct=10), "critical"),
    (Sample(low_power=True), "eco"),
    (Sample(load_per_core=1.5), "eco"),
    (Sample(mem_pressure=2), "eco"),
    (Sample(mem_pressure=4), "critical"),
    (Sample(swap_used_mb=3000), "eco"),
    (Sample(thermal_level=1), "eco"),
    (Sample(thermal_level=3), "critical"),
    (Sample(cpu_speed_limit=50), "critical"),
    (Sample(disk_free_mb=2500), "eco"),
    (Sample(disk_free_mb=900), "critical"),
])
def test_assess_levels(s, level):
    got, reasons = assess(s, options())
    assert got == level
    assert bool(reasons) == (level != "normal")


@pytest.mark.playbook("MAC-02", "MAC-03", "MAC-04")
@pytest.mark.parametrize("s, why", [
    (Sample(thermal_level=1), "hot"),
    (Sample(on_battery=True, battery_pct=80), "battery"),
    (Sample(mem_pressure=2), "memory"),
])
def test_hot_on_battery_or_short_of_memory_drops_the_extras_never_the_switching(s, why):
    """MAC-02 hot / MAC-03 on battery / MAC-04 short of memory: reels, the lab and the archive stop,
    OCR and idle polling slow down. Decisions, tunes and hot polling ask nothing of the governor."""
    g = Governor(probe=lambda: s, clock=lambda: 1000.0)
    g.refresh(force=True)
    assert g.level == "eco" and why in g.why().lower(), g.why()
    assert not any(g.allow(k) for k in ("reel", "lab", "archive"))
    assert g.allow("ocr") and g.scale("ocr") == 2.0 and g.scale("idle_poll") == 2.0
    assert g.allow("decide") and g.scale("hot_poll") == 1.0    # unknown kinds = live switching: never slowed


def test_battery_eco_can_be_turned_off():
    assert assess(Sample(on_battery=True, battery_pct=80), options({"battery_eco": False}))[0] == "normal"


def test_options_ignore_bad_values_and_keep_thresholds_in_order():
    o = options({"disk_low_mb": "lots", "battery_eco": 1, "busy_load": -2, "nope": 3,
                 "disk_critical_mb": 5000, "service_soft_mb": 2000})
    assert o["disk_low_mb"] == 3000 and o["battery_eco"] is True and o["busy_load"] == 0.85
    assert "nope" not in o
    assert o["disk_critical_mb"] == 3000          # never above disk_low_mb
    assert o["service_soft_mb"] == 1200           # never above service_hard_mb
    assert options("garbage")["every_s"] == 30


def test_level_goes_up_at_once_and_down_only_after_calm():
    t = [0.0]
    g = Governor({"calm_s": 100}, clock=lambda: t[0], probe=lambda: Sample())
    assert g.update(Sample(on_battery=True, battery_pct=50)) == "eco"
    assert not g.allow("reel") and not g.allow("lab") and not g.allow("archive")
    assert g.allow("llm") and g.allow("ocr") and g.allow("anything_else")
    assert g.scale("ocr") == 2.0 and g.scale("idle_poll") == 2.0 and g.scale("unknown") == 1.0
    assert g.update(Sample(mem_pressure=4)) == "critical"
    assert not g.allow("llm") and not g.allow("ocr")
    t[0] = 10
    assert g.update(Sample()) == "critical"      # clear, but not for long enough
    t[0] = 60
    assert g.update(Sample(on_battery=True, battery_pct=50)) == "critical"
    t[0] = 111
    assert g.update(Sample(on_battery=True, battery_pct=50)) == "eco"    # 101 s calm below critical
    t[0] = 112
    assert g.update(Sample(mem_pressure=4)) == "critical"                  # and straight back up
    t[0] = 120
    assert g.update(Sample()) == "critical"
    t[0] = 125
    assert g.update(Sample(mem_pressure=4)) == "critical"                  # a relapse restarts the calm clock
    t[0] = 200
    assert g.update(Sample()) == "critical"
    t[0] = 300
    assert g.update(Sample()) == "normal"
    assert [c[1] for c in g.changes] == ["eco", "critical", "eco", "critical", "normal"]


def test_disabled_governor_allows_everything():
    g = Governor({"enabled": False})
    g.update(Sample(mem_pressure=4, disk_free_mb=1))
    assert g.level == "normal" and g.allow("reel") and g.scale("ocr") == 1.0


def test_refresh_is_rate_limited_and_survives_a_broken_probe():
    t = [0.0]
    calls = []

    def probe():
        calls.append(t[0])
        if len(calls) == 2:
            raise RuntimeError("pmset exploded")
        return Sample(on_battery=True, battery_pct=50)
    g = Governor({"every_s": 30}, clock=lambda: t[0], probe=probe)
    assert g.refresh() == "eco"
    t[0] = 10
    assert g.refresh() == "eco" and len(calls) == 1
    t[0] = 40
    assert g.refresh() == "eco" and len(calls) == 2       # the probe raised: level kept
    t[0] = 80
    g.refresh()
    assert len(calls) == 3


def test_settings_changed_on_the_phone_apply_at_the_next_look():
    class Cfg:
        data = {"governor": {"battery_eco": True}}

        def get(self, key, default=None):
            return self.data.get(key, default)
    cfg = Cfg()
    t = [0.0]
    g = Governor.from_config(cfg, clock=lambda: t[0], probe=lambda: Sample(on_battery=True, battery_pct=90))
    assert g.refresh() == "eco"
    cfg.data["governor"] = {"battery_eco": False, "calm_s": 0}
    t[0] = 100
    assert g.refresh() == "normal"


def test_view_is_json_ready():
    import json
    g = Governor()
    g.update(Sample(procs={"service": 300.0, "watchdog": 100.0, "llm_server": 900.0}))
    v = g.view()
    json.dumps(v)
    assert v["level"] == "normal" and v["service_mb"] == 300.0 and v["models_mb"] == 1000.0


def test_background_cmd_wraps_heavy_children(monkeypatch):
    monkeypatch.setattr(gv.sys, "platform", "darwin")
    monkeypatch.setattr(gv.os.path, "exists", lambda p: p == gv.TASKPOLICY)
    assert gv.background_cmd(["ffmpeg", "-i", "x"]) == [gv.TASKPOLICY, "-b", "ffmpeg", "-i", "x"]
    monkeypatch.setattr(gv.sys, "platform", "linux")
    out = gv.background_cmd(["ffmpeg"])
    assert out[-1] == "ffmpeg" and (out == ["ffmpeg"] or out[:3] == ["nice", "-n", "10"])


def test_mlx_limits_are_a_no_op_without_mlx():
    assert gv.apply_mlx_limits(1500) in (True, False)
    gv.free_mlx_cache()


def test_load_options_reads_saved_settings(tmp_path):
    (tmp_path / "redzone").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "redzone" / "default_config.yaml").write_text("governor:\n  models_mb: 1500\n")
    (tmp_path / "data" / "config.yaml").write_text("governor:\n  models_mb: 1200\n  every_s: nope\n")
    o = gv.load_options(tmp_path)
    assert o["models_mb"] == 1200 and o["every_s"] == 30
    (tmp_path / "data" / "config.yaml").write_text(": : broken yaml [")
    assert gv.load_options(tmp_path)["models_mb"] == 1500


def test_default_config_governor_section_matches_code_defaults():
    from redzone.config import Config, ConfigError
    import yaml
    from pathlib import Path
    d = yaml.safe_load((Path(gv.__file__).with_name("default_config.yaml")).read_text())["governor"]
    assert d == gv.DEFAULTS
    from redzone.config import validate
    with pytest.raises(ConfigError):
        validate({"governor": {"models_mb": 50}}, {"governor": d})
    with pytest.raises(ConfigError):
        validate({"governor": {"busy_load": 9}}, {"governor": d})
    validate({"governor": {"busy_load": 1.2, "models_mb": 1200}}, {"governor": d})
    assert Config  # imported for the validation path


# ---- watchdog ------------------------------------------------------------------------------

class FixedGov(Governor):
    def __init__(self, s: Sample, **opts):
        super().__init__(opts, probe=lambda: s)
        self.update(s)

    def refresh(self, force=False):
        return self.level


def _wd(s: Sample, clock=lambda: 1000.0, **opts):
    return watchdog.Watchdog(dry_run=True, use_model=False, clock=clock, governor=FixedGov(s, **opts))


def test_app_over_its_memory_cap_is_restarted_after_three_checks_but_never_in_crunch():
    w = _wd(Sample(procs={"service": 1500.0}))
    o = Obs()
    assert w.decide(o).action == "notify"          # 1st and 2nd look: only a warning
    assert w.decide(o).action == "notify"
    v = w.decide(o)
    assert v.action == "restart_service" and "1500 MB" in v.why and v.source == "governor"
    w.mem_streak = 2
    v = w.decide(Obs(crunch_on_screen=True))
    assert v.action == "notify" and "crunch" in v.why


def test_memory_streak_resets_when_the_app_shrinks():
    s = Sample(procs={"service": 1500.0})
    w = _wd(s)
    w.decide(Obs())
    w.decide(Obs())
    s.procs["service"] = 300.0
    assert w.decide(Obs()).action == "none" and w.mem_streak == 0


def test_hard_rules_still_come_first():
    w = _wd(Sample(procs={"service": 1500.0}))
    v = w.decide(Obs(disk_free_mb=500))
    assert v.action == "prune_disk"


@pytest.mark.playbook("MAC-04")
def test_models_over_budget_drop_the_watchdogs_own_model():
    w = _wd(Sample(procs={"watchdog": 1700.0}))
    w._model = object()
    assert w.decide(Obs()).action == "none" and w._model is None
    v = w.decide(Obs())                            # nothing of its own left to drop: tell a person
    assert v.action == "notify" and "model server" in v.why


@pytest.mark.playbook("MAC-02", "MAC-03", "MAC-04")
def test_no_model_while_the_mac_is_critical(monkeypatch):
    w = watchdog.Watchdog(dry_run=True, use_model=True, clock=lambda: 1000.0,
                          governor=FixedGov(Sample(mem_pressure=4)))
    monkeypatch.setattr(w, "model", lambda: pytest.fail("model must not load"))
    v = w.decide(Obs(errors={"ERROR something brand new happened": 1}))
    assert v.action == "notify" and v.why.startswith("new error")


def test_idle_model_is_dropped(monkeypatch):
    t = [1000.0]
    w = _wd(Sample(), clock=lambda: t[0])
    monkeypatch.setattr(watchdog, "fetch_state", lambda timeout=6.0: None)
    w._model, w._model_used = object(), 1000.0
    t[0] += watchdog.MODEL_IDLE_S + 1
    row = w.tick()
    assert w._model is None and row["obs"]["governor"] == "normal"


# ---- disk guard ----------------------------------------------------------------------------

def _tree(root, name, mb, age_s):
    d = root / name
    d.mkdir()
    f = d / "archive.tgz"
    f.write_bytes(b"x" * int(mb * 1e6))
    old = time.time() - age_s
    os.utime(f, (old, old))
    os.utime(d, (old, old))
    return d


def test_disk_report_lists_transfer_folders_and_their_contents(tmp_path):
    _tree(tmp_path, ".rz_tmp", 2, 10 * 3600)
    (tmp_path / "data" / "archive").mkdir(parents=True)
    r = disk_guard.report(tmp_path)
    assert r["transfer_mb"] == 2.0 and r["transfers"][0]["path"] == ".rz_tmp"
    assert r["transfers"][0]["contents"][0]["name"] == "archive.tgz"
    assert any(w["path"] == "data/archive" for w in r["watched"])
    disk_guard.show(r)


def test_clean_needs_yes_skips_fresh_folders_and_refuses_anything_else(tmp_path):
    old = _tree(tmp_path, ".rz_tmp", 1, 10 * 3600)
    fresh = _tree(tmp_path, ".xfer", 1, 60)
    (tmp_path / "data").mkdir()
    lines = disk_guard.clean([".rz_tmp", ".xfer", "data", ".claude-xfer"], root=tmp_path)
    assert "would delete" in lines[0] and old.exists()
    assert "skipped" in lines[1]
    assert "refusing" in lines[2]
    assert "not there" in lines[3]
    lines = disk_guard.clean([".rz_tmp", ".xfer"], root=tmp_path, yes=True)
    assert "deleted" in lines[0] and not old.exists()
    assert fresh.exists() and (tmp_path / "data").exists()


def test_clean_removes_a_link_never_its_target(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    link = tmp_path / ".claude-xfer"
    link.symlink_to(target)
    old = time.time() - 10 * 3600
    os.utime(link, (old, old), follow_symlinks=False)
    lines = disk_guard.clean([".claude-xfer"], root=tmp_path, yes=True)
    assert "deleted" in lines[0] and not link.exists() and (target / "keep.txt").exists()


# ---- engine + autonomy ---------------------------------------------------------------------

def _engine(tmp_path):
    from redzone.config import Config
    from redzone.engine import Engine
    from redzone.store import Store
    return Engine(Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h"), Store(tmp_path / "db.sqlite"))


def test_engine_holds_a_governor_and_shows_it_on_the_phone(tmp_path):
    import json
    e = _engine(tmp_path)
    e.governor.update(Sample(on_battery=True, battery_pct=50))
    h = e.snapshot()["health"]["governor"]
    json.dumps(h)
    assert h["level"] == "eco" and "reel" in h["paused"] and "on battery" in h["why"]


def test_no_reel_work_while_the_mac_needs_a_break(tmp_path, monkeypatch):
    e = _engine(tmp_path)
    monkeypatch.setattr(e.reeler, "enabled", lambda: True)
    called = []
    monkeypatch.setattr(e.reeler, "ingest", lambda *a, **k: called.append(1))
    e.governor.update(Sample(disk_free_mb=900))
    e._reel_tick()
    assert called == []


def test_heavy_jobs_wait_for_the_governor(tmp_path):
    import asyncio
    e = _engine(tmp_path)
    auto = e.discovery.autonomy
    e.governor.update(Sample(disk_free_mb=2000))
    why = asyncio.run(auto._archive_guard())
    assert why and "needs a break" in why and "GB free" in why
    assert asyncio.run(auto._lab_guard())
    e.governor.opts["calm_s"] = 0
    e.governor.update(Sample(disk_free_mb=50000))
    assert asyncio.run(auto._archive_guard()) is None
    assert asyncio.run(auto._lab_guard()) is None

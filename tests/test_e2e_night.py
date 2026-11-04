"""End-to-end replayed night: the real Engine + Controller + standby + reel + supervisor + watchdog
against fake Fire TV sticks and a fake TV, through a whole replayed night (8+ hours of virtual time,
about 3 minutes of real time), with the faults the user listed (stick drops, a wedged adb link, a
"Still watching?" prompt, a feed outage, a TV that ignores a flip, a hung web server).

The night runs in its own process (`python -m redzone.lab.night`): the virtual clock has to replace
time.time before anything in redzone is imported. Linux-safe: fakes only, the committed fixtures in
tests/fixtures, and a temporary work folder (the repo's data/ is never touched).
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CHECKS = ["no_crash", "no_stuck_state", "safe_mode_clears", "two_stick_recovers", "zero_midplay_cuts",
          "still_watching_cleared", "no_center_in_player", "wedged_stick_reconnected",
          "feed_outage_handled", "watchdog_fixes_what_it_should", "tv_shows_games"]


def _fixtures_present() -> bool:
    from redzone.lab.night import run as night_run   # importing run.py needs no virtual clock
    return all((night_run.FIX / f"{g.fixture}.json").exists() for g in night_run.GAMES)


def _data_snapshot() -> dict:
    d = ROOT / "data"
    if not d.exists():
        return {}
    return {str(p.relative_to(d)): p.stat().st_mtime_ns for p in d.rglob("*") if p.is_file()}


@pytest.fixture(scope="module")
def night(tmp_path_factory):
    if not _fixtures_present():
        pytest.skip("the night's ESPN fixtures are not in tests/fixtures")
    work = tmp_path_factory.mktemp("night")
    out = work / "night.json"
    before = _data_snapshot()
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("RZ_NIGHT_TRACE", None)
    env["RZ_NIGHT_HOSTILE_HOST"] = "1"   # another subnet, a tunnel, a busy Mac: the night must not see any of it
    proc = subprocess.run([sys.executable, "-m", "redzone.lab.night", "--json", str(out), "--work", str(work / "w")],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert out.exists(), f"the night wrote no report (exit {proc.returncode}):\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    report = json.loads(out.read_text())
    report["_exit"] = proc.returncode
    report["_data_untouched"] = _data_snapshot() == before
    return report


@pytest.mark.parametrize("name", CHECKS)
def test_night_check(night, name):
    checks = {c["name"]: c for c in night["checks"]}
    assert name in checks, f"the night did not grade {name}"
    assert checks[name]["ok"], json.dumps(checks[name]["detail"], indent=1, default=str)[:4000]


def test_night_covers_the_whole_night(night):
    assert night["hours"] >= 7.5
    assert night["cuts"] >= 20, "too few cuts: the night barely switched"
    names = {f["name"] for f in night["faults"]}
    assert {"stick_drop", "stick_wedge", "still_watching", "feed_outage", "flip_ignored", "api_down"} <= names
    assert night["_exit"] == (0 if night["ok"] else 1)


def test_night_leaves_repo_data_alone(night):
    assert night["_data_untouched"], "the night wrote into the repo's data/"


# ---- fast unit checks on the harness itself (no night run) -----------------------------------

def test_virtual_clock_keeps_tiny_steps():
    """The hang this harness once had: at t ~ 1.8e9 a step of ~5e-8 s vanished in the float, so
    virtual time never moved. The clock keeps its offset apart from t0."""
    from redzone.lab.night.vclock import VirtualClock
    c = VirtualClock(1_793_566_434.0)
    for _ in range(1000):
        c.advance(4.7e-8)
    assert c.time() > 1_793_566_434.0
    assert c.monotonic() > VirtualClock.MONO0
    c.skew = 30.0
    assert c.time() - c.t >= 30.0 and c.monotonic() < VirtualClock.MONO0 + 1


def test_fault_specs_and_plug_hook(monkeypatch):
    import types
    from redzone.lab.night import faults
    f = faults.from_spec({"name": "stick_drop", "at": 3600, "dur": 60, "host": "hidden"})
    assert isinstance(f, faults.StickDrop) and (f.at, f.dur, f.host) == (3600.0, 60.0, "hidden")
    f = faults.from_spec({"name": "clock_skew", "at": 10, "seconds": 45})
    assert f.info == {"seconds": 45}
    with pytest.raises(KeyError):
        faults.from_spec({"name": "no_such_fault", "at": 1})
    # The platform's fault module plugs in as module:function(world, rng) -> faults.
    mod = types.ModuleType("rz_test_faults")
    mod.faults = lambda world, rng: [faults.FeedOutage("feed_outage", rng.uniform(1, 2), 30.0)]
    monkeypatch.setitem(sys.modules, "rz_test_faults", mod)
    got = faults.plug("rz_test_faults:faults", object(), random.Random(1))
    assert len(got) == 1 and isinstance(got[0], faults.FeedOutage)
    assert len(faults.plug("rz_test_faults", object(), random.Random(1))) == 1   # function defaults to `faults`


def test_default_faults_land_while_games_are_live():
    from redzone.lab.night import faults
    windows = [(1000.0, 12_000.0), (15_000.0, 28_000.0)]
    for seed in range(20):
        for f in faults.default(random.Random(seed), windows):
            assert any(lo + 300 <= f.at <= hi - 600 for lo, hi in windows), (seed, f.describe())


def test_the_night_never_reads_the_host_it_runs_on(monkeypatch):
    """On Nick's Mac the night's watchdog saw the live RedZone app (over its 1200 MB cap) in the
    real `ps` and kept restarting the night's own engine; two-stick mode was off at the end.
    pin_host makes every governor in the night see a calm machine with no RedZone processes."""
    from redzone import governor as G
    from redzone.lab.night import run as night_run
    monkeypatch.setattr(G, "sample", lambda *a, **k: G.Sample(procs={"service": 1500.0}, on_battery=True,
                                                               battery_pct=5, load_per_core=14.0))
    assert G.Governor().update(G.sample()) == "critical"     # what the Mac looked like mid-deploy
    night_run.pin_host(G)
    gov = G.Governor()
    s = G.sample()
    assert gov.update(s) == "normal"
    assert s.procs == {} and gov.service_mb() is None


def test_two_stick_off_at_the_end_is_held_to_the_same_420_s():
    from redzone.lab.night import run as night_run

    def sample(t, two):
        return night_run.Sample(t, 0.0, False, "idle", False, two, True, False, None, 0, 2, ("connected",) * 2)
    on_at_end = [sample(t, t < 100 or t > 300) for t in range(0, 1000, 5)]
    assert night_run._still_off_at_end(on_at_end, lambda x: not x.two_stick) == 0.0
    late = [sample(t, t < 900) for t in range(0, 1000, 5)]
    assert night_run._still_off_at_end(late, lambda x: not x.two_stick) == 95.0      # young gap: fine
    stuck = [sample(t, t < 400) for t in range(0, 1000, 5)]
    assert night_run._still_off_at_end(stuck, lambda x: not x.two_stick) > 420       # fails the check


def test_pin_network_says_home_whatever_the_host_is():
    """pin_network makes the night's network check say home even on a host whose LAN isn't 10.0.0.x
    (Nick's Mac: 100.110.165.x plus a Tailscale tunnel), so the supervisor keeps reconnecting sticks."""
    import asyncio
    from redzone import network
    from redzone.lab.night import run as night_run
    real = network.assess
    try:
        night_run.pin_network(network)
        w = network.NetWatch(Path("/nonexistent/devices.json"))
        st = asyncio.run(w.refresh(force=True))
        assert st.where == "home" and not st.away and not st.routes_broken
    finally:
        network.assess = real


def test_a_hostile_host_says_away_until_the_night_pins_it(tmp_path):
    """The CI night runs on hostile_host(): another subnet with a tunnel taking 10.x. Unpinned, the
    network check says away (the supervisor would skip every stick reconnect, as on Nick's Mac), and
    the governor is critical with a 1500 MB live app; pinned, home and normal."""
    import asyncio
    import json as _json
    from redzone import governor, network
    from redzone.lab.night import run as night_run
    devices = tmp_path / "devices.json"
    devices.write_text(_json.dumps({"sticks": [{"host": h} for h in night_run.HOSTS]}))
    saved = network.Probe, network.assess, governor.sample
    try:
        night_run.hostile_host()
        st = asyncio.run(network.NetWatch(devices).refresh(force=True))
        assert st.away and st.tailscale
        assert governor.Governor().update(governor.sample()) == "critical"
        night_run.pin_network(network)
        night_run.pin_host(governor)
        st = asyncio.run(network.NetWatch(devices).refresh(force=True))
        assert st.where == "home" and not st.routes_broken
        gov = governor.Governor()
        assert gov.update(governor.sample()) == "normal" and gov.service_mb() is None
    finally:
        network.Probe, network.assess, governor.sample = saved

"""T1 (Nick 9:50 AM Nov 5): smoke + unit tests for the time-shift buffer SHADOW path and its stay/leave log.

Tester-only file (T1 tester). Builds on the test writers' #318 (scripts/buffer_shadow_grade.py, S1/S2/R1) and adds
what #318 does not check:

  C1  the 90 s cap holds: whatever by_league asks for, the controller never holds more than timing.behind_live_s.max_s,
      and no shadow row says behind_s > 90.
  C2  shadow never touches the screen: `shadow: true` with `enabled: false` sends no pause/play key and records no
      hold, at any B (also B over the cap).
  C3  the adaptive buffer (Nick 9:35 AM: feed-lag p95 + sync error + tune/seek + margin) is never below the hard
      floor (10 s, Nick's "~10-15 s") and never above the cap, and its parts add up.
  C4  every stay/leave decision is logged with its game context (score/time, WP trajectory, leverage, runs, timeouts,
      stakes) and the predicted value of staying vs the best alternative (Nick 9:45 AM).
  C5  Rule #1: no logged LEAVE at a high-leverage moment (redzone/lab/leverage.high_leverage is the floor), and the
      shadow's entries pass #318's R1 when that grader is on the branch.

The validators below are unit-tested on synthetic rows and always run. The night-lab tests run lane 2's shadow once
`redzone/buffer_shadow.py` is on the branch and skip until then. Field names follow lane 2's log (FIELDS below is the
single place to change if lane 2 renames one).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from redzone.lab.leverage import high_leverage
from redzone.models import League

ROOT = Path(__file__).resolve().parents[1]
CAP_S = 90.0          # Nick 9:10 AM: 60-90 s behind live, cap 90
FLOOR_S = 10.0        # Nick 9:35 AM: never 0, hard floor ~10-15 s (the low end is the hard line)
TOL_S = 0.51          # rounding in the log

# ---- lane 2's row names (the one place to edit if they differ) ----------------------------------------------------
FIELDS = {
    "stay_leave_kinds": {"stay_leave"},            # rows that are a stay/leave call
    "stay_leave_actions": {"stay", "leave"},
    "context": "context",                           # the game-context dict on a stay/leave row
    "context_keys": ("score", "clock", "period", "wp", "wp_traj", "leverage", "runs", "timeouts", "stakes"),
    "value_stay": "value_stay",
    "value_alt": "value_alt",
    "alt": "alt",                                   # the best alternative game id (null when none)
    "high_lev": "high_leverage",                    # bool, in context
    "adaptive": "behind_adaptive_s",
    "adaptive_parts": ("feed_lag_p95_s", "sync_err_s", "tune_s", "margin_s"),
}


def stay_leave_problems(r: dict) -> list[str]:
    """C4 + C5 problems with one stay/leave row (empty = fine)."""
    bad = []
    if r.get("action") not in FIELDS["stay_leave_actions"]:
        bad.append(f"action {r.get('action')!r} not stay/leave")
    for k in ("league", "gid"):
        if not r.get(k):
            bad.append(f"{k} missing")
    ctx = r.get(FIELDS["context"])
    if not isinstance(ctx, dict):
        return bad + ["no context dict"]
    for k in FIELDS["context_keys"]:
        if k not in ctx:
            bad.append(f"context.{k} missing")
    for k in (FIELDS["value_stay"], FIELDS["value_alt"]):
        v = r.get(k)
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool)):
            bad.append(f"{k} not a number or null")
        if k not in r:
            bad.append(f"{k} missing (null is fine)")
    if FIELDS["alt"] not in r:
        bad.append("alt missing (null is fine)")
    if r.get("action") == "leave" and _is_high_leverage(r):
        bad.append("RULE1: leave at a high-leverage moment")
    return bad


def _is_high_leverage(r: dict) -> bool:
    """The row's own flag OR the rule floor recomputed from its context (either one says high = high)."""
    ctx = r.get(FIELDS["context"]) or {}
    if ctx.get(FIELDS["high_lev"]) is True:
        return True
    try:
        lg = League(str(r.get("league")).lower())
    except ValueError:
        return False
    sc = ctx.get("score") or {}
    p = {"period": {"number": ctx.get("period")}, "clock": {"displayValue": ctx.get("clock")},
         "homeScore": sc.get("home") if isinstance(sc, dict) else None,
         "awayScore": sc.get("away") if isinstance(sc, dict) else None}
    try:
        return high_leverage(lg, p)
    except Exception:
        return False


def adaptive_problems(r: dict) -> list[str]:
    """C3 problems with the adaptive buffer value on one row (empty = fine, also when the row doesn't carry it)."""
    k = FIELDS["adaptive"]
    if k not in r or r[k] is None:
        return []
    v = r[k]
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return [f"{k} not a number"]
    bad = []
    if v < FLOOR_S - 1e-9:
        bad.append(f"{k} {v} below the {FLOOR_S:.0f} s floor")
    if v > CAP_S + 1e-9:
        bad.append(f"{k} {v} above the {CAP_S:.0f} s cap")
    parts = [r.get(p) for p in FIELDS["adaptive_parts"]]
    if all(isinstance(p, (int, float)) for p in parts):
        raw = sum(parts)
        want = min(CAP_S, max(FLOOR_S, raw))
        if abs(want - v) > TOL_S:
            bad.append(f"{k} {v} != clamp(sum of parts {raw:.1f}, {FLOOR_S:.0f}, {CAP_S:.0f}) = {want:.1f}")
    return bad


def cap_problems(r: dict) -> list[str]:
    b = r.get("behind_s")
    if isinstance(b, (int, float)) and not isinstance(b, bool) and b > CAP_S + 1e-9:
        return [f"behind_s {b} above the {CAP_S:.0f} s cap"]
    return []


def log_problems(rows: list[dict]) -> list[str]:
    out = []
    for i, r in enumerate(rows):
        probs = cap_problems(r) + adaptive_problems(r)
        if r.get("kind") in FIELDS["stay_leave_kinds"]:
            probs += stay_leave_problems(r)
        out += [f"row {i}: {p}" for p in probs]
    return out


# ---- validator unit tests (always run) -----------------------------------------------------------------------------

def sl(action="stay", **kw):
    r = {"ts": 1.0e9, "mode": "shadow", "kind": "stay_leave", "league": "nba", "gid": "401", "stick": None,
         "role": None, "behind_s": 90.0, "source": "default", "action": action, "target": None, "reason": "x",
         "alt": "402", "value_stay": 0.4, "value_alt": 0.6,
         "context": {"score": {"home": 50, "away": 40}, "clock": "8:00", "period": 2, "wp": 0.7,
                     "wp_traj": [0.6, 0.65, 0.7], "leverage": 0.2, "high_leverage": False, "runs": {"home": 8},
                     "timeouts": {"home": 3, "away": 4}, "stakes": 0.1}}
    ctx = kw.pop("context", {})
    r["context"].update(ctx)
    r.update(kw)
    return r


def test_a_full_stay_leave_row_passes():
    assert stay_leave_problems(sl()) == []
    assert stay_leave_problems(sl("leave")) == []
    assert stay_leave_problems(sl(alt=None, value_alt=None)) == []      # nothing else on: null is fine


def test_every_context_field_is_required():
    for k in FIELDS["context_keys"]:
        r = sl()
        del r["context"][k]
        assert any(f"context.{k}" in p for p in stay_leave_problems(r)), k
    for k in ("value_stay", "value_alt", "alt"):
        r = sl()
        del r[k]
        assert stay_leave_problems(r), k
    r = sl()
    del r["context"]
    assert stay_leave_problems(r) == ["no context dict"]
    assert stay_leave_problems(sl(action="switch"))


def test_leave_at_high_leverage_is_a_rule1_fail():
    # the row's own flag
    assert any("RULE1" in p for p in stay_leave_problems(sl("leave", context={"high_leverage": True})))
    # recomputed from context even when the row says low: NBA Q4, 2:00 left, down 3
    close_late = {"period": 4, "clock": "2:00", "score": {"home": 98, "away": 95}, "high_leverage": False}
    assert any("RULE1" in p for p in stay_leave_problems(sl("leave", context=close_late)))
    # overtime always counts
    assert any("RULE1" in p for p in stay_leave_problems(sl("leave", context={"period": 5, "clock": "4:00"})))
    # staying at high leverage is fine; leaving a blowout late is fine
    assert stay_leave_problems(sl("stay", context=close_late)) == []
    blowout = {"period": 4, "clock": "2:00", "score": {"home": 120, "away": 90}}
    assert stay_leave_problems(sl("leave", context=blowout)) == []


def test_nhl_and_nfl_close_late_also_count():
    nhl = sl("leave", league="nhl", context={"period": 3, "clock": "17:00", "score": {"home": 2, "away": 2}})
    assert any("RULE1" in p for p in stay_leave_problems(nhl))      # ESPN's NHL clock counts up: 3:00 left
    nfl = sl("leave", league="nfl", context={"period": 4, "clock": "3:30", "score": {"home": 20, "away": 17}})
    assert any("RULE1" in p for p in stay_leave_problems(nfl))


def test_adaptive_buffer_floor_cap_and_sum():
    ok = {"behind_adaptive_s": 41.0, "feed_lag_p95_s": 20.0, "sync_err_s": 6.0, "tune_s": 9.0, "margin_s": 6.0}
    assert adaptive_problems(ok) == []
    assert adaptive_problems({"behind_adaptive_s": 0.0})                     # never 0
    assert adaptive_problems({"behind_adaptive_s": 9.0})                     # below the floor
    assert adaptive_problems({"behind_adaptive_s": 120.0})                   # above the cap
    low = {"behind_adaptive_s": 10.0, "feed_lag_p95_s": 2.0, "sync_err_s": 1.0, "tune_s": 1.0, "margin_s": 1.0}
    assert adaptive_problems(low) == []                                      # parts 5 s -> floored to 10
    high = {"behind_adaptive_s": 90.0, "feed_lag_p95_s": 80.0, "sync_err_s": 10.0, "tune_s": 10.0, "margin_s": 5.0}
    assert adaptive_problems(high) == []                                     # parts 105 s -> capped at 90
    wrong = dict(ok, behind_adaptive_s=60.0)
    assert any("clamp" in p for p in adaptive_problems(wrong))
    assert adaptive_problems({"behind_s": 90.0}) == []                       # rows without it are fine


def test_cap_on_every_row():
    assert cap_problems({"behind_s": 90.0}) == []
    assert cap_problems({"behind_s": 90.4})
    assert log_problems([sl(), sl(behind_s=95.0)]) == ["row 1: behind_s 95.0 above the 90 s cap"]


# ---- the controller: cap + shadow never holds (C1, C2; runs on today's code) -----------------------------------------

def _pausing(two_fixture):
    from redzone.tuners.adb import RunResult
    from tests.test_control import FakeStick

    class PausingStick(FakeStick):
        async def key(self, *codes):
            self.log.extend(codes)
            for c in codes:
                if c == "KEYCODE_MEDIA_PAUSE":
                    self.playback_state = "paused"
                elif c == "KEYCODE_MEDIA_PLAY":
                    self.playback_state = "playing"
            return RunResult(True, "", 0.01)

    c, tuner = two_fixture
    stick = PausingStick("b:5555", display={"input": "HDMI1"}, hdmi="HDMI2")
    c.sticks[1] = stick
    c.cfg.defaults["league_overrides"] = c.cfg.data["league_overrides"] = {}   # no per-league buffer
    return c, tuner, stick


from tests.test_control import two  # noqa: E402,F401  (fixture)


def _has_shadow_key() -> bool:
    import yaml
    d = yaml.safe_load((ROOT / "redzone" / "default_config.yaml").read_text())
    return "shadow" in ((d.get("timing") or {}).get("behind_live_s") or {})


@pytest.mark.skipif(not _has_shadow_key(), reason="timing.behind_live_s.shadow (lane 2) not on this branch yet")
@pytest.mark.parametrize("asked", [45.0, 90.0, 200.0])
def test_shadow_on_buffer_off_never_holds_the_stick(two, asked):  # noqa: F811
    c, tuner, stick = _pausing(two)
    c.cfg.update({"timing": {"behind_live_s": {"enabled": False, "shadow": True, "max_s": 90,
                                               "apps": ["youtubetv"]}}})
    c.behind_of = lambda target: asked
    held = []
    c.on_behind = lambda t, new, old: held.append((t, new, old))

    async def go():
        assert c.preload("youtubetv:fox")
        await c._preload_task
    asyncio.run(go())
    assert "KEYCODE_MEDIA_PAUSE" not in stick.log and "KEYCODE_MEDIA_PLAY" not in stick.log, stick.log
    assert not c.held, c.held
    assert all(new == 0.0 for _, new, _ in held), held
    assert c._buffer_for(stick, "youtubetv:fox", type("R", (), {"path": "tune"})()) == 0.0


@pytest.mark.parametrize("asked,want", [(45.0, 45.0), (90.0, 90.0), (200.0, 90.0), (-5.0, 0.0)])
def test_the_controller_never_holds_past_the_cap(two, asked, want):  # noqa: F811
    c, tuner, stick = _pausing(two)
    c.cfg.update({"timing": {"behind_live_s": {"enabled": True, "max_s": 90, "apps": ["youtubetv"]}}})
    c.behind_of = lambda target: asked
    assert c._buffer_for(stick, "youtubetv:fox", type("R", (), {"path": "tune"})()) == want


def test_the_default_config_keeps_the_buffer_off_and_capped(tmp_path):
    """Tonight is shadow only: the live buffer stays OFF, the cap is 90 and no league asks past it."""
    from redzone.config import Config
    cfg = Config(tmp_path / "none.yaml", tmp_path / "hist")
    assert float(cfg.get("timing.behind_live_s.max_s")) == CAP_S
    assert cfg.get("timing.behind_live_s.enabled") is False, "the live buffer must stay OFF tonight (shadow only)"
    for lg, b in (cfg.get("timing.behind_live_s.by_league") or {}).items():
        assert 0 < float(b) <= CAP_S, (lg, b)


# ---- the night lab with lane 2's shadow (turns on with redzone/buffer_shadow.py) -----------------------------------

SHADOW = importlib.util.find_spec("redzone.buffer_shadow") is not None
needs_shadow = pytest.mark.skipif(not SHADOW, reason="redzone/buffer_shadow.py (lane 2) not on this branch yet")
GRADER = ROOT / "scripts" / "buffer_shadow_grade.py"


def _night(tmp: Path, name: str, *sets: str) -> Path:
    from redzone.lab.night import run as night_run
    if not all((night_run.FIX / f"{g.fixture}.json").exists() for g in night_run.GAMES):
        pytest.skip("the night's ESPN fixtures are not in tests/fixtures")
    work = tmp / name
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("RZ_NIGHT_TRACE", None)
    args = [sys.executable, "-m", "redzone.lab.night", "--tv", "smartthings", "--start", "20:55", "--hours", "1.5",
            "--seed", "1", "--no-default-faults", "--work", str(work), "--json", str(tmp / f"{name}.json")]
    for s in sets:
        args += ["--set", s]
    p = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert (tmp / f"{name}.json").exists(), f"no report (exit {p.returncode}):\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}"
    return work


def _screen(work: Path):
    import sqlite3
    tv = [(round(json.loads(x)["at"]), json.loads(x)["requested"])
          for x in (work / "data" / "tv_input.jsonl").read_text().splitlines() if x.strip()]
    db = sqlite3.connect(work / "data" / "redzone.db")
    cols = [c[1] for c in db.execute("PRAGMA table_info(switches)")]
    keep = [c for c in ("ts", "target", "kind", "action", "game_id") if c in cols]
    sw = [tuple(round(v) if isinstance(v, float) else v for v in r)
          for r in db.execute(f"SELECT {', '.join(keep)} FROM switches ORDER BY 1")]
    return tv, sw


def _rows(work: Path) -> list[dict]:
    p = work / "data" / "buffer_shadow.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


@pytest.fixture(scope="module")
def t1_nights(tmp_path_factory):
    if not SHADOW:
        pytest.skip("redzone/buffer_shadow.py (lane 2) not on this branch yet")
    tmp = tmp_path_factory.mktemp("t1_nights")
    off = _night(tmp, "off")
    # B over the cap on every league: the shadow must clamp it and still change nothing on screen
    over = _night(tmp, "over_cap", "timing.behind_live_s.shadow=true",
                  "timing.behind_live_s.by_league={nfl: 200, cfb: 200, nba: 200, cbb: 200, wcbb: 200, nhl: 200}")
    return off, over


@needs_shadow
def test_t1_over_cap_shadow_changes_nothing_on_screen(t1_nights):
    off, over = t1_nights
    assert _screen(over) == _screen(off), "a shadow asked for B=200 changed a TV input or a switch"


@needs_shadow
def test_t1_shadow_log_holds_cap_floor_context_and_rule1(t1_nights):
    _, over = t1_nights
    rows = _rows(over)
    assert rows, "shadow on but no rows"
    probs = log_problems(rows)
    assert not probs, "\n".join(probs[:30])
    assert max(float(r.get("behind_s", 0)) for r in rows) <= CAP_S


@needs_shadow
def test_t1_every_game_gets_stay_leave_rows_with_context(t1_nights):
    _, over = t1_nights
    rows = _rows(over)
    sl_rows = [r for r in rows if r.get("kind") in FIELDS["stay_leave_kinds"]]
    assert sl_rows, "no stay/leave rows: Nick 9:45 AM wants every stay/leave call logged with its context"
    gids_decided = {str(r.get("gid")) for r in rows if r.get("kind") in ("decision", "entry") and r.get("gid")}
    gids_sl = {str(r.get("gid")) for r in sl_rows}
    assert gids_decided <= gids_sl, f"games with editor decisions but no stay/leave row: {gids_decided - gids_sl}"


@needs_shadow
def test_t1_adaptive_buffer_logged_next_to_the_cap(t1_nights):
    _, over = t1_nights
    with_adaptive = [r for r in _rows(over) if r.get(FIELDS["adaptive"]) is not None]
    if not with_adaptive:
        pytest.xfail("lane 2 has not logged the adaptive buffer (Nick 9:35 AM: 'if that fits before the freeze')")
    assert all(not adaptive_problems(r) for r in with_adaptive)


@needs_shadow
@pytest.mark.skipif(not GRADER.exists(), reason="#318's scripts/buffer_shadow_grade.py not on this branch yet")
def test_t1_over_cap_shadow_passes_318_grader(t1_nights):
    _, over = t1_nights
    spec = importlib.util.spec_from_file_location("buffer_shadow_grade", GRADER)
    bsg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bsg)
    res = bsg.grade(_rows(over), over / "games")
    assert res["S1"] == "PASS" and res["S2"] == "PASS", res["schema_problems"][:10]
    assert res["R1"] in ("PASS", "PENDING"), json.dumps(res["live"], indent=1)[:3000]

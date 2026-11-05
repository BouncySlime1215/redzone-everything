"""Time-shift buffer SHADOW path (Cycle 24, Nick 9:10 AM Nov 5): tests by the test writers.

Contract (lane 2, redzone/buffer_shadow.py): `timing.behind_live_s.shadow: true` while `enabled` is off runs a second
"editor" Decider B s behind live, in memory only, and logs data/buffer_shadow.jsonl. It must
  1. change nothing on screen (same TV inputs and switches as shadow off, same seed),
  2. touch no stick (editor rows carry stick/role null; any later pause/seek row is on the hidden stick only),
  3. write rows that pass the schema (scripts/buffer_shadow_grade.py S1/S2),
  4. never schedule a mid-play entry: every entry's arena moment is dead under BOTH graders (cutgrade + EXP-744
     clock truth), R1.
The grader unit tests run always; the night-lab tests turn on when redzone/buffer_shadow.py exists.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("buffer_shadow_grade", ROOT / "scripts" / "buffer_shadow_grade.py")
bsg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsg)
_spec2 = importlib.util.spec_from_file_location("clock_truth_grade", ROOT / "scripts" / "clock_truth_grade.py")
ctg = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(ctg)

FIXTURE = ROOT / "tests" / "fixtures" / "espn_nba_summary_401909860.json"
GID = "401909860"


def row(**kw):
    r = {"ts": 1.0e9, "mode": "shadow", "kind": "decision", "league": "nba", "gid": GID, "stick": None, "role": None,
         "behind_s": 90.0, "source": "default", "action": "stay", "target": None, "reason": "x"}
    r.update(kw)
    return r


def entry(arena_at, mu=40.0, behind=90.0, rewind=0.0, **kw):
    pic = arena_at + behind + mu + rewind
    return row(kind="entry", action="switch", to=GID, picture_at=pic, mu=mu, sd=15.0, rewind_s=rewind,
               arena_at=arena_at, **kw)


# ---- schema (S1/S2) -----------------------------------------------------------------------------------------

def test_good_rows_pass_the_schema():
    assert bsg.check_row(row()) == []
    assert bsg.check_row(entry(1.0e9, rewind=12.0)) == []
    assert bsg.check_row(row(kind="seek", stick="lan.181:5555", role="hidden")) == []


def test_the_editor_touches_no_stick():
    assert any("S2" in b for b in bsg.check_row(row(stick="lan.150:5555", role="visible")))
    assert any("S2" in b for b in bsg.check_row(entry(1.0e9, stick="lan.181:5555", role="hidden")))


def test_pause_or_seek_only_on_the_hidden_stick():
    for k in ("pause", "seek", "hold", "resume"):
        assert any("S2" in b for b in bsg.check_row(row(kind=k, stick="lan.150:5555", role="visible")))
        assert any("S2" in b for b in bsg.check_row(row(kind=k)))     # no stick named at all


def test_schema_catches_bad_values():
    assert bsg.check_row(row(mode="on"))
    assert bsg.check_row(row(behind_s=91.0))
    assert bsg.check_row(row(behind_s=-1.0))
    bad = entry(1.0e9)
    bad["arena_at"] += 5.0                     # not picture_at - behind_s - mu - rewind_s
    assert any("arena_at" in b for b in bsg.check_row(bad))
    nolg = entry(1.0e9)
    nolg["league"] = None
    assert bsg.check_row(nolg)
    missing = row()
    del missing["source"]
    assert bsg.check_row(missing)


# ---- Rule #1 (R1), both graders -------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    d = tmp_path_factory.mktemp("arch")
    (d / "nba").mkdir()
    shutil.copy(FIXTURE, d / "nba" / f"summary_{GID}.json")
    return d


def _moments(archive):
    """One arena moment both graders call dead, one both call live, from the fixture game."""
    from redzone.lab import cutgrade
    rp = bsg._replay(archive, "nba", GID)
    ev = ctg.events(archive, "nba", GID)
    dead = live = None
    t, end = ev[0][0] + 60, ev[-1][0] - 60
    while t < end and (dead is None or live is None):
        d, _, _ = cutgrade._dead_at(rp.league, rp.wall, rp.plays, t)
        c = ctg.label(ev, t, band=3)
        if d and c == "dead" and dead is None:
            dead = t
        if not d and c == "live" and live is None:
            live = t
        t += 7.0
    assert dead is not None and live is not None
    return dead, live


def test_rule1_passes_dead_landings_and_fails_a_live_one(archive):
    dead, live = _moments(archive)
    ok = bsg.grade([row(), entry(dead)], archive)
    assert ok["R1"] == "PASS" and ok["entries_graded_cutgrade"] == 1, ok
    bad = bsg.grade([entry(dead), entry(live)], archive)
    assert bad["R1"] == "FAIL" and bad["entries_live"] == 1, bad


def test_rule1_reads_the_rewind_into_the_landing(archive):
    dead, live = _moments(archive)
    # a rewind entry is graded where the rewound picture lands (arena_at already takes rewind_s off)
    assert bsg.grade([entry(live, rewind=30.0)], archive)["R1"] == "FAIL"
    assert bsg.grade([entry(dead, rewind=20.0)], archive)["R1"] == "PASS"


def test_rule1_is_pending_without_play_by_play(tmp_path):
    r = bsg.grade([entry(1.0e9)], tmp_path)
    assert r["R1"] == "PENDING" and r["entries"] == 1


def test_cli_exit_code(archive, tmp_path):
    dead, live = _moments(archive)
    log = tmp_path / "buffer_shadow.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [row(), entry(live)]) + "\n")
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "buffer_shadow_grade.py"), str(log),
                        "--archive", str(archive)], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert p.returncode == 1 and "FAIL" in p.stdout, p.stdout + p.stderr


# ---- the shadow path on the night lab (turns on with lane 2's redzone/buffer_shadow.py) -------------------------

SHADOW = importlib.util.find_spec("redzone.buffer_shadow") is not None
needs_shadow = pytest.mark.skipif(not SHADOW, reason="redzone/buffer_shadow.py (lane 2) not on this branch yet")


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
    tv = [(round(json.loads(x)["at"]), json.loads(x)["requested"])
          for x in (work / "data" / "tv_input.jsonl").read_text().splitlines() if x.strip()]
    db = sqlite3.connect(work / "data" / "redzone.db")
    cols = [c[1] for c in db.execute("PRAGMA table_info(switches)")]
    keep = [c for c in ("ts", "target", "kind", "action", "game_id") if c in cols]
    sw = [tuple(round(v) if isinstance(v, float) else v for v in r)
          for r in db.execute(f"SELECT {', '.join(keep)} FROM switches ORDER BY 1")]
    return tv, sw


@pytest.fixture(scope="module")
def nights(tmp_path_factory):
    if not SHADOW:
        pytest.skip("redzone/buffer_shadow.py (lane 2) not on this branch yet")
    tmp = tmp_path_factory.mktemp("nights")
    off = _night(tmp, "off", "timing.behind_live_s.shadow=false")   # shadow defaults on (lane 2, #326)
    on = _night(tmp, "shadow", "timing.behind_live_s.shadow=true")
    return off, on


@needs_shadow
def test_shadow_changes_nothing_on_screen(nights):
    off, on = nights
    assert not (off / "data" / "buffer_shadow.jsonl").exists()
    tv_off, sw_off = _screen(off)
    tv_on, sw_on = _screen(on)
    assert tv_on == tv_off, "shadow changed a TV input"
    assert sw_on == sw_off, "shadow changed a switch"


@needs_shadow
def test_shadow_log_schema_and_no_stick(nights):
    _, on = nights
    rows = [json.loads(x) for x in (on / "data" / "buffer_shadow.jsonl").read_text().splitlines() if x.strip()]
    assert rows, "shadow on but no rows"
    res = bsg.grade(rows, on / "games")
    assert res["S1"] == "PASS" and res["S2"] == "PASS", res["schema_problems"][:10]


@needs_shadow
def test_shadow_never_schedules_a_mid_play_entry(nights):
    _, on = nights
    rows = [json.loads(x) for x in (on / "data" / "buffer_shadow.jsonl").read_text().splitlines() if x.strip()]
    res = bsg.grade(rows, on / "games")
    assert res["entries_graded_cutgrade"] >= 1, "the editor made no gradable entry on the night"
    assert res["R1"] == "PASS", json.dumps(res["live"], indent=1)[:3000]

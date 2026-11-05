"""[T9 tester, Nov 5 2026] Dry-run RC8 flip in the replay sim (redzone.lab.night, no TV, no stick).

A full night (all five games, so CFB, which ships off, plays too) with the shipped defaults plus ONE league
flipped (NHL, which ships live): the night must stay clean (every night check, 0 mid-play cuts), the flipped league's stream is held at its per-league buffer within the 90 s cap, and every
other league's streams are held exactly as the shipped defaults say (on at their by_league value, or not at all).
A full night with the same flip ran clean on Nov 5 (55 cuts, 0 mid-play; NHL espn held 90.1 s, NFL fox/nbc 60.1 s,
CFB abc and NBA prime untouched).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELD = re.compile(r"buffer: (\S+) on \S+ held ([0-9.]+)s behind live")


def _expected_hold_s(cfg, league: str) -> float:
    from redzone import config
    if not config.for_league(cfg, "timing.behind_live_s.enabled", league, False):
        return 0.0
    by = cfg.get("timing.behind_live_s.by_league") or {}
    b = by.get(league, cfg.get("timing.behind_live_s.default", 0))
    return min(float(b or 0), float(cfg.get("timing.behind_live_s.max_s", 90.0)))


def _flip_key():
    """The RC8 flag once redzone/buffer_rc8.py is in the tree, else the hidden-stick hold key it builds on."""
    try:
        from redzone import buffer_rc8
    except ImportError:
        return "timing.behind_live_s.enabled", None
    return buffer_rc8.RC8_FLAG, buffer_rc8


def test_dry_run_flip_of_one_league_in_the_replay_sim(tmp_path, cfg):
    from redzone.lab.night import run as night_run
    key, rc8 = _flip_key()
    if not all((night_run.FIX / f"{g.fixture}.json").exists() for g in night_run.GAMES):
        pytest.skip("the night's ESPN fixtures are not in tests/fixtures")
    out, work = tmp_path / "night.json", tmp_path / "w"
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
               TZ="America/New_York")
    env.pop("RZ_NIGHT_TRACE", None)
    proc = subprocess.run([sys.executable, "-m", "redzone.lab.night", "--tv", "smartthings",
                           "--seed", "1", "--no-default-faults",
                           "--set", "league_overrides.nhl=" + json.dumps({key: True}),
                           "--json", str(out), "--work", str(work)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert out.exists(), f"no report (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    night = json.loads(out.read_text())

    # Rule #1 and every night check hold with the flip.
    assert night["mid_play_cuts"] == [], night["mid_play_cuts"]
    bad = [c for c in night["checks"] if not c["ok"]]
    assert not bad, json.dumps(bad, indent=1, default=str)[:3000]

    # Who got held, and by how much.
    holds: dict[str, set[float]] = {}
    for m in HELD.finditer((work / "data" / "service.log").read_text()):
        holds.setdefault(m.group(1), set()).add(round(float(m.group(2))))
    league_of = {spec.target: spec.league for spec in night_run.GAMES}
    expect = {lg: _expected_hold_s(cfg, lg) for lg in set(league_of.values())}
    if rc8 is not None:
        cfg.update({"league_overrides": rc8.flip(dict(cfg.data), "nhl")})
        expect["nhl"] = rc8.buffer_for(cfg, "nhl")[0]
    else:
        expect["nhl"] = _expected_hold_s(cfg, "nhl") or min(float(cfg.get("timing.behind_live_s.by_league.nhl")), 90.0)
    assert 10.0 <= expect["nhl"] <= 90.0

    nhl_targets = [t for t, lg in league_of.items() if lg == "nhl"]
    assert any(t in holds for t in nhl_targets), f"the flipped league was never held: {holds}"
    for target, secs in holds.items():
        lg = league_of.get(target)
        assert lg is not None, f"held a stream the night doesn't schedule: {target}"
        assert expect[lg] > 0, f"{lg} ({target}) was held but the flip didn't touch it and it ships off"
        for s in secs:
            assert abs(s - expect[lg]) <= 1.0 and s <= 91.0, (target, s, expect[lg])

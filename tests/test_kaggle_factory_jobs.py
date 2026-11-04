"""The Kaggle factory notebook's season_job runs end to end for EVERY league in PLAN (moments + self-play),
offline: the two fetchers write small fixture seasons. Regression for the Nov 4 run where every CBB season
failed with KeyError: <League.CBB> (the oracle's grader tables had no college rows)."""
from __future__ import annotations

import ast
import glob
import gzip
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import noggin_factory as NF  # noqa: E402

from redzone.models import PERIOD_S, REGULATION, League  # noqa: E402

SCRIPT = ROOT / "scripts" / "kaggle_noggin_factory.py"


def _notebook_defs():
    """PLAN and season_job from the notebook script, without running its setup cells."""
    tree = ast.parse(SCRIPT.read_text())
    plan = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "PLAN")
    job = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "season_job")
    ns = {"range": range, "list": list}
    exec(compile(ast.Module([plan], []), str(SCRIPT), "exec"), ns)
    return ns["PLAN"], job


def _basketball_game(league: str, gid: str, start: datetime, home: str, away: str) -> list[dict]:
    """Plays every 30 s of wall time, a basket every other play, a one-point game into the last 2:00."""
    lg = League(league)
    reg, per_s = REGULATION[lg], PERIOD_S[lg]
    plays, t, hs, as_ = [], start, 0, 0
    for per in range(1, reg + 1):
        for k, clock in enumerate(range(per_s, 0, -30)):
            score = k % 2 == 0
            if score:
                hs, as_ = (hs + 2, as_) if (k // 2) % 2 == 0 else (hs, as_ + 2)
            plays.append({"id": f"{gid}{per}{k:03d}", "sequenceNumber": str(len(plays)),
                          "type": {"text": "Jump Shot" if score else "Defensive Rebound"},
                          "text": "x", "period": {"number": per}, "clock": {"displayValue": f"{clock // 60}:{clock % 60:02d}"},
                          "wallclock": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "scoringPlay": score,
                          "scoreValue": 2 if score else 0, "homeScore": hs, "awayScore": as_})
            t += timedelta(seconds=30)
        plays.append({"id": f"{gid}{per}end", "sequenceNumber": str(len(plays)), "type": {"text": "End Period"},
                      "text": "end", "period": {"number": per}, "clock": {"displayValue": "0:00"},
                      "wallclock": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "scoringPlay": False, "scoreValue": 0,
                      "homeScore": hs, "awayScore": as_})
        t += timedelta(seconds=120)
    return plays


def _hockey_game(gid, start, home, away):
    plays, t, hs = [], start, 0
    for per in (1, 2, 3):
        for k, clock in enumerate(range(1200, 0, -40)):
            goal = k == 10 and per != 2
            hs += goal
            plays.append({"id": f"{gid}{per}{k:03d}", "sequenceNumber": str(len(plays)),
                          "type": {"text": "Goal" if goal else "Shot"}, "text": "x", "period": {"number": per},
                          "clock": {"displayValue": f"{clock // 60}:{clock % 60:02d}"},
                          "wallclock": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "scoringPlay": goal,
                          "homeScore": hs, "awayScore": 1})
            t += timedelta(seconds=40)
        t += timedelta(seconds=1000)
    return plays


def _nights(year):
    """Three nights, two games each, well before every league's scorecard window."""
    base = datetime(year - 1, 12, 1, 0, 0, tzinfo=timezone.utc)
    return [(f"{d}{g}", base + timedelta(days=d, minutes=40 * g), *teams)
            for d in range(3) for g, teams in enumerate((("WSH", "NYK"), ("BOS", "LAL")))]


def _write_espn(league, year, dest: Path, **_):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest, "wt") as f:
        for gid, start, home, away in _nights(year):
            plays = (_hockey_game(gid, start, home, away) if league == "nhl"
                     else _basketball_game(league, gid, start, home, away))
            f.write(json.dumps({"id": gid, "season_type": 2, "plays": plays, "matchup": f"{away} @ {home}"}) + "\n")
    return True


def _write_pbp(league, year, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if league == "nfl":
        for gid, start, home, away in _nights(year):
            for k in range(160):
                q, t = 1 + k // 40, start + timedelta(seconds=40 * k)
                rows.append(dict(game_id=gid, play_id=float(k), time_of_day=t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 time=f"{14 - (k % 40) // 3}:00", qtr=float(q), play_type="pass" if k % 2 else "run",
                                 desc="pass complete" if k % 2 else "run", timeout=0.0, touchdown=float(k % 37 == 0),
                                 field_goal_result=None, extra_point_result=None, interception=0.0, fumble_lost=0.0,
                                 sack=0.0, complete_pass=float(k % 2), safety=0.0, sp=float(k % 37 == 0),
                                 total_home_score=float(7 * (k // 37)), total_away_score=0.0, season_type="REG",
                                 two_point_attempt=0.0))
    else:
        for gid, start, home, away in _nights(year):
            for p in _basketball_game(league, gid, start, home, away):
                rows.append(dict(id=p["id"], sequence_number=p["sequenceNumber"], type_text=p["type"]["text"],
                                 text=p["text"], period_number=p["period"]["number"],
                                 clock_display_value=p["clock"]["displayValue"], wallclock=p["wallclock"],
                                 scoring_play=p["scoringPlay"], score_value=p["scoreValue"], home_score=p["homeScore"],
                                 away_score=p["awayScore"], game_id=gid, home_team_abbrev=home, away_team_abbrev=away))
    pd.DataFrame(rows).to_parquet(dest, index=False)
    return True


def test_season_job_runs_for_every_league_in_plan(tmp_path, monkeypatch):
    plan, job = _notebook_defs()
    ns = {"OUT": str(tmp_path / "factory"), "RAW": str(tmp_path / "raw"), "SMOKE": False,
          "time": time, "os": os, "json": json, "glob": glob, "dict": dict, "round": round, "bool": bool,
          "len": len, "int": int, "__import__": __import__}
    exec(compile(ast.Module([job], []), str(SCRIPT), "exec"), ns)
    monkeypatch.setattr(NF, "fetch_espn_season", lambda league, year, dest, **k: _write_espn(league, year, Path(dest)))
    monkeypatch.setattr(NF, "_fetch", lambda url, dest: _write_pbp(Path(dest).parent.name,
                                                                  int(Path(dest).stem.split("_")[-1]), Path(dest)))
    assert set(plan) >= {"nba", "nfl", "nhl", "cbb"}
    for league, (years, synthetic) in plan.items():
        res = ns["season_job"](league, years[0] - 3, min(synthetic, 1), time.time() + 600)
        assert "error" not in res, (league, res)
        assert res["moments"] > 0, (league, res)
        if synthetic:
            assert res.get("selfplay_complete") is True and res["selfplay"] > 0, (league, res)
        assert not glob.glob(f"{ns['RAW']}/{league}/*"), "the raw season is deleted after the job"

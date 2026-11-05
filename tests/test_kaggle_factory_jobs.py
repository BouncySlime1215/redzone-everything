"""The Kaggle factory notebook's season_job runs end to end for EVERY league in PLAN (moments + self-play),
offline: the two fetchers write small fixture seasons. Regression for the Nov 4 run where every CBB season
failed with KeyError: <League.CBB> (the oracle's grader tables had no college rows)."""
from __future__ import annotations

import ast
import glob
import gzip
import json
import shutil
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
    consts = [n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "APIWEB_BEFORE"]
    ns = {"range": range, "list": list}
    exec(compile(ast.Module([plan], []), str(SCRIPT), "exec"), ns)
    return ns["PLAN"], ast.Module(consts + [job], [])


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


def _football_game(gid, start, home, away):
    """An ESPN-summary football game (the CFB feed's shape): snaps 40 s apart, a timeout and a TD now and then."""
    plays, t, hs = [], start, 0
    for q in (1, 2, 3, 4):
        for k in range(40):
            td = k == 20
            hs += 7 * td
            kind = "Passing Touchdown" if td else "Timeout" if k == 30 else "Pass Reception" if k % 2 else "Rush"
            clock = 900 - 22 * k
            plays.append({"id": f"{gid}{q}{k:03d}", "sequenceNumber": str(len(plays)), "type": {"text": kind},
                          "text": kind, "period": {"number": q},
                          "clock": {"displayValue": f"{clock // 60}:{clock % 60:02d}"},
                          "wallclock": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "scoringPlay": td,
                          "homeScore": hs, "awayScore": 3})
            t += timedelta(seconds=40 if kind != "Timeout" else 150)
        t += timedelta(seconds=120 if q != 2 else 1200)
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
                     else _football_game(gid, start, home, away) if league == "cfb"
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
    exec(compile(job, str(SCRIPT), "exec"), ns)
    monkeypatch.setattr(NF, "fetch_espn_season", lambda league, year, dest, **k: _write_espn(league, year, Path(dest)))
    monkeypatch.setattr(NF, "_fetch", lambda url, dest: _write_pbp(Path(dest).parent.name,
                                                                  int(Path(dest).stem.split("_")[-1]), Path(dest)))
    assert set(plan) >= {"nba", "nfl", "nhl", "cbb", "cfb"} and "wcbb" not in plan   # Nick 7:55 PM: no WCBB
    for league, (years, synthetic) in plan.items():
        res = ns["season_job"](league, years[0] - 3, min(synthetic, 1), time.time() + 600)
        assert "error" not in res, (league, res)
        assert res["moments"] > 0, (league, res)
        if synthetic:
            assert res.get("selfplay_complete") is True and res["selfplay"] > 0, (league, res)
        assert res.get("tokens", 0) > 0, (league, res)            # NFM event tokens, every league
        info = json.loads(Path(ns["OUT"], "tokens", league, f"{years[0] - 3}.json").read_text())
        assert info["complete"] and info["games"] == 6 and info["events"] > 0
        assert not glob.glob(f"{ns['RAW']}/{league}/*"), "the raw season is deleted after the job"


def test_every_planned_league_has_a_scorecard_cut():
    """A league missing from EVAL_FIRST_NIGHT would have NO cut (the .get default): its locked nights could leak."""
    from redzone.noggin import factory as F
    plan, _ = _notebook_defs()
    assert set(plan) <= set(F.EVAL_FIRST_NIGHT), set(plan) - set(F.EVAL_FIRST_NIGHT)


def test_college_football_reads_the_whole_fbs_board(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(NF, "_get_json", lambda sess, url, tries=4: seen.append(url))
    NF.fetch_espn_season("cfb", 2020, tmp_path / "espn_2020.jsonl.gz", jobs=1)
    boards = [u for u in seen if "scoreboard" in u]
    assert boards and all("groups=80" in u for u in boards)


def _apiweb_payload(gid, day):
    plays = [{"eventId": 1, "sortOrder": 1, "typeDescKey": "period-start", "periodDescriptor": {"number": 1},
              "timeRemaining": "20:00"}]
    for per in (1, 2, 3):
        for k in range(12):
            key = "goal" if k == 6 and per != 2 else "penalty" if k == 3 else "stoppage" if k == 9 else "shot-on-goal"
            det = {"homeScore": per, "awayScore": 1} if key == "goal" else \
                {"descKey": "cross-checking"} if key == "penalty" else {"reason": "tv-timeout"} if key == "stoppage" else {}
            plays.append({"eventId": 100 * per + k, "sortOrder": 100 * per + k, "typeDescKey": key, "details": det,
                          "periodDescriptor": {"number": per}, "timeRemaining": f"{19 - k}:00"})
        plays.append({"eventId": 100 * per + 99, "sortOrder": 100 * per + 99, "typeDescKey": "period-end",
                      "periodDescriptor": {"number": per}, "timeRemaining": "00:00"})
    return {"id": int(gid), "gameDate": day, "gameType": 2, "homeTeam": {"abbrev": "WSH"},
            "awayTeam": {"abbrev": "PIT"}, "plays": plays}


def test_nhl_apiweb_plays_read_like_the_espn_feed():
    import nhl_apiweb as NA

    from redzone.models import League, Stoppage
    from redzone.sources import espn
    g = NA.convert(_apiweb_payload("2015020001", "2014-10-08"))
    kinds = [p["type"]["text"] for p in g["plays"]]
    assert g["matchup"] == "PIT @ WSH" and g["date"] == "2014-10-08" and "Cross-Checking" in kinds
    stops = [espn.stoppage_for(League.NHL, p) for p in g["plays"]]
    assert Stoppage.TIMEOUT in stops and Stoppage.SCORE_BREAK in stops and Stoppage.INTERMISSION in stops
    assert Stoppage.WHISTLE in stops                                       # the penalty
    assert g["plays"][-1]["homeScore"] == 3 and g["plays"][-1]["awayScore"] == 1


def test_old_nhl_seasons_tokenize_from_apiweb(tmp_path, monkeypatch):
    import nhl_apiweb as NA
    plan, job = _notebook_defs()
    ns = {"OUT": str(tmp_path / "factory"), "RAW": str(tmp_path / "raw"), "SMOKE": False,
          "time": time, "os": os, "json": json, "glob": glob, "dict": dict, "round": round, "bool": bool,
          "len": len, "int": int, "__import__": __import__}
    exec(compile(job, str(SCRIPT), "exec"), ns)
    monkeypatch.setattr(NF, "fetch_espn_season", lambda league, year, dest, **k: False)    # no ESPN wall clocks

    def fake(year, dest, max_games=None, session=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(dest, "wt") as f:
            for k, (day, cut) in enumerate((("2015-01-10", False), ("2015-01-11", False), ("2026-11-01", True))):
                g = NA.convert(_apiweb_payload(f"201502000{k}", day))
                f.write(json.dumps(g) + "\n")
        return True
    monkeypatch.setattr(NA, "fetch_season", fake)
    monkeypatch.delenv("RZ_NHL_APIWEB", raising=False)
    assert not ns["season_job"]("nhl", 2015, 0, time.time() + 600).get("tokens")   # [#202] off by default (NHL.com ToS)
    shutil.rmtree(ns["OUT"], ignore_errors=True)
    shutil.rmtree(ns["RAW"], ignore_errors=True)
    monkeypatch.setenv("RZ_NHL_APIWEB", "1")
    res = ns["season_job"]("nhl", 2015, 0, time.time() + 600)
    info = json.loads(Path(ns["OUT"], "tokens", "nhl", "2015.json").read_text())
    assert res["tokens"] > 0 and info["games"] == 2 and info["source"] == "nhl-api-web"   # the eval-window game is dropped
    assert info["coverage"]["no_wall_games"] == 2
    assert not glob.glob(f"{ns['RAW']}/nhl/*")

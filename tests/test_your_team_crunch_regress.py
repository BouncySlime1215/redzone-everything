"""Your-team crunch is never left and always wins over another crunch (test writers, planner + integrator Nov 6).

Regression for the WV loop's v3 reads (/mnt/project-files/wv-loop/v3reads/your_team_crunch_misses.md), app as shipped
(eval_config pinned, 90 s buffer, 70 s feed lag):
  20251110 WSH @ DET (401810054)  82/105: a catch-up return left WSH's Q4 for LAL, then an ORL crunch; back at 0:01.
  20251212 CLE @ WSH (401836753)  12/31: the app stayed on a CHI crunch during WSH's own crunch.
Both are eval-data-v3 FIT nights (dev_before 20261001), never locked.

The CI test runs both nights from a committed fixture, tests/fixtures/your_team_crunch/: ESPN summaries in the
night lab's espn_nba_summary_<gid> form, trimmed to what the replay reads (header teams/status, pickcenter, the
play rows up to 5 min after WSH's last play, nested fields cut to type/period/clock/team ids; no win probability,
no box score), the slate's scoreboard (games that start after WSH ends dropped) and the pinned eval_config.yaml.
The trimmed nights grade exactly like the full v3 archive (82/105 and 12/31 on integration/next 4023ac1). Earlier
plays can't be cut: the 20251110 miss is a catch-up return set ~2 h before WSH's Q4. There's no channels file,
as in the v3 archive. Grader: sportsgrade's my_crunch / my_crunch_live (frozen), through scripts/eval_sports.py.

test_v3_fit_your_team_crunch_all reads the whole v3 fit split (462/506 today). It needs the v3 archive and
RZ_V3_READS=1 (about 20 min), and skips otherwise.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "your_team_crunch"
sys.path.insert(0, str(ROOT / "scripts"))
NIGHTS = [("20251110", "401810054"), ("20251212", "401836753")]
MINE_FIRST: list[str] = []   # [EXP-214] decider.crunch.mine_first ships ON: the test guards the default config


def _run(monkeypatch, capsys, root: Path, argv: list[str]) -> dict:
    import eval_sports as es
    monkeypatch.setattr(es, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["eval_sports.py", *argv])
    capsys.readouterr()
    es.main()
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("day,gid", NIGHTS)
def test_your_team_crunch_is_never_left(day, gid, tmp_path, monkeypatch, capsys):
    arch = tmp_path / "data" / "archive" / "nba"
    arch.mkdir(parents=True)
    shutil.copy(FIX / "eval_config.yaml", tmp_path / "data" / "eval_config.yaml")
    shutil.copy(FIX / f"espn_nba_scoreboard_{day}.json", arch / f"scoreboard_{day}.json")
    for e in json.loads((FIX / f"espn_nba_scoreboard_{day}.json").read_text())["events"]:
        shutil.copy(FIX / f"espn_nba_summary_{e['id']}.json", arch / f"summary_{e['id']}.json")
    out = _run(monkeypatch, capsys, tmp_path, ["nba", "--end", day, "--nights", "1",
                                                 "--behind-live", "90", "--feed-lag", "70", *MINE_FIRST])
    n = next(x for x in out["per_night"] if x["night"] == day)
    assert n["my_crunch"] > 0, f"{day}: no your-team crunch graded (fixture broken?)"
    assert n["my_crunch_live"] == n["my_crunch"], (
        f"{day}: your-team crunch live {n['my_crunch_live']}/{n['my_crunch']}: the app left WSH's crunch "
        f"or another crunch beat it")


def test_v3_fit_your_team_crunch_all():
    if os.environ.get("RZ_V3_READS") != "1" or not (ROOT / "data/archive/nba/summary_401810054.json").exists():
        pytest.skip("v3-wide read: needs the eval-data-v3 archive and RZ_V3_READS=1 (~20 min)")
    env = {**os.environ, "RZ_EVAL_LOCK": "eval-data-v3.lock.json"}
    out = json.loads(subprocess.run([sys.executable, "scripts/eval_sports.py", "nba", "--split", "fit",
                                     "--behind-live", "90", "--feed-lag", "70", *MINE_FIRST], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=3600, check=True).stdout)
    live, total = (int(x) for x in out["your_team_crunch_live"].split("/"))
    assert total > 0 and live == total, f"v3 fit your-team crunch live {live}/{total}"


def test_mine_first_ships_on():
    """[EXP-214] Your team first is an overseer default (integrator, Nov 6); Nick can turn it off."""
    import yaml
    cfg = yaml.safe_load((ROOT / "redzone" / "default_config.yaml").read_text())
    assert cfg["decider"]["crunch"]["mine_first"] is True

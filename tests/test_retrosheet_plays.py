"""Retrosheet event files -> ESPN-format MLB plays: runs, half-inning ends, pitching changes, no invented clocks."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import retrosheet_plays as R  # noqa: E402

EVN = """id,ATL202304060
version,2
info,visteam,SDN
info,hometeam,ATL
info,date,2023/04/06
start,snelb001,"Blake Snell",0,0,1
start,stris002,"Spencer Strider",1,0,1
play,1,0,grist001,02,CFFS,K
play,1,0,sotoj001,21,BBCX,HR/F89
play,1,0,machm001,32,SS.BBBC,K
play,1,1,acunr001,21,SBBX,D7/L7L
play,1,1,olsom001,00,X,S8/G.2-H
play,1,1,rilea001,00,X,63/G
sub,hillt001,"Tim Hill",0,0,1
play,2,0,bogax001,00,NP,NP
play,2,0,bogax001,00,X,E6/G.B-1
play,2,1,darnt001,00,X,HR/F7.1-H
"""


def test_parse_scores_innings_and_pitching_changes():
    (g,) = R.parse(EVN)
    assert g["id"] == "ATL202304060" and g["date"] == "2023-04-06" and g["matchup"] == "SDN @ ATL"
    kinds = [p["type"]["text"] for p in g["plays"]]
    assert kinds == ["Strikeout", "Home Run", "Strikeout", "End Inning", "Double", "Single", "Out",
                     "Pitching Change", "End Inning", "Error", "End Inning", "Home Run", "End Inning"]
    last = g["plays"][-1]
    assert (last["awayScore"], last["homeScore"]) == (1, 3)          # Soto HR; Olson RBI single; d'Arnaud 2-run HR
    assert all("wallclock" not in p for p in g["plays"])               # no time label is invented


def test_runs_counts_moves_home_and_errors_that_save_the_runner():
    assert R.runs("HR/F7.1-H") == 2 and R.runs("HR/F7.B-H;1-H") == 2 and R.runs("S8/G.3-H;2-H") == 2
    assert R.runs("S8/G.3XH(72)") == 0 and R.runs("S8/G.3XH(2E7)") == 1 and R.runs("HP") == 0


def test_tokenizes_with_unknown_time_and_mlb_stoppages(tmp_path):
    pytest.importorskip("pandas")                      # CI test jobs have no pandas
    import gzip
    import json

    import noggin_tokens as T
    f = tmp_path / "retro_2023.jsonl.gz"
    with gzip.open(f, "wt") as h:
        h.write(json.dumps(R.parse(EVN)[0]) + "\n")
    info = T.build("mlb", f, 2023, tmp_path / "tok")
    assert info["games"] == 1 and info["source"] == "retrosheet" and info["coverage"]["no_wall_games"] == 1

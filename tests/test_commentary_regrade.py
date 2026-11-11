"""EXP-1404: the commentary importance re-grade (agent/kaggle/commentary_regrade.py)."""
import gzip
import json
from pathlib import Path

import pytest

from agent.kaggle import commentary_regrade as C

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def college_rules(monkeypatch):
    """The moment builder patches sportsgrade's big/crunch globally; undo it so other tests see the originals."""
    from agent.kaggle import build_moments_kaggle as B
    from redzone.lab import sportsgrade
    monkeypatch.setattr(sportsgrade, "is_big", sportsgrade.is_big)
    monkeypatch.setattr(sportsgrade, "is_crunch", sportsgrade.is_crunch)
    monkeypatch.setattr(B, "_PATCHED", False)
    B._college_rules()


@pytest.mark.parametrize("lg,gid", [("nba", "401909860"), ("nfl", "401772894"), ("cbb", "401820788")])
def test_rebuilt_big_next_matches_the_moment_builder(lg, gid, college_rules):
    """The join check: our play index and big_next are the moment builder's (reasoner_data.game_rows)."""
    from redzone.config import Config
    from redzone.lab import reasoner_data
    from redzone.lab.backtest import Replay
    from redzone.models import League
    r = Replay.load(League(lg), FIX / f"espn_{lg}_summary_{gid}.json")
    lab = {x["id"]: x for x in C.label_replay(lg, r, "20260101")}
    assert len(lab) == len(r.plays)
    rows = reasoner_data.game_rows(League(lg), r, "20260101", Config(), {"WSH"})
    assert rows
    for row in rows:
        x = lab[f"{lg}:{r.gid}:{row.i}"]
        assert x["big_next"] == row.big_next
        assert x["crunch"] in (0, 1) and x["y_out"] in (0, 1)


def test_grade_flags_a_clock_rule_and_finds_real_signal(tmp_path):
    import random
    rng = random.Random(0)
    ans, lab = [], []
    for n in range(40):
        for i in range(40):
            per, clock, diff = 1 + i % 4, 60.0 * rng.randint(0, 5) + 1, rng.randint(0, 7)
            rule = int(per == 4 and clock <= 120 and diff <= 5)            # big_next = the clock rule
            y = int(rng.random() < 0.3)                                    # outcome: unrelated to the clock
            imp = 9 * rule + 5 * y + rng.random()                          # Teacher reads the rule AND the outcome
            gid = f"{n}"
            ans.append({"id": f"nba:{gid}:{i}|commentary", "text": json.dumps({"importance": imp})})
            lab.append({"id": f"nba:{gid}:{i}", "night": f"2024{n:04d}", "period": per, "clock_s": clock,
                        "abs_diff": diff, "crunch": rule, "big_next": rule, "y_out": y, "flip": y, "wp_swing": 0,
                        "close_finish": 0})
    a, l_ = tmp_path / "a.jsonl", tmp_path / "l.jsonl.gz"
    a.write_text("".join(json.dumps(x) + "\n" for x in ans))
    with gzip.open(l_, "wt") as f:
        f.write("".join(json.dumps(x) + "\n" for x in lab))
    g = C.grade(a, l_, {"nba": 0.99}, draws=50)["per_league"]["nba"]
    assert g["old_label_is_our_rule"] and g["old_big_next"]["join_ok"]
    assert g["y_out"]["within_context_auc"] > 0.7 and g["verdict"].startswith("REAL SIGNAL")
    assert C.grade(a, l_, {"nba": 0.5}, draws=10)["per_league"]["nba"]["verdict"].startswith("VOID")


def test_games_lists_ids_only(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"id": "nba:9:1|commentary", "text": "secret"}) + "\n"
                 + json.dumps({"id": "nfl:4:2|commentary", "text": "x"}) + "\n")
    assert C.games(p) == {"nba": {"9": [1]}, "nfl": {"4": [2]}}

"""Nick, Nov 4 10:55 PM: a second "Go now" tap switches at once, even mid-play. The graders count it on
its own line, never as the decider joining mid-play (grader v11). And college leagues read their pro
league's play-length row (oracle.py used to KeyError on a CBB/CFB replay)."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_a_forced_mid_play_entry_is_its_own_line(tmp_path):
    import collect_live_test as c
    now = time.time()
    d = tmp_path / "data"
    d.mkdir()
    (d / "tv_input.jsonl").write_text("")
    cuts = [{"ts": now - 9, "enter": "play", "leave": "dead"},
            {"ts": now - 8, "enter": "play", "leave": "dead", "forced": True},
            {"ts": now - 7, "enter": "dead", "leave": "dead", "forced": True}]
    (d / "supervisor_cuts.jsonl").write_text("\n".join(map(json.dumps, cuts)) + "\n")
    out = tmp_path / "out"
    assert c.main(["--data", str(d), "--out", str(out), "--since", str(int(now - 3600))]) == 0
    s = json.loads((out / "summary.json").read_text())
    assert s["entries_mid_play"] == 1 and s["entries_mid_play_forced"] == 1


def test_grade_cut_carries_the_forced_flag():
    from types import SimpleNamespace as NS

    from redzone.supervisor import grade_cut
    dead = NS(landing=lambda a, b: NS(state="play", why="ball live", kind="", resume=None), windows=lambda: [])
    tl = {"g": NS(dead=dead)}
    t = {"cut_at": 1.0, "picture_at": 2.0, "to": "g", "enter_delay": {"mu": 40.0}}
    assert grade_cut(t | {"forced": True}, tl)["forced"] is True
    assert grade_cut(t, tl)["forced"] is False and grade_cut(t, tl)["enter"] == "play"


@pytest.mark.parametrize("lg,base", [("CBB", "NBA"), ("CFB", "NFL")])
def test_college_leagues_read_their_pro_rows(lg, base):
    from redzone.grading import PLAY_LEN, RESUME_LEAD
    from redzone.models import League
    college, pro = League[lg], League[base]
    assert college.base == pro and PLAY_LEN[college] == PLAY_LEN[pro]
    assert RESUME_LEAD.get(college, 0) == RESUME_LEAD.get(pro, 0)

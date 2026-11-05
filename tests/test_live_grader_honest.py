"""[lane 3, Nov 5] Wed Nov 4's live "17 of 30 entries mid-play": 15 were phone taps, 2 first picks, every one
labeled "play may be live" (no dead window known), graded on the unsynced 40 +/- 15 s Prime prior while the
stick measured ~16 s. The live grader now says "unknown" when it can't call a landing, grades on the measured
delay once it has one, and the summary counts the app's entries apart from your taps."""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _timelines(why, seen):
    def landing(a, b):
        seen.append(a)
        return NS(state="play", why=why, kind="", resume=None)
    return {"g": NS(dead=NS(landing=landing, windows=lambda: []))}


def _trace(**kw):
    return {"cut_at": 100.0, "picture_at": 102.0, "to": "g", "target": "prime:x",
            "enter_delay": {"mu": 40.0, "sigma": 15.0}, **kw}


def test_a_landing_no_dead_window_covers_is_unknown_not_play():
    from redzone.decider.cuts import NO_DEAD_WHY
    from redzone.supervisor import grade_cut
    g = grade_cut(_trace(), _timelines(NO_DEAD_WHY, []))
    assert g["enter"] == "unknown" and NO_DEAD_WHY in g["enter_why"]
    assert grade_cut(_trace(), _timelines("play resumes while you land", []))["enter"] == "play"


def test_the_measured_delay_wins_once_it_is_tighter_than_the_plan():
    from redzone.supervisor import grade_cut
    book = NS(measured=lambda t: t == "prime:x", get=lambda t: NS(mu=16.0, sigma=1.5))
    seen = []
    g = grade_cut(_trace(), _timelines("dead ball on screen", seen), book)
    assert seen == [102.0 - 16.0] and g["enter_delay"] == {"mu": 16.0, "sigma": 1.5, "src": "measured"}
    seen.clear()                                   # not measured, or looser than the plan: the plan stands
    for b in (NS(measured=lambda t: False, get=book.get), NS(measured=lambda t: True, get=lambda t: NS(mu=16.0, sigma=20.0))):
        g = grade_cut(_trace(), _timelines("dead ball on screen", seen), b)
        assert g["enter_delay"]["src"] == "planned"
    assert seen == [62.0, 62.0]


def test_the_summary_counts_the_apps_entries_apart_from_your_taps(tmp_path):
    import collect_live_test as c
    now = time.time()
    d = tmp_path / "data"
    d.mkdir()
    (d / "tv_input.jsonl").write_text("")
    cuts = [{"ts": now - 9, "enter": "play", "kind": "start"},
            {"ts": now - 8, "enter": "play", "kind": "user"},
            {"ts": now - 7, "enter": "play", "kind": "user", "forced": True},
            {"ts": now - 6, "enter": "unknown", "kind": "user"},
            {"ts": now - 5, "enter": "dead", "kind": "ended", "enter_delay": {"mu": 16.0, "src": "measured"}}]
    (d / "supervisor_cuts.jsonl").write_text("\n".join(map(json.dumps, cuts)) + "\n")
    out = tmp_path / "out"
    assert c.main(["--data", str(d), "--out", str(out), "--since", str(int(now - 3600))]) == 0
    s = json.loads((out / "summary.json").read_text())
    assert (s["entries_mid_play"], s["entries_mid_play_user"], s["entries_mid_play_forced"]) == (1, 1, 1)
    assert s["entries_unknown"] == 1 and s["entries_measured_delay"] == 1
    assert "chosen by the app" in (out / "summary.md").read_text()

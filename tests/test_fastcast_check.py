"""[fastcast live check] Grading FastCast against polling (redzone/sources/fastcast_check.py)."""
import json
import random

from redzone.sources import fastcast_check as fcc
from redzone.sources.fastcast_check import PUSHED, POLLED, FastcastCheck, grade_log

T = 1_793_750_000.0


def play(i, home=0, away=0, kind="Jump Shot"):
    return {"id": str(i), "homeScore": home, "awayScore": away, "type": {"text": kind}}


def night(chk, games=2, plays=100, lead=3.0, jitter=0.5, drop=0, lie=None, seed=0):
    """Each game: both feeds' first batch (ignored), then `plays` new plays, FastCast `lead` s ahead."""
    rng = random.Random(seed)
    for g in range(games):
        gid = f"g{g}"
        chk.saw(POLLED, gid, [play(0)], T)
        chk.saw(PUSHED, gid, [play(0)], T)
        for i in range(1, plays + 1):
            t = T + 10 * i
            chk.saw(PUSHED, gid, [], t)                                  # FastCast is watching
            if i > drop:
                kind = "Turnover" if lie == i else "Jump Shot"
                chk.saw(PUSHED, gid, [play(i, i, 0, kind)], t)
            chk.saw(POLLED, gid, [play(i, i, 0)], t + lead + rng.gauss(0, jitter))
    for k in range(1, 20):                                               # ...and keeps watching
        chk.saw(PUSHED, gid, [], T + 10 * plays + 10 * k)
    return T + 10 * plays + fcc.PAIR_WINDOW_S + 60


def test_pending_until_enough_plays_then_pass():
    chk = FastcastCheck()
    end = night(chk, games=1, plays=50)
    g = chk.grade(end)
    assert g["result"] == "pending" and g["paired_plays"] == 50
    chk = FastcastCheck(started=T)
    end = night(chk)
    g = chk.grade(end)
    assert g["result"] == "pass" and g["paired_plays"] == 200 and abs(g["lead_median_s"] - 3.0) < 0.3
    assert g["coverage_pct"] == 100.0 and g["disagreements"] == 0 and not g["reasons"]


def test_first_batches_never_count():
    chk = FastcastCheck()
    chk.saw(POLLED, "g", [play(i) for i in range(40)], T)          # already on when we started watching
    chk.saw(PUSHED, "g", [play(i) for i in range(40)], T + 30)
    assert chk.grade(T + 500)["paired_plays"] == 0 and chk.grade(T + 500)["missed_by_fastcast"] == 0


def test_not_faster_fails():
    chk = FastcastCheck(started=T)
    g = chk.grade(night(chk, lead=0.3))
    assert g["result"] == "fail" and "median lead" in g["reasons"][0]


def test_often_behind_fails():
    chk = FastcastCheck(started=T)
    g = chk.grade(night(chk, lead=1.5, jitter=4.0))
    assert g["result"] == "fail" and any("behind polling" in r for r in g["reasons"])


def test_missing_plays_fails_on_coverage():
    chk = FastcastCheck(started=T)
    g = chk.grade(night(chk, drop=10))
    assert g["missed_by_fastcast"] == 20 and g["result"] == "fail" and "delivered" in g["reasons"][-1]


def test_a_game_the_shadow_dropped_does_not_count_as_missed():
    chk = FastcastCheck()
    chk.saw(POLLED, "g", [play(0)], T)
    chk.saw(PUSHED, "g", [play(0)], T)
    chk.saw(POLLED, "g", [play(1)], T + 10)                           # FastCast unsubscribed: nothing more
    assert chk.grade(T + 3600)["missed_by_fastcast"] == 0


def test_one_disagreement_fails_even_early():
    chk = FastcastCheck(started=T)
    g = chk.grade(night(chk, games=1, plays=20, lie=7))
    assert g["result"] == "fail" and g["disagreements"] == 1
    assert g["disagreement_examples"][0]["pushed"][1] == "Turnover"


def test_scores_disagree_fails():
    chk = FastcastCheck()
    chk.saw(POLLED, "g", [play(0)], T)
    chk.saw(PUSHED, "g", [play(0)], T)
    chk.saw(PUSHED, "g", [play(1, 2, 0)], T + 10)
    chk.saw(POLLED, "g", [play(1, 3, 0)], T + 13)
    assert chk.grade(T + 20)["disagreements"] == 1


def test_too_long_without_enough_plays_stops():
    chk = FastcastCheck(started=T)
    night(chk, games=1, plays=10)
    g = chk.grade(T + fcc.MAX_SHADOW_S + 1)
    assert g["result"] == "stopped" and g["reasons"]


def test_tick_writes_the_verdict_and_freezes_it(tmp_path):
    chk = FastcastCheck(path=tmp_path / "fastcast_check.json", started=T)
    end = night(chk)
    assert chk.tick(end)["result"] == "pass" and chk.done
    assert json.loads((tmp_path / "fastcast_check.json").read_text())["result"] == "pass"
    chk.saw(PUSHED, "g0", [play(999, 0, 99, "Turnover")], end)   # after the verdict: ignored
    assert chk.tick(end + 100)["result"] == "pass"


def test_offline_grade_from_the_flight_recorder(tmp_path):
    rows = []
    for g in range(2):
        for i in range(1, 101):
            t = T + 10 * i
            rows.append({"t": t, "league": "nba", "game": f"g{g}", "play": str(i), "type": "Shot", "source": PUSHED})
            rows.append({"t": t + 2.5, "league": "nba", "game": f"g{g}", "play": str(i), "type": "Shot",
                         "source": POLLED})
    rows.append({"t": T, "league": "nba", "game": "g0", "play": "x", "source": "nhl"})   # other feeds ignored
    p = tmp_path / "plays.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    g = grade_log(p)
    assert g["result"] == "pass" and g["paired_plays"] == 200 and g["lead_median_s"] == 2.5


def test_script_exit_codes(tmp_path, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("fcs", Path(__file__).parent.parent / "scripts/fastcast_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.main(["--data", str(tmp_path), "--day", "2026-11-03"]) == 2
    (tmp_path / "fastcast_check.json").write_text(json.dumps({"result": "pass"}))
    assert m.main(["--data", str(tmp_path), "--live"]) == 0 and "fastcast\": true" in capsys.readouterr().out


def test_hours_without_live_games_dont_use_up_the_shadow_budget():
    # [fastcast mac] a restart at midnight used to burn the 4 h on an empty slate ("0 plays over 0 games")
    chk = FastcastCheck(started=T)
    for k in range(0, int(fcc.MAX_SHADOW_S) + 3600, 60):
        chk.watching(False, T + k)
    g = chk.grade(T + fcc.MAX_SHADOW_S + 3600)
    assert g["result"] == "pending" and g["watched_s"] == 0
    for k in range(0, int(fcc.MAX_SHADOW_S) + 120, 60):       # then 4 h of live games
        chk.watching(True, T + 20000 + k)
    assert chk.grade(T + 20000 + fcc.MAX_SHADOW_S + 120)["result"] == "stopped"


def test_the_file_carries_the_client_status(tmp_path):
    chk = FastcastCheck(path=tmp_path / "fastcast_check.json", started=T)
    chk.client = {"connected": False, "task": "running", "wanted": 2, "last_error": "HTTP 403"}
    chk.tick(T + 60)
    import json
    assert json.loads((tmp_path / "fastcast_check.json").read_text())["client"]["last_error"] == "HTTP 403"

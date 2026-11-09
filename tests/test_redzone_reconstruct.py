"""The RedZone reconstruction obeys its rules, labels itself a reconstruction, and refuses locked nights.

Why: the rows become weak labels for cut pacing (data job 13), so a mid-play cut, a peek at the future or a
locked night in them would poison later models without any visible failure.
"""
import csv
import json

import pytest

from scripts import redzone_reconstruct as rr

P = rr.Params()
T0 = 1_757_264_400        # a Sunday 17:00 UTC


def play(t, ty="Rush", down=1, yte=60, end_yte=None, period=1, clock="10:00", home=0, away=0, scoring=False, text=""):
    return {"id": f"{t}{ty}", "wallclock": rr._iso(t), "type": {"text": ty}, "text": text or ty, "scoringPlay": scoring,
            "period": {"number": period}, "clock": {"displayValue": clock}, "homeScore": home, "awayScore": away,
            "start": {"down": down, "yardsToEndzone": yte},
            "end": {"yardsToEndzone": yte if end_yte is None else end_yte}, "statYardage": 3}


def summary(gid, drives, kick=T0):
    return {"drives": {"previous": [{"plays": d} for d in drives]},
            "header": {"competitions": [{"date": rr._iso(kick)[:16] + "Z", "competitors": [
                {"homeAway": "away", "team": {"abbreviation": f"A{gid}"}},
                {"homeAway": "home", "team": {"abbreviation": f"H{gid}"}}]}]}}


def game(gid, drives):
    return rr.load_game(summary(gid, drives), gid, P)


def quiet(t0, n=40, gap=40, **kw):
    return [play(t0 + i * gap, **kw) for i in range(n)]


def td(t, yte=5):
    return play(t, "Passing Touchdown", down=1, yte=yte, end_yte=0, scoring=True, text="touchdown")


def test_score_elsewhere_gets_a_replay_cut_at_the_dead_ball():
    a, b = game("a", [quiet(T0)]), game("b", [quiet(T0 + 5, n=10) + [td(T0 + 405, yte=50)] + quiet(T0 + 460, n=10)])
    show = rr.simulate([a, b], P)
    cut = next(c for c in show.real_cuts if c.reason == "score")
    assert cut.to_game == "b" and cut.rule == "copy.1.score"
    assert cut.t >= T0 + 405 + P.play_s          # not before the play ended
    assert next(x for x in b.triggers if x.kind == "td").served == "replay"


def test_trigger_order_score_beats_red_zone():
    on_air_rz = quiet(T0, n=2) + [play(T0 + 80 + i * 40, yte=15) for i in range(30)]
    scorer = quiet(T0 + 3, n=4) + [td(T0 + 163, yte=50)] + quiet(T0 + 220, n=20)
    show = rr.simulate([game("rz", [on_air_rz]), game("td", [scorer]), game("x", [quiet(T0 + 7, n=30)])], P)
    cut, back = show.real_cuts[:2]
    assert (cut.to_game, cut.reason) == ("td", "score")
    assert (back.to_game, back.reason) == ("rz", "rz_entry") and back.t - cut.t >= P.score_hold_s


def test_never_cuts_during_a_play_in_either_game():
    a = game("a", [quiet(T0, n=60, gap=20)])
    b = game("b", [quiet(T0 + 6, n=8, gap=20) + [td(T0 + 166)] + quiet(T0 + 180, n=40, gap=20)])
    show = rr.simulate([a, b], P)
    assert show.real_cuts and rr.mid_play_cuts(show) == 0


def test_ceiling_three_cuts_in_any_minute():
    drives = {g: [[play(T0 + o + i * 20, yte=15) for i in range(40)]] for g, o in (("a", 0), ("b", 7), ("c", 13))}
    show = rr.simulate([game(g, d) for g, d in drives.items()], P)
    assert rr.peak_per_60s(show.real_cuts) <= 3


def test_no_lookahead_past_the_delay():
    """Cutting off the feed later must not change any cut before the cutoff minus the delay."""
    full = [quiet(T0, n=30), quiet(T0 + 9, n=8) + [td(T0 + 329)] + quiet(T0 + 380, n=15),
            [play(T0 + 17 + i * 40, yte=18) for i in range(25)]]
    cutoff = T0 + 300
    shown = rr.simulate([game(str(i), [d]) for i, d in enumerate(full)], P)
    short = rr.simulate([game(str(i), [[p for p in d if rr.parse_ts(p["wallclock"]) < cutoff]])
                         for i, d in enumerate(full)], P)
    early = [c for c in shown.cuts if c.t < cutoff - P.delay_s]
    assert early == [c for c in short.cuts if c.t < cutoff - P.delay_s]


def test_every_cut_was_knowable_when_it_was_made():
    games = [game("a", [quiet(T0, n=30)]),
             game("b", [quiet(T0 + 9, n=4) + [play(T0 + 169 + i * 40, yte=15) for i in range(6)] + [td(T0 + 409)]])]
    show = rr.simulate(games, P)
    assert all(c.known_at <= c.t + P.delay_s for c in show.real_cuts)


def test_a_timeout_on_air_sends_the_show_elsewhere():
    a = game("a", [quiet(T0, n=5) + [play(T0 + 200, "Official Timeout")] + quiet(T0 + 380, n=5)])
    show = rr.simulate([a, game("b", [quiet(T0 + 5, n=20)])], P)
    cut = show.real_cuts[0]
    assert cut.reason == "break:strong_drive" and cut.t >= T0 + 200 - P.delay_s and show.on_air(cut.t) == "b"


def test_idle_game_is_not_left_for_filler():
    show = rr.simulate([game("a", [quiet(T0, n=20)]), game("b", [quiet(T0 + 5, n=20)])], P)
    assert show.real_cuts == []


def test_last_game_standing_gets_no_more_cuts():
    a = game("a", [quiet(T0, n=5) + [play(T0 + 200, "End of Game", period=4, clock="0:00")]])
    b = game("b", [quiet(T0 + 5, n=30)])
    show = rr.simulate([a, b], P)
    assert [c.to_game for c in show.real_cuts] == ["b"]
    assert show.real_cuts[0].rule == "copy.12.last_game"
    assert show.on_air(show.end) == "b"


def test_late_narrowing_keeps_only_close_games_and_scores():
    close = [play(T0 + 7 + i * 30, period=4, clock="4:00", home=20, away=17, yte=70) for i in range(20)]
    blowout = [play(T0 + i * 30, period=4, clock="4:00", home=40, away=3, yte=15) for i in range(20)]
    show = rr.simulate([game("blow", [blowout]), game("close", [close])], P)
    assert [c.to_game for c in show.real_cuts][0] == "close"
    assert all(c.to_game == "close" or c.reason == "score" for c in show.real_cuts)


def test_a_score_nobody_can_reach_in_time_is_dropped():
    a = game("a", [[play(T0 + i * 5, "Rush") for i in range(60)]])         # always in a play: no dead ball for a minute
    b = game("b", [quiet(T0 + 5, n=2) + [td(T0 + 90)] + quiet(T0 + 140, n=3)])
    rr.simulate([a, b], P)
    assert next(x for x in b.triggers if x.kind == "td").served == "dropped"


def _archive(root, garbage_locked=True):
    d = root / "nfl"
    d.mkdir(parents=True)
    nights = [f"202509{x:02d}" for x in (7, 14, 21, 28)] + [f"202510{x:02d}" for x in (5, 12, 19, 26)]
    for i, n in enumerate(nights):
        (d / f"scoreboard_{n}.json").write_text(json.dumps({"events": [{"id": f"g{i}", "name": "x"}]}))
        kick = int(rr.parse_ts(f"{n[:4]}-{n[4:6]}-{n[6:]}T17:00:00Z"))
        data = summary(f"g{i}", [quiet(kick, n=10)], kick=kick)
        (d / f"summary_g{i}.json").write_text("{ locked: opening this raises" if garbage_locked and i >= 6 else
                                              json.dumps(data))
    return nights


def test_locked_night_is_refused_and_never_opened(tmp_path, capsys):
    nights = _archive(tmp_path)
    out = tmp_path / "out"
    assert rr.main(["--archive", str(tmp_path), "--out", str(out), "--nights", nights[-1]]) == 2
    assert "refused" in capsys.readouterr().err
    assert not out.exists()


def test_default_run_skips_locked_nights_by_name(tmp_path, capsys):
    nights = _archive(tmp_path)                  # the two locked summaries are garbage: opening one would raise
    out = tmp_path / "out"
    assert rr.main(["--archive", str(tmp_path), "--out", str(out)]) == 0
    capsys.readouterr()
    shown = {r[0] for r in csv.reader(x for x in (out / "windows.csv").open() if not x.startswith("#"))}
    assert nights[0] in shown and nights[-1] not in shown


def test_output_says_it_is_a_reconstruction(tmp_path, capsys):
    _archive(tmp_path)
    out = tmp_path / "out"
    rr.main(["--archive", str(tmp_path), "--out", str(out)])
    capsys.readouterr()
    assert (out / "cuts.csv").read_text().startswith("# RECONSTRUCTION, NOT A RECORD")
    assert (out / "windows.csv").read_text().startswith("# RECONSTRUCTION, NOT A RECORD")
    assert json.loads((out / "summary.json").read_text())["notice"].startswith("RECONSTRUCTION")
    assert "NOT A RECORD" in rr.NOTICE and "RECONSTRUCTION" in rr.__doc__


def test_output_inside_the_repo_is_refused(tmp_path, capsys):
    _archive(tmp_path)
    assert rr.main(["--archive", str(tmp_path), "--out", str(rr.ROOT / "reconstruct_out")]) == 2
    assert "public" in capsys.readouterr().err
    assert not (rr.ROOT / "reconstruct_out").exists()
    with pytest.raises(ValueError):
        rr.write_outputs(rr.ROOT / "reconstruct_out", {}, {})

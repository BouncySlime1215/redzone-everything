"""PARK facts and timing (redzone/noggin/park_facts.py, park_timing.py): what the app may know of a LIVE game, row by row.

Pinned here: fed the rows of recorded dev nights one at a time, the facts builder calls final exactly the dead
stretches the grader's whole-game facts have (`scripts/park_ceiling.truth_of`), never calls a stoppage open once a
later row shows the clock moved, closes the open-stoppage hole (ledger L25), and equals truth_of once the game is
over. On hand-made rows: the hole itself, the earliest restart of an open stoppage, a feed that rewrites a row. The
timing model: the sign of K with the three K's measured on Nov 10, the four error parts and the padding per game,
unknown K wide and never zero or borrowed. The guards ported from the show runner. Nothing in the app imports them.

Why: every fact handed to the PARK planner in the app must be one it could know at that second; this is where that
is proved, on recorded nights and on rows typed to show the edge cases.
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from redzone.grading import dead_at  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin import park_core as core  # noqa: E402
from redzone.noggin import park_facts as pf  # noqa: E402
from redzone.noggin import park_timing as pt  # noqa: E402

ARCHIVE = ROOT / "data/archive/nba"
needs_archive = pytest.mark.skipif(not ARCHIVE.exists(), reason="no recorded dev nights")
T = 1_800_000_000.0        # hand-made rows start here (feed seconds); nothing real


# ------------------------------------------------------------------------------------------ recorded dev nights
def _two_dev_nights():
    if not ARCHIVE.exists():
        return ["none"]
    import park_facts_check as chk
    return chk.dev_nights("nba", 2)


@needs_archive
@pytest.mark.parametrize("night", _two_dev_nights())
def test_row_by_row_the_final_stretches_are_the_graders_whole_game_answer(night):
    import park_facts_check as chk
    t = chk.check_night(night)
    assert t["games"] >= 8 and t["rows"] > 4000
    assert t["mismatch"] == 0                                # every stretch called final is truth_of's, and none is missing
    assert t["confirmed"] > 900                              # most of the night's stretches were final before its game ended
    assert t["open_after_move"] == 0                         # never "still open" once a later row shows the clock moved
    assert t["hole"] > 50 and t["hole_closed"] == t["hole"]  # the grader on the rows so far says "stopped" there; the builder says play
    assert t["final_equal"] == t["games"]                    # game over: every key equals truth_of (dead, brk, shots, hl, lev, v2, big)


# ------------------------------------------------------------------------------------------ hand-made rows
def row(dt, kind, clock, *, period=1, scoring=False, shooting=False, home=10, away=8, seq=[0]):
    seq[0] += 1
    return {"id": str(seq[0]), "sequenceNumber": str(seq[0]), "type": {"text": kind}, "text": kind.lower(),
            "clock": {"displayValue": clock}, "period": {"number": period}, "homeScore": home, "awayScore": away,
            "scoringPlay": scoring, "shootingPlay": shooting,
            "wallclock": datetime.fromtimestamp(T + dt, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def hole_rows():
    """A foul at +10 s (clock 8:00) and a substitution at +40 s at the same clock: open. Then an out-of-bounds turnover at
    +70 s with the clock at 7:52: play ran 8 s before it, so the foul's stoppage ended at +62 s; no PLAY row is in yet."""
    return [row(0, "Jump Shot", "8:10", shooting=True), row(2, "Defensive Rebound", "8:08"), row(10, "Personal Foul", "8:00"),
            row(40, "Substitution", "8:00"), row(70, "Out of Bounds - Lost Ball Turnover", "7:52"), row(75, "Substitution", "7:52"),
            row(90, "Jump Shot", "7:40", shooting=True)]


def feed(rows):
    g = pf.GameFacts("nba", "g1")
    for p in rows:
        g.add(p)
    return g


def test_an_open_stoppage_is_open_until_a_row_dates_its_end():
    rows = hole_rows()
    g = feed(rows[:4])
    op = g.facts().open
    assert op is not None and op.since == T + 10 and op.row == T + 40 and op.anchor == "edge"
    assert op.earliest_restart == T + 40                     # read at its last row: it was stopped then
    assert g.facts(edge=T + 80).open.earliest_restart == T + 80 - 26     # the grader's own lead before the feed's edge
    assert g.settled() == T + 10                             # nothing from the foul on is final
    assert g.facts().dead == []


def test_the_open_stoppage_hole_stays_closed():
    """Ledger L25: read the grader's way, the foul's stoppage is 'still stopped' until a later PLAY row exists, though the
    turnover row's clock shows play ran from +62 s. The builder dates it from that row at once."""
    rows = hole_rows()
    g = feed(rows[:5])
    ws, ps = g.wall, g.plays
    assert dead_at(League.NBA, ws, ps, T + 65)[0]            # the hole: the grader on these rows says stopped at +65 s
    op = g.facts().open
    assert op is not None and op.since == T + 70             # what is open now is the turnover's stoppage, not the foul's
    view, unsure = g.planner_view(T + 5000, with_value=False)
    assert (T + 10, T + 62) in view["dead"] and not any(a <= T + 65 < b for a, b in view["dead"])    # play from +62 s
    assert view["dead"][-1] == (T + 70, T + 5000) and unsure == T + 10    # the turnover's runs on; nothing is final yet
    assert g.settled() == T + 10                             # dated, but final only once a play row is in (a game can end here)
    g.add(rows[5])
    g.add(rows[6])                                           # the play row: now everything up to it is final
    f = g.facts()
    assert f.settled == T + 90 and f.open is None
    assert list(zip(f.dead, f.brk)) == [((T + 10, T + 62), False), ((T + 70, T + 78), False)]
    assert f.live == [(T, T + 10), (T + 62, T + 70)]
    whole = [dead_at(League.NBA, g.wall, g.plays, T + s)[0] for s in range(0, 90)]
    mine = [any(a <= T + s < b for a, b in f.dead) for s in range(0, 90)]
    assert whole == mine                                     # second for second the grader's answer on the whole rows


def test_a_made_baskets_four_seconds_are_open_only_while_they_last():
    g = feed([row(0, "Jump Shot", "8:10", shooting=True), row(5, "Driving Layup Shot", "8:05", scoring=True, shooting=True, home=12)])
    assert g.facts().open is not None and g.facts().open.earliest_restart <= T + 5 + 4
    assert g.facts(edge=T + 10).open is None                 # past the 4 s: play runs unless a row says not
    assert g.facts().shot_windows == [(T, T + 4), (T + 5, T + 9)]


def test_rows_sharing_the_last_second_are_never_final_early():
    g = feed([row(0, "Jump Shot", "8:10", shooting=True), row(3, "Defensive Rebound", "8:07")])
    assert g.settled() <= T + 3                              # a later row may still be stamped +3 s
    g.add(row(3, "Personal Foul", "8:07"))
    assert g.facts().open is not None and g.facts().open.since == T + 3


def test_update_takes_summaries_and_starts_over_when_the_feed_rewrites_a_row():
    rows = hole_rows()
    g = pf.GameFacts("nba", "g1")
    assert g.update({"plays": rows[:4]}, final=False) == 4 and g.update({"plays": rows[:7]}, final=False) == 3
    assert g.facts().dead == feed(rows).facts().dead
    fixed = [dict(p) for p in rows]
    fixed[4] = {**fixed[4], "clock": {"displayValue": "7:50"}}       # ESPN corrected the turnover's clock
    g.update({"plays": fixed}, final=False)
    assert g.facts().dead == feed(fixed).facts().dead and g.facts().dead[0] == (T + 10, T + 60)
    g.update({"plays": fixed}, final=True)
    assert g.final and g.settled() == math.inf and g.facts().open is None
    with pytest.raises(ValueError):
        g.add(row(100, "Jump Shot", "7:30"))


def test_basketball_only():
    for lg in ("nfl", "nhl", "mlb"):
        with pytest.raises(ValueError):
            pf.GameFacts(lg)
    assert pf.GameFacts("cbb").league == League("cbb")


def test_high_leverage_and_value_rows_are_kept_per_row():
    late = [row(0, "Jump Shot", "2:00", period=4, shooting=True, home=100, away=97),
            row(5, "Jump Shot", "1:55", period=4, shooting=True, home=100, away=90)]
    g = feed(late)
    assert g.hl == [(T - 7.0, T)]                             # close and late (4th, 6:00 or less, within 6); the second is not close
    assert [x[0] for x in g.lev] == [T, T + 5] and g.value() == ([], [])     # value needs a summary (update)


# ------------------------------------------------------------------------------------------ timing
K_TABLE = json.loads("""{
"espn": {"channel": "espn", "K": 2.852, "spread": 0.491, "n": 5, "date": "2026-11-10", "pm": 0.28, "thrown": 14, "n_tenths": 0, "source": "chrome", "league": "cbb", "wide": false, "waiting": 0},
"espn2": {"channel": "espn2", "K": -2.777, "spread": 0.503, "n": 5, "date": "2026-11-10", "pm": 0.28, "thrown": 2, "n_tenths": 0, "source": "chrome", "league": "cbb", "wide": false, "waiting": 0},
"nbc": {"channel": "nbc", "K": 24.768, "spread": 0.478, "n": 11, "date": "2026-11-10", "pm": 0.18, "thrown": 8, "n_tenths": 0, "source": "chrome", "league": "nba", "wide": false, "waiting": 0}
}""")   # tools/calibrate/k_table.json as the calibration tool wrote it on Nov 10 (ledger L29, L30, L36)
TODAY = "2026-11-10"


def tonight(channel, league):
    return pt.GameTiming(league, pt.k_for(K_TABLE, channel=channel, league=league, game_id="x", today=TODAY, unnamed_ok=True))


def test_the_sign_of_k_with_the_three_games_measured_on_nov_10():
    """K = content time of a frame minus the feed time of the game-clock second on it. espn2 (FLA at FSU): the feed's
    time is 2.78 s LATER than the frame's; nbc (BOS at PHI): the feed's time is about 25 s EARLIER than the frame's."""
    espn2, espn, nbc = tonight("espn2", "cbb"), tonight("espn", "cbb"), tonight("nbc", "nba")
    F = T + 500.0
    assert espn2.content_of_feed(F) == pytest.approx(F - 2.777) and nbc.content_of_feed(F) == pytest.approx(F + 24.768)
    assert espn.content_of_feed(F) == pytest.approx(F + 2.852)
    assert nbc.content_of_feed(F) - espn2.content_of_feed(F) == pytest.approx(27.545)       # nbc shows the same second 27.5 s later
    for g in (espn2, espn, nbc):
        assert g.feed_of_content(g.content_of_feed(F)) == pytest.approx(F)
        tune = pt.Tune.from_read(content_at=F + 40.0, position=7000.0)     # a read: content F + 40 s at position 7000 s
        pos = g.position_of_feed(F, tune)
        assert pos == pytest.approx(7000.0 - 40.0 + g.k_value) and g.feed_of_position(pos, tune) == pytest.approx(F)
    assert [round(g.air_delay(), 3) for g in (espn2, espn, nbc)] == [17.223, 22.852, 44.768]   # K + the 20 s nominal live lag


def test_k_belongs_to_one_game_on_one_night():
    k = lambda gid, teams, ch, lg, **kw: pt.k_for(table, channel=ch, league=lg, game_id=gid, teams=teams, today=TODAY, **kw)
    table = {"espn2": {"channel": "espn2", "K": -2.777, "spread": 0.503, "date": TODAY, "league": "cbb"},
             "espn": {"channel": "espn", "K": 2.85, "spread": 0.49, "date": TODAY, "league": "cbb", "game": "401800001"},
             "nbc": {"channel": "nbc", "K": 24.768, "spread": 0.478, "date": TODAY, "league": "nba", "game": "BOS @ PHI"},
             "tnt": {"channel": "tnt", "K": 9.0, "spread": 0.3, "date": "2026-11-09", "league": "nba", "game": "401800003"},
             "abc": {"channel": "abc", "K": 5.0, "spread": 3.0, "date": TODAY, "league": "nba", "game": "401800005", "wide": True}}
    assert k("401800001", ("MSU", "DUKE"), "espn", "cbb") == pt.GameK(2.85, 0.5, 0, "table")      # named by feed id; spread floored
    assert k("401800007", ("BOS", "PHI"), "nbc", "nba").k == 24.768                               # ... or by its teams
    assert k("401800002", ("KU", "UK"), "espn", "cbb") is None                    # another game on that channel later: unknown
    assert k("401800009", ("FLA", "FSU"), "espn2", "cbb") is None                 # a row naming no game could be an earlier game's
    assert k("401800009", ("FLA", "FSU"), "espn2", "cbb", unnamed_ok=True).k == -2.777
    assert k("401800003", ("BOS", "PHI"), "tnt", "nba") is None                   # yesterday's row
    assert k("401800001", ("MSU", "DUKE"), "espn", "nba") is None                 # the other league
    assert k("401800005", ("LAL", "GSW"), "abc", "nba") is None                   # a row the tool marked wide
    eyes = {"abc|nba|401800004": {"k_s": 4.1, "spread_s": 0.6, "n": 5}}
    assert k("401800004", ("LAL", "GSW"), "abc", "nba", eyes_k=eyes) == pt.GameK(4.1, 0.6, 5, "eyes")
    assert k("401800006", ("LAL", "GSW"), "abc", "nba", eyes_k=eyes) is None


def test_the_four_error_parts_and_each_games_padding():
    nbc, espn2 = tonight("nbc", "nba"), tonight("espn2", "cbb")
    assert nbc.errors() == (0.29, 0.5, 0.33, 0.13)          # read 0.29, K 0.478 floored to 0.5, NBA stamp 0.33, keys 0.13
    assert nbc.errors().total == pytest.approx(math.sqrt(0.29 ** 2 + 0.5 ** 2 + 0.33 ** 2 + 0.13 ** 2))
    assert nbc.padding() == pytest.approx(4 * 0.678159, abs=1e-5) and nbc.grid_padding() == 3
    assert espn2.errors() == (0.29, 0.503, 0.5, 0.13) and espn2.grid_padding() == 3           # college stamp 0.5
    assert nbc.errors(read=False)[0] == 1.5 and nbc.grid_padding(read=False) == 6             # a tune not read: the wide doubt
    p = pt.TimingParams(pad_k=3.0)
    assert pt.GameTiming("nba", nbc.k, p).padding() == pytest.approx(3 * 0.678159, abs=1e-5)
    assert pt.eyes_padding() == 2
    assert pt.ErrorParts(0, 0, 0, 0).grid_padding(4) == 1   # never zero on the grid


def test_unknown_k_is_wide_never_zero_and_never_borrowed():
    unknown = pt.GameTiming("nba", pt.k_for(K_TABLE, channel="nbc", league="nba", game_id="401800099", teams=("LAL", "GSW"), today=TODAY))
    assert not unknown.known and unknown.k is None           # the nbc row names no game: not this one's without the operator's word
    assert unknown.k_value == pt.TIMING.prior_k == 13.0 and unknown.k_value != 0
    assert unknown.k_sd == 12.0 and unknown.grid_padding() == 48 and unknown.padding() > 40
    assert unknown.air_delay() == 33.0
    assert pt.GameTiming("nba", pt.k_for(K_TABLE, channel="nbc", league="nba", game_id="x", today="2026-11-11",
                                         unnamed_ok=True)).grid_padding() == 48      # tomorrow, tonight's K is gone


def test_params_from_config():
    cfg = {"park.pad_k": 3.5, "park.timing.read_sd": 0.25, "park.timing.prior_sd": "bad", "park.ghost_s": 30}
    p = pt.TimingParams.from_config(lambda k, d=None: cfg.get(k, d))
    assert (p.pad_k, p.read_sd, p.prior_sd, p.tune_sd) == (3.5, 0.25, 12.0, 1.5)
    f = pf.FactsParams.from_config(lambda k, d=None: cfg.get(k, d))
    assert f.ghost_s == 30.0 and f.lead(League.NBA) == 26.0 and pf.FactsParams(open_lead_s=35.0).lead(League.NBA) == 35.0


def test_a_games_view_moves_to_the_stick_by_its_own_k():
    g = feed(hole_rows())
    view, _ = g.planner_view(T + 5000, with_value=False)
    nbc, unknown = tonight("nbc", "nba"), pt.GameTiming("nba", None)
    moved = pf.on_stick(view, nbc, show_delay=20.0)
    assert moved["dead"][0] == (T + 10 + 24.768 + 20.0 + 20.0, T + 62 + 24.768 + 20.0 + 20.0)    # + K + live lag + show delay
    assert moved["t0"] == pytest.approx(view["t0"] + 64.768) and moved["shots"][0] == pytest.approx(view["shots"][0] + 64.768)
    assert pf.on_stick(view, unknown)["dead"][0][0] == T + 10 + 33.0                            # no K: the wide prior's delay


def test_a_tune_read_or_not():
    t = pt.Tune.unread(wall_at=T + 100, position=50.0)
    assert not t.read and t.content(50.0) == T + 100 - 20.0  # assumed at its live edge, 20 s behind content time
    assert pt.Tune.from_read(T + 70, 50.0) == pt.Tune(T + 20, True)


# ------------------------------------------------------------------------------------------ the guards (ported from the show runner)
T0 = 1000


def game(gid, end, live, *, shots=(), hl=()):
    dead, prev = [], T0
    for a, b in live:
        if a > prev:
            dead.append((prev, a))
        prev = b
    if end > prev:
        dead.append((prev, end))
    return {"gid": gid, "t0": T0, "t1": end, "dead": dead, "brk": [False] * len(dead), "shots": list(shots), "big": [],
            "hl": list(hl), "lev": [(T0, 1.0, 5, 600.0)], "v2": []}


def two_games():
    """Game 0: a 40 s dead ball at 1100. Game 1: a dead ball 1090-1110, then a play with a shot at 1122."""
    return core.Night([game("a", 1400, [(1000, 1100), (1140, 1300), (1330, 1400)]),
                       game("b", 1400, [(1000, 1090), (1110, 1125), (1150, 1290), (1320, 1400)], shots=[1122])])


def ask(night, leave, land, *, t=None, pad=2, know=27, cap=145.0, over=False, sure=None, open_from=None):
    A, base = night.arrs(True), night.base
    t = (max(leave, land) + 5 if t is None else t) - base
    return pf.refusal(A, pad, t, 2, know, cap, 0, leave - base, over, 1, land - base, sure or [10 ** 9] * 2, open_from or [None] * 2)


def test_guards_padded_dead_ball_to_dead_ball_only():
    n = two_games()
    assert ask(n, 1120, 1100) == ""
    assert "leave is outside" in ask(n, 1050, 1100) and "leave is outside" in ask(n, 1101, 1100) and "leave is outside" in ask(n, 1139, 1100)
    assert ask(n, 1050, 1100, over=True) == ""
    assert "landing is outside" in ask(n, 1120, 1115) and "landing is outside" in ask(n, 1120, 1109) and "landing is outside" in ask(n, 1120, 1091)
    assert ask(n, 1120, 1100, t=1243) == "" and "145 s behind" in ask(n, 1120, 1100, t=1244)
    assert "leave is outside" in ask(n, 1120, 1100, t=1094) and ask(n, 1120, 1100, t=1095) == ""      # nothing past the feed's edge


def test_guards_high_leverage_and_open_stoppages():
    n = core.Night([game("a", 1400, [(1000, 1100), (1140, 1300)], hl=[(1118, 1125)]), game("b", 1400, [(1000, 1090), (1110, 1300)])])
    assert "high-leverage" in ask(n, 1120, 1100) and "high-leverage" in ask(n, 1116, 1100) and ask(n, 1110, 1100) == ""
    n, base = two_games(), two_games().base
    kw = dict(open_from=[1100 - base, None])
    assert "leave is outside" in ask(n, 1120, 1100, sure=[1121 - base, 10 ** 9], **kw)
    assert ask(n, 1120, 1100, sure=[1122 - base, 10 ** 9], **kw) == ""
    assert pf.sure_until(None, 500.0) == math.inf and pf.sure_until(100, 500.0) == 474.0
    assert pf.sure_until(100, 500.0, floor=480.0) == 480.0 and pf.sure_until(100, 500.0, eyes_hi=490.0) == 490.0


def test_a_games_own_padding_is_cut_into_its_arrays():
    night = two_games()
    g = night.games[1]
    plain = core.Arr(g, night.base, night.n, True)
    cut = pf.pad_arr(core.Arr(g, night.base, night.n, True), 3)
    for i in range(night.n - 400):                         # (a stretch running to the grid's end keeps its end)
        assert (cut.since[i] > 2 and cut.left[i] >= 2) == (plain.since[i] > 5 and plain.left[i] >= 5), i
    assert bytes(cut.dead) == bytes(plain.dead)
    a = plain.starts[1]
    seen = pf.pad_arr(core.Arr(g, night.base, night.n, True), 3, ({a}, set(), 0))
    assert seen.since[a] == 1 and cut.since[a] == 0         # an edge Eyes itself saw keeps Eyes's own (smaller) cut


def test_what_eyes_saw_goes_over_the_feeds():
    base, end = 1000, 9000.0
    g = {"dead": [(1100.0, 1130.0), (1200.0, 1260.0), (1300.0, end)], "brk": [False, True, False]}
    assert pf.merge_eyes(g, [{"lo": 150, "hi": 320, "stops": [(205, 252)], "open": 310}], base, end)["dead"] == \
        [(1100.0, 1130.0), (1205, 1252), (1310, end)]
    assert pf.merge_eyes(g, [{"lo": 150, "hi": 220, "stops": [(205, 220)], "open": None}], base, end)["dead"] == \
        [(1100.0, 1130.0), (1205, 1220), (1220, 1260.0), (1300.0, end)]
    assert pf.merge_eyes(g, [{"lo": 150, "hi": 220, "stops": [(160, 170)], "open": None}], base, end)["dead"] == \
        [(1100.0, 1130.0), (1160, 1170), (1300.0, end)]


def test_only_the_park_shows_own_modules_import_them():
    """Unit 8 (PARK-CONTROLLER) wired them in behind `park.enabled`: the show's controller and the engine's side of it
    are the only readers, and the engine imports those only once the flag is on (tests/test_park_flag_off.py)."""
    show = {"park_show.py", "park_run.py"}
    for f in (ROOT / "redzone").rglob("*.py"):
        if f.name in ("park_facts.py", "park_timing.py") or f.name in show:
            continue
        text = f.read_text()
        assert "park_facts" not in text and "park_timing" not in text, f
    for name in show:
        assert "park_timing" in next((ROOT / "redzone").rglob(name)).read_text()

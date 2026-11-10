"""[arbiter.catchup_route_short] With another missed must-see waiting, a catch-up clip is just the play. On by default (Nick, Nov 9); it needs arbiter.catchup_route, which is off.

Why: the audit's residual showed the biggest avoidable loss after catch-up is the one hidden stick's queue (1,543 of 3,456 value
points): a 30 s clip plus its seek holds the stick about 51 s while other moments run past the 180 s cap. These pin what may never
slip: off changes nothing, the play itself is always whole, nothing airs 180 s or more late, and a clip alone stays whole.
"""
from collections import Counter
from pathlib import Path

import pytest
import yaml

from redzone.noggin import arbiter as A
from redzone.noggin import catchup as C
from scripts import veto_audit as va
from tests.test_exits_catchup import CANDS, miss, route_decider, truth

ON, OFF = C.Route(reach_s=180.0, short=True), C.Route(reach_s=180.0)
A1, B1 = C.Moment("a", "ga", 5.0, 1000.0), C.Moment("b", "gb", 3.0, 1005.0)


def test_the_flag_is_registered_and_on_while_the_route_is_off():
    cfg = yaml.safe_load((Path(A.__file__).parents[1] / "default_config.yaml").read_text())
    assert cfg["arbiter"]["catchup_route_short"] is True and A.DEFAULT_PARAMS.catchup_route_short is True and C.Route().short is False
    assert cfg["arbiter"]["catchup_route"] is False      # the route itself stays off until the TV proves the load time, so short changes nothing live
    assert A.Params.from_config({"arbiter.catchup_route_short": True}.get).catchup_route_short is True


@pytest.mark.parametrize("now,end", [(1030.0, 1100.0), (1030.0, 1052.0), (1060.0, 1075.0), (1100.0, 1400.0), (1170.0, 1300.0)])
@pytest.mark.parametrize("pool", [(A1,), (A1, B1), (B1,)])
def test_flag_off_changes_nothing(now, end, pool):
    off = C.next_piece(pool, now, end, r=OFF)
    assert off == C.next_piece(pool, now, end, r=C.Route(reach_s=180.0, short=False))
    assert off is None or off.clip_s == OFF.clip_s          # every clip is the whole clip
    if len(pool) == 1:                                        # and a clip alone is the same clip with the flag on
        assert C.next_piece(pool, now, end, r=ON) == off


def test_with_another_waiting_the_clip_is_just_the_play():
    p = C.next_piece((A1, B1), 1030.0, 1100.0, r=ON)
    whole = C.next_piece((A1, B1), 1030.0, 1100.0, r=OFF)
    assert p.moment is whole.moment is A1 and p.offset_s == 0.0             # the same clip goes first
    assert p.seconds == C.first_piece_s(A1, ON) == 11.0 and p.last and p.clip_s == 11.0
    assert whole.seconds == 30.0 and p.until == whole.until - 19.0           # the screen is back on the live game 19 s sooner
    assert p.seconds >= ON.lead_s + A1.play_s                                 # the play is whole


def test_a_clip_waits_for_nobody_who_is_past_the_cap():
    gone = C.Moment("old", "gc", 5.0, 1030.0 + 3.2 + 10.0 - 180.0)            # its first piece would air exactly 180 s late
    p = C.next_piece((A1, gone), 1030.0, 1100.0, r=ON)
    assert p.moment is A1 and p.seconds == 30.0 and p.clip_s == 30.0


def test_a_short_clip_is_never_180_s_late_and_never_shorter_than_the_play():
    for now in range(1015, 1200):
        p = C.next_piece((A1, B1), float(now), now + 300.0, r=ON)
        if p is not None:
            assert C.lateness(p.moment, now + ON.flip_s, 0.0, ON) < C.HARD_CAP_S and p.seconds >= C.first_piece_s(p.moment, ON)
    assert C.next_piece((A1, B1), 1030.0, 1030.0 + 2 * 3.2 + 2.0 + 10.9, r=ON) is None       # dead time too short for the play: no clip


def test_a_clip_already_in_pieces_is_untouched():
    for r in (ON, OFF):
        p = C.next_piece((), 1060.0, 1200.0, doing=(A1, 12.0), r=r)
        assert p.offset_s == 12.0 and p.seconds == 18.0 and p.last and p.clip_s == 30.0


def test_the_decider_ends_a_short_clip_with_its_one_piece():
    d, m, other = route_decider(), miss(), miss(1004.0, "p2")
    other.game_id = "c"
    d.queue = [m, other]
    p = A.Params(catchup_route=True, catchup_route_reach_s=170.0, catchup_route_short=True)
    got = d._catchup_route({**CANDS, "c": CANDS["b"]}, 1040.0, p)
    assert got is not None and got.piece_s == 12.0 and got.offset_s == 0.0 and d._route_clip == (got, 12.0)      # 3 s lead-in, an 8 s NFL play, 1 s
    d.arbiter_log, d.cfg.v["arbiter.plan_clip_s"], d._clip = Counter(), 30.0, got
    d.screen_m.to("replaying", "t", now=1040.0)
    d.catchup_m.to("airing", "t", now=1040.0)
    assert not d._route_piece_ended(1052.0) and d._route_doing is None and d.catchup_m.is_("idle")       # finished: nothing more is owed


def test_the_decider_keeps_the_whole_clip_with_the_flag_off_or_nobody_waiting():
    two = {**CANDS, "c": CANDS["b"]}
    for flag, queue_len, want in ((False, 2, 30.0), (True, 1, 30.0)):
        d, m, other = route_decider(), miss(), miss(1004.0, "p2")
        other.game_id = "c"
        d.queue = [m, other][:queue_len]
        got = d._catchup_route(two, 1040.0, A.Params(catchup_route=True, catchup_route_reach_s=170.0, catchup_route_short=flag))
        assert got.piece_s == want and d._route_clip == (got, 30.0)


def test_the_audit_airs_more_clips_in_the_same_dead_time_and_the_same_openings(monkeypatch):
    a, b, c = va.Air(1000.0, "b", "td", 5.0), va.Air(1002.0, "c", "td", 5.0), va.Air(1004.0, "d", "td", 5.0)
    t = truth(monkeypatch, {"b": [a], "c": [b], "d": [c]})
    ticks = [(float(n), "a", True) for n in range(1020, 1180, 2)]
    whole, _ = va.route_shown([a, b, c], va.Tally(route_on=True, route2=ticks), t, 100)
    short, stats = va.route_shown([a, b, c], va.Tally(route_on=True, route2=ticks, route_short=True), t, 100)
    assert len(whole) == 2 and len(short) == 3 and set(whole) <= set(short)
    assert stats["max_late_s"] < 180.0 and stats["pieces"] == 3
    mid = truth(monkeypatch, {"b": [a], "c": [b]}, dead=(False, False, 0.0))
    assert va.route_shown([a, b], va.Tally(route_on=True, route2=ticks, route_short=True), mid, 180)[0] == {}       # still never mid-play


def test_the_printed_latest_air_is_the_latest_not_a_sum(monkeypatch):
    rows = []
    for league in ("nfl", "nba"):
        row = va.new_row()
        row["league"], row["brain"], row["card"] = league, "hindsight", {"entries_mid_play": 0, "entries": 0, "cuts_high": 0, "mid_play_leaves": 0}
        for reach in map(str, va.REACHES):
            row["route"][reach].update({"pieces": 1, "clips": 1, "value": 5.0, "max_late_s": 170.0})
        rows.append(row)
    assert "latest 170.0 s" in va.table(rows) and "latest 340" not in va.table(rows)

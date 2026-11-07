"""[jump mode] Nick, Nov 7: a rotation over every live game, 4-5 switches a minute, never joining mid-play."""
from redzone.decider.decider import Candidate, Decider
from redzone.decider.screen import DelayBook, Event
from redzone.models import League
from redzone.scoring.engine import Scored

MU = 35.0


def sc(gid, score, lock=None, crunch=False, league=League.CFB):
    return Scored(gid, league, score, score, score, 1.0, 0.0, f"reason {gid}", [], False, lock, None, 1.0,
                  crunch=crunch)


def cands(**games):
    return {gid: Candidate(s if isinstance(s, Scored) else sc(gid, s), f"youtubetv:{gid}") for gid, s in games.items()}


def setup(cfg, clock, on=True, games=("a", "b", "c")):
    cfg.update({"jump_mode": {"enabled": on}})
    delays = DelayBook({"youtubetv": MU}, sigma_default=15.0, clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    dec.deadline_cost = lambda target: 4.0   # the hidden stick has it: one input flip
    for gid in games:
        for v in (MU - 0.5, MU, MU + 0.5):
            delays.calibrate(f"youtubetv:{gid}", v)
        dec.timeline(gid, League.CFB)._lags.append(3.0)   # a measured 3 s feed lag
    dec.current, dec.since = "a", clock.t
    return dec


def play(dec, gid, seen, big=False, pbp=False):
    tl = dec.timelines[gid]
    tl.events.append(Event(seen, "play", "play", big=big))
    if pbp:   # the play-by-play's mark at the whistle (cut timing's dead windows)
        tl.dead.add(f"{gid}{seen}", seen, seen, "rush", "run for 3", "Rush", None, 1)


def fresh(dec, clock):
    for tl in dec.timelines.values():
        tl.fresh_at = clock.t
        tl.dead.seen(clock.t, 3.0)


def test_jumps_at_the_dead_ball_after_the_play_budget(cfg, clock):
    dec = setup(cfg, clock)
    t0 = clock.t
    play(dec, "a", t0 + 10)
    play(dec, "a", t0 + 40)
    play(dec, "b", t0 + 50)
    play(dec, "c", t0 + 52)
    clock.t = t0 + 100        # a's second play finished airing at t0 + 75: dead ball on screen
    fresh(dec, clock)
    d = dec.decide(cands(a=70, b=50, c=40))
    assert d.action == "switch" and d.kind == "jump" and d.target == "b", d.reason
    assert d.jump["why"] in ("play_budget", "hot_target") and d.jump["plays_seen"] == 2
    assert dec.current == "b" and dec.jump_left_at["a"] == clock.t


def test_never_leaves_while_a_play_is_on_screen(cfg, clock):
    dec = setup(cfg, clock)
    t0 = clock.t
    play(dec, "a", t0 + 10)
    play(dec, "a", t0 + 40)
    play(dec, "a", t0 + 60)   # snapped at t0+50 real: on screen t0+85..t0+95
    play(dec, "b", t0 + 50)
    clock.t = t0 + 90
    fresh(dec, clock)
    d = dec.decide(cands(a=70, b=50))
    assert d.kind != "jump" and d.target in ("a", None)
    assert dec.jump_holds["play_on_screen"] >= 1


def test_never_joins_a_game_mid_play(cfg, clock):
    dec = setup(cfg, clock, games=("a", "b"))
    t0 = clock.t
    play(dec, "a", t0 + 10)
    play(dec, "a", t0 + 40)
    play(dec, "b", t0 + 68)   # b's play is on screen t0+93..t0+103: we'd land in it
    clock.t = t0 + 100
    fresh(dec, clock)
    d = dec.decide(cands(a=70, b=50))
    assert d.kind != "jump"
    assert dec.jump_holds["no_game_at_dead_ball"] >= 1
    assert not dec.jump_lands_dead("b", cands(b=50)["b"], clock.t, 4.0)


def test_crunch_time_and_a_scoring_drive_keep_the_screen(cfg, clock):
    for scored in (sc("a", 70, crunch=True), sc("a", 70, lock="red zone drive")):
        dec = setup(cfg, clock)
        t0 = clock.t
        play(dec, "a", t0 + 10)
        play(dec, "a", t0 + 40)
        play(dec, "b", t0 + 50)
        clock.t = t0 + 100
        fresh(dec, clock)
        d = dec.decide({"a": Candidate(scored, "youtubetv:a"), **cands(b=50)})
        assert d.kind != "jump"
        assert dec.jump_holds["crunch"] + dec.jump_holds["rz_drive"] == 1


def test_one_play_then_a_hot_game_jumps(cfg, clock):
    dec = setup(cfg, clock, games=("a", "b"))
    t0 = clock.t
    play(dec, "a", t0 + 40)
    play(dec, "b", t0 + 50, big=True)   # a big play in b that just aired
    clock.t = t0 + 100
    fresh(dec, clock)
    d = dec.decide(cands(a=70, b=50))
    assert d.kind == "jump" and d.target == "b" and d.jump["big"] and d.jump["plays_seen"] == 1


def test_flag_off_is_the_normal_decider(cfg, clock):
    dec = setup(cfg, clock, on=False)
    t0 = clock.t
    play(dec, "a", t0 + 10)
    play(dec, "a", t0 + 40)
    play(dec, "b", t0 + 50)
    clock.t = t0 + 100
    fresh(dec, clock)
    d = dec.decide(cands(a=70, b=50, c=40))
    assert d.kind != "jump" and not dec.jump_holds


def test_rotation_comes_back_to_the_game_away_longest(cfg, clock):
    dec = setup(cfg, clock)
    dec.jump_left_at = {"b": clock.t - 10, "c": clock.t - 240}
    # c has a lower score but has been away 4 minutes: 40 + 24 > 50 + 1
    assert dec.jump_next(cands(a=70, b=50, c=40)) == "c"
    # the standby already on b keeps it unless c beats it by standby_margin (8)
    dec.jump_left_at = {"b": clock.t - 10, "c": clock.t - 100}
    assert dec.jump_next(cands(a=70, b=50, c=40), standby="youtubetv:b") == "b"


def test_three_game_slate_rotates_and_never_joins_mid_play(cfg, clock):
    """Ten minutes of three college games (a snap every ~38-44 s each, logged 3 s late), a tick every 2 s, Nick's
    live setting decider.leave.low_leverage [cfb]: the rotation jumps well over once a minute, visits every game,
    and no picture ever comes up on a play (Rule #1 for joins)."""
    cfg.update({"replay": {"enabled": False}, "decider": {"leave": {"low_leverage": ["cfb"]}}})
    dec = setup(cfg, clock)
    cfg.data["decider"]["entry"]["rewind"] = False   # every picture at the live edge (not a settable key)
    t0 = clock.t
    snaps = {g: [t0 + off + i * per for i in range(20)] for g, off, per in
             (("a", 5, 40), ("b", 18, 38), ("c", 31, 44))}   # each play runs [snap, snap + 6] in real life
    logged = {g: 0 for g in snaps}
    jumps = []
    while clock.t < t0 + 600:
        clock.t += 2
        for g, ts in snaps.items():
            while logged[g] < len(ts) and ts[logged[g]] + 6 + 3 <= clock.t:   # logged at the whistle, 3 s lag
                play(dec, g, ts[logged[g]] + 6, pbp=True)
                logged[g] += 1
        fresh(dec, clock)
        d = dec.decide(cands(a=70, b=55, c=45))
        if d.kind != "jump":
            continue
        jumps.append(d)
        on_screen = lambda g, t: any(s + MU - 1 <= t <= s + 6 + MU + 1 for s in snaps[g])   # noqa: E731
        # the picture comes up 4 s after the decision and must stay dead for land_dead_s (2 s)
        assert not any(on_screen(d.target, clock.t + x) for x in (4.0, 5.0, 6.0)), ("joined mid-play", d.jump)
    assert len(jumps) / 10 >= 1.2, len(jumps)
    assert {d.target for d in jumps} == {"a", "b", "c"}
    assert all(d.jump["why"] and "holds" in d.jump for d in jumps)

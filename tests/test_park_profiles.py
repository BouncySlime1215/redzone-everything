"""The honest feed, N streams, the device profiles and the show delay of the PARK simulator, on tiny hand-made nights.

Why: the two-stream simulator read the end of every dead ball 27 s ahead, which the real feed cannot do, and Chrome can
hold more than one spare stream; before anyone reads a pace number from the new modes, each promise they make (nothing
used before a row shows it, the guards, the step times, and old runs unchanged) has to be checked on nights small enough
to read by eye. No archive, no TV, no browser, no training: every game here is a few typed lists.
"""
import hashlib
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_plan as pp  # noqa: E402
import park_profiles as pf  # noqa: E402
import park_sim as ps  # noqa: E402

T0 = 1000          # every hand-made game tips off here
NO_ERR = dict(pos_sd=0.0, delay_sd=0.0, stamp_sd=0.0, key_sd=0.0, pad_k=0.0)


def game(gid, end, live, *, shots=(), v2=(), big=(), hl=(), lev=1.0, rows=None, extra=()):
    """One game's facts as park_ceiling.truth_of gives them. `live` = [(start, end), ...]; the rest of T0..end is dead ball.
    Play rows (`lev`): one per shot, one per whistle (the end of each live stretch) and one per `extra` second, each with
    the game clock that has run by then. rows=False gives the single row the older tests use."""
    dead, prev = [], T0
    for a, b in live:
        if a > prev:
            dead.append((prev, a))
        prev = b
    if end > prev:
        dead.append((prev, end))

    def clock(t):
        return 2880.0 - sum(max(0, min(t, b) - a) for a, b in live)
    stamps = sorted(set(shots) | {b for _, b in live} | {T0, end} | set(extra)) if rows is None else rows
    feed = [(T0, lev, 5, 600.0)] if rows is False else [(t, lev, 5, clock(t)) for t in stamps]
    return {"gid": gid, "t0": T0, "t1": end, "dead": dead, "brk": [False] * len(dead), "shots": list(shots), "big": sorted(big),
            "hl": list(hl), "lev": feed, "v2": sorted((t, v, 0.0) for t, v in v2)}


def random_games(seed, games=3, minutes=30, lengths=(6, 9, 14, 20, 24, 35), rows=None, hl_p=0.08):
    """Games with dead balls of every length, shots, graded plays, a must-see or two and high-leverage spans. A live
    stretch of 14 s or more has a shot 6 s before its whistle, so its restart is first shown `length - 6` s late."""
    rng = random.Random(seed)
    out = []
    for g in range(games):
        t, live, shots, v2, big, hl = T0 + rng.randrange(0, 20), [], [], [], [], []
        end = T0 + minutes * 60
        while t < end - 60:
            d = rng.choice(lengths)
            a, b = t, min(end - 30, t + d)
            live.append((a, b))
            if d >= 14:
                sh = a + d - 6
                shots.append(sh)
                if rng.random() < 0.4:
                    v2.append((sh, rng.choice([1.0, 2.0, 3.0])))
                if rng.random() < 0.04:
                    big.append((sh, 5.0))
                if rng.random() < hl_p:
                    hl.append((sh - 7, sh))
            t = b + rng.choice([3, 5, 8, 12, 30, 45, 70])
        out.append(game(f"g{g}", end, live, shots=shots, v2=v2, big=big, hl=hl, lev=rng.choice([0.7, 1.0, 1.6]), rows=rows))
    return out


def setting(device=None, guard=2, **kw):
    """A planner setting with no timing error at all (what the app believes is what is true), on a device profile."""
    base = dict(route="tune", ping_pong=True, land_wait_max=999.0, max_behind=315.0, guard=float(guard), shots=True, **NO_ERR)
    if device:
        base.update(ps.profile(device))
        base.update(NO_ERR)
    base.update(kw)
    return ps.Setting(**base)


def play(night, s, plan=None, until=None):
    """Run the planner one second at a time and keep every flip with where it left and where it landed (true positions on
    the night's grid) and the state one second after it."""
    pl = pp.Planner(plan or pp.Plan(jump_bonus=0.5))
    A = night.arrs(s.shots, s.value, s.live_w)
    a, b = night.span
    b = b if until is None else min(b, until - night.base)
    brain = ps.Foresight()
    st, S = ps.run(night, brain, s, t0=a, t1=a + 1, planner=pl)
    cuts = []
    for t in range(a + 1, b):
        sg, sb = S.sg, S.sb
        n_before = len(st.hops)
        st, S = ps.run(night, brain, s, t0=t, t1=t + 1, state=S, stats=st, planner=pl)
        if len(st.hops) > n_before:
            cuts.append({"t": t, "from": sg, "leave_i": int(t - sb), "to": S.sg, "land_i": int(S.s_from), "behind": S.sb,
                         "left_ready": S.h_ready, "left_at": int(S.h_ready - S.hb) - 1, "up": S.black})
    return st, S, cuts, A, pl


def padded(x, i, guard):
    calm = x.hlc[min(x.n, i + guard + 1)] - x.hlc[max(0, i - guard)] == 0
    return x.since[i] > guard and x.left[i] >= guard and calm


def digest(st):
    return hashlib.sha1(repr((st.hops, st.flips, st.sched, st.firm, st.ups, [round(b, 6) for b in st.behind],
                              st.mid_entry, st.mid_leave, st.hl_leave)).encode()).hexdigest()[:16]


# ---- defaults: the old two-stream run, second for second ---------------------------------------------------------------

@pytest.mark.parametrize("seed, err, bonus, want", [(3, "spec", 0.5, "814f6885ca54df7c"), (5, "middle", 2.0, "3fc02474e1d0a8d5"),
                                                    (5, "today", 0.5, "4f4184660f80d5c9")])
def test_defaults_reproduce_the_old_two_stream_planner(seed, err, bonus, want):
    """The digests were taken from park_sim and park_plan as they stood on branch `park` before this change: every flip,
    every scheduled jump, every second's distance behind live."""
    night = ps.Night(random_games(seed, rows=False))
    st, _ = ps.run(night, ps.Foresight(), pp.setting(err, True, seed=seed), planner=pp.Planner(pp.Plan(jump_bonus=bonus)))
    assert len(st.hops) >= 10 and digest(st) == want


def test_defaults_reproduce_the_old_rule_set_controller():
    night = ps.Night(random_games(6, rows=False))
    st, _ = ps.run(night, ps.Hindsight(), ps.Setting(seed=6, bore_w=0.05, pos_sd=0.3, delay_sd=1.0, pad_k=4.0))
    assert len(st.hops) == 30 and digest(st) == "8e8e2ce0248460b1"


def test_the_new_settings_are_off_by_default_and_need_the_planner():
    s = ps.Setting()
    assert (s.restart_known, s.streams, s.show_delay, s.open_wait, s.min_guard) == ("lead", 2, 0.0, 0.0, 1)
    assert s.park_confirm_s == s.left_pause_s == s.jump_done_s == s.seek_s == s.seek_over_s == 0.0
    night = ps.Night(random_games(1, minutes=10))
    for kw in ({"restart_known": "rows"}, {"streams": 4}, {"show_delay": 45.0}, {"seek_s": 0.46}, {"restart_known": "soon"}):
        with pytest.raises(ValueError):
            ps.run(night, ps.Hindsight(), ps.Setting(**kw))


# ---- the honest feed ---------------------------------------------------------------------------------------------------

def stoppage_game():
    """A whistle at 1100, play back on at 1120, and the first row after it a FOUL at 1130 (a whistle, not a shot): the
    clock has run 10 s. Then a dead ball to 1160, a shot at 1175 and a whistle at 1180. A substitution row at 1105."""
    return game("a", 1400, [(1000, 1100), (1120, 1130), (1160, 1180), (1200, 1400)], shots=[1175], extra=[1105])


def test_a_restart_is_known_only_when_a_later_row_shows_the_clock_ran():
    night = ps.Night([stoppage_game(), game("b", 1400, [(1000, 1400)])])
    x = night.arrs(True)[0]
    b = night.base
    bf, bt, rv = x.feed(0)
    # the dead ball 1100-1120: its end is known when the foul row (1130) is in, not before, and not from the sub at 1105
    assert all(rv[i - b] == 1130 - b for i in range(1100, 1120))
    # the dead ball 1130-1160: known at the shot row, 15 s after play restarted
    assert all(rv[i - b] == 1175 - b for i in range(1130, 1160))
    tab = pp.Tab(x, 2)
    k = tab.le.index(1120 - b)
    assert tab.reveals()[k] == 1130 - b and tab.reveals()[k + 1] == 1175 - b
    assert all(r >= e for r, e in zip(tab.reveals(), tab.le))          # never before the restart itself


def test_the_open_stoppage_hole_stays_closed():
    """Seconds 1120-1129 are live, and until the foul row the feed has only the whistle at 1100. Read the grader's way they
    are "still stopped". With open_wait 0 that belief is allowed (and is wrong: the sim counts what it costs). It ends the
    moment a row shows the clock ran, even though that row is a whistle and no live play row has followed. With the
    grader's own 26 s bound it is never held at all."""
    night = ps.Night([stoppage_game(), game("b", 1400, [(1000, 1400)])])
    x = night.arrs(True)[0]
    b = night.base
    bf, bt, _ = x.feed(0)

    def believed(i, k, feed):
        return feed[0][i - b] <= k - b < feed[1][i - b]
    assert believed(1124, 1126, (bf, bt)) and not x.legal[1124 - b]            # wrong, and held only before the row
    for k in range(1130, 1300):
        assert not any(believed(i, k, (bf, bt)) for i in range(1120, 1130))    # the foul row is in: possibly over, never legal
    for i in range(1100, 1120):
        assert believed(i, i, (bf, bt))                                        # the real dead ball is believed at once
    strict = x.feed(26)[:2]
    assert not any(believed(i, k, strict) for i in range(1120, 1130) for k in range(1100, 1300))
    assert all(strict[0][i - b] == 1130 - b for i in range(1104, 1120))        # the real one waits for the row


def twin(games, x):
    """The same night as far as every row stamped before second x tells, and nothing after: a restart whose first row
    comes at x or later never happened, and a stretch of play in progress never ends."""
    out = []
    for g in games:
        dead, shots = [], [s for s in g["shots"] if s < x]
        rows = [r for r in g["lev"] if r[0] < x]
        last_row = max(r[0] for r in rows)
        for a, b in g["dead"]:
            if a >= x:
                break
            later = [r[0] for r in g["lev"] if r[0] >= b]          # the row that shows this restart
            if b > last_row or not later or min(later) >= x:
                dead.append((a, g["t1"]))                           # not shown before x: on the twin it never restarts
                break
            dead.append((a, b))
        out.append(dict(g, dead=dead, brk=[False] * len(dead), shots=shots, lev=rows, hl=[],
                        v2=[v for v in g["v2"] if v[0] < x], big=[v for v in g["big"] if v[0] < x]))
    return out


@pytest.mark.parametrize("device, kw", [(None, {}), ("sticks", {}), ("chrome", {"streams": 3}), ("chrome", {"show_delay": 30.0})])
def test_honest_mode_uses_nothing_a_reported_row_has_not_shown(device, kw):
    """Two nights whose play rows are the same up to second X. On the second one nothing happens after X: a restart the
    first night only shows later never took place. Until the feed reaches X the planner must do exactly the same on both.
    (The same check fails in `lead` mode, which is the point: it reads the restart itself.)"""
    base = [dict(g, hl=[]) for g in random_games(7, games=3, minutes=25)]
    s = setting(device, restart_known="rows", **kw)
    flips = 0
    for x in (T0 + 500, T0 + 800, T0 + 1100):
        until = x - int(s.feed_lead) - 1          # the last second whose feed (now + feed_lead) holds no row from X on
        r1 = play(ps.Night(base), s, until=until)
        r2 = play(ps.Night(twin(base, x)), s, until=until)
        assert [(c["t"], c["from"], c["to"], c["leave_i"], c["land_i"]) for c in r1[2]] == [(c["t"], c["from"], c["to"], c["leave_i"], c["land_i"]) for c in r2[2]]
        assert r1[0].sched == r2[0].sched and r1[4].cmds == r2[4].cmds and r1[4].go == r2[4].go
        assert ps.hidden_of(r1[1]) == ps.hidden_of(r2[1]) and (r1[1].sg, r1[1].sb) == (r2[1].sg, r2[1].sb)
        flips += len(r1[2])
    assert flips >= 10          # it did jump: the test is not passing on an idle planner


def test_lead_mode_does_read_the_restart_ahead_of_the_rows():
    """The contrast: on the same twins the old reading does not behave the same, because it sees the restart itself."""
    base = [dict(g, hl=[]) for g in random_games(7, games=3, minutes=25)]
    s, differs = setting(None), 0
    for x in range(T0 + 500, T0 + 1100, 50):
        until = x - int(s.feed_lead) - 1
        r1, r2 = play(ps.Night(base), s, until=until), play(ps.Night(twin(base, x)), s, until=until)
        differs += [(c["t"], c["to"]) for c in r1[2]] != [(c["t"], c["to"]) for c in r2[2]] or r1[4].cmds != r2[4].cmds
    assert differs >= 1


# ---- the guards, on every profile ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("device, kw", [("sticks", {}), ("chrome", {}), ("chrome", {"streams": 2}), ("chrome", {"show_delay": 45.0}),
                                        ("chrome", {"show_delay": 90.0, "streams": 8}), ("sticks", {"late_cap": 60.0})])
def test_guards_hold_on_every_profile(device, kw):
    """No leave inside a padded high-leverage span, no landing or leave outside a padded legal window, nothing over the
    lateness cap, nothing closer to live than the show delay. The restarts here are all shown within 24 s, so with the
    grader's 26 s bound no belief is wrong and every cut can be checked against the facts."""
    flips = 0
    for seed in (1, 2):
        night = ps.Night(random_games(seed, lengths=(6, 9, 14, 20, 24, 30)))
        s = setting(device, restart_known="rows", open_wait=26.0, **kw)
        st, S, cuts, A, _ = play(night, s)
        for c in cuts:
            xs, xh = A[c["from"]], A[c["to"]]
            assert padded(xs, c["leave_i"], 2) or c["leave_i"] > xs.a1 + 2, c
            assert xh.since[c["land_i"]] > 2 and xh.left[c["land_i"]] >= 2, c
            assert not xs.hl[c["leave_i"]], c
            assert s.show_delay <= c["behind"] <= s.late_cap, c
        assert st.mid_entry == st.mid_leave == st.hl_leave == 0
        assert s.show_delay <= min(st.behind) and max(st.behind) <= s.late_cap
        flips += len(cuts)
    assert flips >= 8


def test_the_padding_floor_of_the_profiles():
    """With an exact position the padding rule gives 1 s at the best timing; the profiles never run under 2 s."""
    night = ps.Night(random_games(1, minutes=10))
    for device, floor in (("chrome", 2), ("sticks", 2)):
        pl = pp.Planner()
        ps.run(night, ps.Foresight(), pp.setting("spec", True, **ps.profile(device)), t0=night.span[0], t1=night.span[0] + 2, planner=pl)
        assert pl.G == floor
    pl = pp.Planner()
    ps.run(night, ps.Foresight(), pp.setting("spec", True, pos_sd=0.0), t0=night.span[0], t1=night.span[0] + 2, planner=pl)
    assert pl.G == 1          # the rule alone, as it was


# ---- N streams -----------------------------------------------------------------------------------------------------------

def test_four_streams_hold_four_games_and_stop_tuning():
    night = ps.Night(random_games(4, games=4))
    two = play(night, setting("chrome", restart_known="rows", streams=2))
    four = play(night, setting("chrome", restart_known="rows", streams=4))
    st, S = four[0], four[1]
    held = [h[0] for h in ps.hidden_of(S)]
    assert len(held) == 3 and sorted(held + [S.sg]) == [0, 1, 2, 3]          # one stream per game, none shared
    assert st.parks < two[0].parks                                           # so it tunes far less than one spare stream does
    assert len({c["to"] for c in four[2]}) == 4 and len(four[2]) >= 10       # and it lands on every game
    assert st.mid_entry == st.mid_leave == st.hl_leave == 0


def test_more_streams_than_games_leaves_the_spare_ones_idle():
    night = ps.Night(random_games(4, games=3, minutes=15))
    st, S, cuts, _, pl = play(night, setting("chrome", restart_known="rows", streams=8))
    held = [h[0] for h in ps.hidden_of(S)]
    assert len(held) == 7 and sorted(g for g in held if g >= 0) == sorted({0, 1, 2} - {S.sg})
    assert len(pl.cmds) == 7 and len(cuts) >= 5


def lined_up():
    """Three games whose dead balls line up, each with graded shots (8 points in all before second 1260)."""
    g0 = game("a", 1500, [(1000, 1100), (1130, 1200), (1215, 1300), (1330, 1500)], shots=[1190, 1290], v2=[(1190, 2.0)])
    g1 = game("b", 1500, [(1000, 1080), (1120, 1150), (1180, 1260), (1290, 1500)], shots=[1140, 1250], v2=[(1140, 3.0)])
    g2 = game("c", 1500, [(1000, 1090), (1140, 1165), (1195, 1280), (1310, 1500)], shots=[1155, 1270], v2=[(1155, 3.0)])
    return ps.Night([g0, g1, g2])


def test_looks_queue_back_to_back_on_parked_streams():
    """With three streams the second and third looks are already parked, so every graded shot is seen; one spare stream
    has to be tuned and played to each spot, and misses one."""
    night = lined_up()
    two = play(night, setting("chrome", restart_known="rows", streams=2), pp.Plan(jump_bonus=1.0), until=1260)
    three = play(night, setting("chrome", restart_known="rows", streams=3), pp.Plan(jump_bonus=1.0), until=1260)
    assert len({c["to"] for c in three[2]}) == 3
    assert three[0].v2_seen == pytest.approx(8.0) and two[0].v2_seen == pytest.approx(6.0)
    assert three[0].mid_entry == three[0].mid_leave == three[0].hl_leave == 0


def test_a_wrong_belief_about_an_open_stoppage_is_counted_not_hidden():
    """Game "a" stops at 1200 and restarts at 1215, and its next row is a shot at 1290. With one spare stream the planner
    is on that game at 1251, the feed (27 s ahead) still shows no row since the whistle, so it believes the dead ball is
    still on and leaves: mid-play. The cut is judged at the true position and counted. Nothing in the timing is wrong
    here; only the feed is honest. (No wait bound helps when a row is 75 s late; the hole test covers the 26 s bound.)"""
    night = lined_up()
    st, S, cuts, A, _ = play(night, setting("chrome", restart_known="rows", streams=2), pp.Plan(jump_bonus=1.0), until=1260)
    bad = [c for c in cuts if not A[c["from"]].legal[c["leave_i"]]]
    assert st.mid_leave == 1 and st.mid_entry == 0 and len(bad) == 1
    assert bad[0]["from"] == 0 and 1215 <= bad[0]["leave_i"] + night.base < 1290


# ---- the device profiles ---------------------------------------------------------------------------------------------------

def test_the_two_profiles_carry_the_measured_numbers():
    c, k = ps.DEVICES["chrome"], ps.DEVICES["sticks"]
    assert (k["streams"], k["tune_s"], k["pause_key_s"], k["flip"], k["park_confirm_s"], k["jump_done_s"], k["seek_s"]) == (2, 2.7, 0.7, 2.06, 2.0, 3.0, 0.0)
    assert (c["streams"], c["resume_s"], c["seek_s"], c["seek_over_s"], c["switch_s"], c["pos_sd"], c["park_confirm_s"]) == (4, 0.28, 0.46, 0.22, 0.3, 0.0, 0.0)
    assert c["flip"] == pytest.approx(c["switch_s"] + c["resume_s"])
    assert set(ps.profile("chrome", streams=8)) <= set(ps.Setting.__dataclass_fields__) and ps.profile("chrome", streams=8)["streams"] == 8
    assert [ps.ticks(v) for v in (0.0, 0.22, 0.46, 0.58, 2.06, 2.7, 3.0)] == [0, 1, 1, 1, 2, 3, 3]


@pytest.mark.parametrize("device, up, left_ready, left_on", [("chrome", 1, 1, 1), ("sticks", 2, 3, 2)])
def test_a_jump_takes_the_profiles_time(device, up, left_ready, left_on):
    """The new picture is up `up` s after the jump starts; the stream that was left plays on `left_on` s, and may be
    jumped to again `left_ready` s after the jump started."""
    night = ps.Night(random_games(3))
    st, S, cuts, _, _ = play(night, setting(device, streams=2))
    assert len(cuts) >= 8
    for c in cuts:
        assert c["up"] - c["t"] == up and c["left_ready"] - c["t"] == left_ready and c["left_at"] == c["leave_i"] + left_on
    gaps = [b["t"] - a["t"] for a, b in zip(cuts, cuts[1:])]
    assert min(gaps) >= max(up, left_ready if device == "sticks" else 1)      # no jump starts before the last is complete


def test_a_park_is_a_seek_on_chrome_and_a_pause_with_a_confirm_on_sticks():
    night = ps.Night(random_games(3))
    for device, cost in (("chrome", 1), ("sticks", 2)):
        s = setting(device, streams=2)
        pl, brain = pp.Planner(pp.Plan(jump_bonus=0.5)), ps.Foresight()
        a, b = night.span
        st, S = ps.run(night, brain, s, t0=a, t1=a + 1, planner=pl)
        seeks = pauses = 0
        for t in range(a + 1, b):
            rolls, hops, was = st.rolls, len(st.hops), (S.hg, S.h_paused, S.h_ready)
            st, S = ps.run(night, brain, s, t0=t, t1=t + 1, state=S, stats=st, planner=pl)
            if len(st.hops) > hops:
                continue
            if st.rolls > rolls and device == "chrome":
                seeks += 1
                assert S.h_ready == t + cost                       # one forward seek: 0.46 s, one grid second
            if S.hg == was[0] and S.h_paused and not was[1] and st.rolls == rolls and t >= was[2]:
                pauses += 1
                assert S.h_ready == (t + cost if device == "sticks" else was[2])      # confirmed 2 s after the freeze | at once
        assert (seeks if device == "chrome" else pauses) >= 1


# ---- the one command -----------------------------------------------------------------------------------------------------

def test_the_profile_table_prints_every_row_and_column(capsys):
    nights = [ps.Night(random_games(2, games=3, minutes=12))]
    rows = pf.table("tiny 3", nights, "spec", delays=(0, 45), streams=(2, 4), open_wait=0.0, bonus=0.5)
    names = [r["row"] for r in rows]
    assert names == ["sticks, lead (old)", "sticks, rows (honest)", "chrome, rows, show delay 0 s, 2 streams", "chrome, rows, show delay 0 s, 4 streams",
                     "chrome, rows, show delay 45 s, 2 streams", "chrome, rows, show delay 45 s, 4 streams"]
    out = capsys.readouterr().out
    for col in ("jumps/min", "5 min", "60 min", "yes-rate", "planned jumps delivered", "graded value seen", "big plays seen",
                "behind live s (median / p90)", "mid-play joins / mid-play leaves / high-leverage leaves"):
        assert col in out
    assert out.count("\n| ") == len(rows) + 1 and "Simulated" in out and "hand-made" not in out
    old = pp.evaluate(nights, pp.setting("spec", True), pp.Plan(jump_bonus=0.5))
    assert rows[0]["jumps_per_min"] == old["jumps_per_min"] and rows[0]["v2_seen"] == old["v2_seen"]      # the old row IS the old run
    assert all(r["behind_med_s"] >= 44 for r in rows[4:]) and all(r["behind_med_s"] < 44 for r in rows[:4])      # (true = believed + error)

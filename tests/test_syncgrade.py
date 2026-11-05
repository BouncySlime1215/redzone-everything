"""[EXP-800/801] The sync grader reads the sticks' own behind-live meter (overseer tests, Nov 5 2:30 AM)."""
from redzone.lab import syncgrade as S

UP0 = 5_000_000.0          # the stick's uptime (ms) at wall 0


def row(t, upd_t, pos_s, state="playing", tune_t=None, pred=None, stick="s1", ch="espn", app="youtubetv"):
    """A probe row at wall t whose media_session was last refreshed at wall upd_t at stream position pos_s."""
    return {"t": t, "stick": stick, "app": app, "channel": ch, "uptime_ms": UP0 + t * 1000,
            "ms": {"state": state, "position": pos_s * 1000, "updated": UP0 + upd_t * 1000, "speed": 1.0},
            "tune_t": tune_t, "pred_behind_s": pred}


def night(pred_after_pause=30.0):
    L = 46803.0
    rows = [row(90, 50, 46000, tune_t=None)]                         # before the tune: another stream
    rows.append(row(102.4, 102.4, L, tune_t=100, pred=0.0))          # landed at the live edge
    rows += [row(110 + k, 102.4, L, tune_t=100, pred=0.0) for k in range(5)]   # extrapolated: E holds
    rows.append(row(200, 200, L + (200 - 102.4), state="paused", tune_t=100, pred=0.0))
    rows.append(row(231, 230, L + (200 - 102.4), tune_t=100, pred=pred_after_pause))  # resumed 30 s behind
    return rows


def test_extrapolation_keeps_E_flat_and_a_30_s_pause_reads_30_s_behind():
    graded, lands = S.meter(night())
    playing = [r for r in graded if r["behind_s"] is not None]
    assert len(lands) == 1 and abs(lands[0]["tune_s"] - 2.4) < 1e-6
    assert all(abs(r["behind_s"]) < 1e-6 for r in playing[:-1])
    assert abs(playing[-1]["behind_s"] - 30.0) < 1e-6
    # Without the uptime extrapolation the raw position would drift 1 s per second.
    r = night()[3]
    assert abs(S.origin(r) - S.origin(night()[1])) < 1e-6


def test_exp800_grades_the_controllers_belief_and_never_invents_absolute_latency():
    out = S.exp800(night(pred_after_pause=0.0))                  # the controller missed the pause
    assert out["stick1|youtubetv"]["behind_err_p95_s"] == 30.0 and out["stick1|youtubetv"]["verdict"] == "PENDING"
    assert out["absolute_latency"]["espn"].startswith("PENDING")
    assert S.exp800(night(), anchors={"espn": 31.5})["absolute_latency"]["espn"] == 31.5


def test_exp801_lands_live_only_near_the_other_tunes_of_that_channel():
    rows = []
    for k in range(22):
        t0 = 1000.0 * (k + 1)
        land = 46803.0 + (8.0 if k in (0, 1) else 0.2 * (k % 3))  # tunes 0 and 1 landed 8 s off the edge
        rows += [row(t0 + 2.0, t0 + 2.0, land, tune_t=t0, stick=f"s{k % 2}")]
    out = S.exp801(rows)
    assert out["graded"] == 22 and out["live_share"] == round(20 / 22, 3) and out["verdict"] == "FAIL"
    assert out["per_app"]["youtubetv"]["tune_to_playing_p50_s"] == 2.0


def test_a_tune_that_never_plays_is_not_a_landing():
    rows = [row(100, 50, 46000), row(105, 50, 46000, state="buffering", tune_t=101), row(140, 140, 46803, tune_t=101)]
    _, lands = S.meter(rows)
    assert lands == []                                            # PLAYING came 39 s later: past LAND_WAIT


def _two_tunes(constant, app="youtubetv"):
    """Two live-edge tunes on one channel 10 min apart. YTTV lands at a constant position; a stream whose
    origin holds lands at a position that grew with the wall clock (constant E). Both must read live."""
    rows = []
    for k, t0 in enumerate((1000.0, 1600.0)):
        land = 46803.0 if constant == "pos" else 46803.0 + (t0 - 1000.0)
        rows.append(row(t0 + 2.0, t0 + 2.0, land, tune_t=t0, stick=f"s{k}", app=app))
    return rows


def test_exp801_reads_live_edge_tunes_10_min_apart_as_live_on_the_pinned_reference():
    out = S.exp801(_two_tunes("pos"))                             # YTTV: pinned to the landing position
    assert out["graded"] == 2 and out["live_share"] == 1.0 and out["verdict"] == "PENDING"
    assert out["ref"]["youtubetv|espn"] == {"ref": "pos_s", "mad_pos_s": 0.0, "mad_E_s": 300.0}
    out = S.exp801(_two_tunes("E", app="prime"))                  # Prime: E until measured
    assert out["live_share"] == 1.0 and out["ref"]["prime|espn"]["ref"] == "E_land"
    # The pin is never re-picked from the data: a YTTV night that holds E instead reads off the edge.
    assert S.exp801(_two_tunes("E"))["live_share"] == 0.0


def test_stick_addresses_never_reach_the_output():
    addr = ["192.168.1.47:5555", "G070VM1234567890"]
    rows = [{**r, "stick": addr[0]} for r in night()] + [{**r, "stick": addr[1], "t": r["t"] + 0.5}
                                                         for r in night()]
    out = str(S.exp800(rows)) + str(S.exp801(rows + _two_tunes("pos")))
    assert all(a not in out for a in addr) and "stick1|youtubetv" in out and "stick2" in out


def test_a_prime_epoch_position_measures_absolute_delay_per_stick_and_a_counter_does_not():
    wall0 = 1_793_900_000.0                                   # a real wall clock, so the epoch is in ms
    epoch = [row(wall0 + k, wall0 + k, wall0 + k - 6.4, app="prime", stick="10.0.0.150:5555", ch="prime_nba")
             for k in range(0, 300, 10)]
    counter = [row(wall0 + k, wall0 + k, 86368.0 + k, app="prime", stick="10.0.0.181:5555", ch="prime_nba")
               for k in range(0, 300, 10)]
    for r in epoch + counter:
        r["uptime_ms"] = UP0 + (r["t"] - wall0) * 1000
        r["ms"]["updated"] = r["uptime_ms"]
    out = S.exp800(epoch + counter)
    assert S.is_epoch(epoch[0]["ms"]) and not S.is_epoch(counter[0]["ms"])
    assert list(out["absolute_by_stick"]) == ["stick1|prime"]
    assert abs(out["absolute_by_stick"]["stick1|prime"]["median_s"] - 6.4) < 1e-3
    assert out["absolute_latency"]["prime_nba"].startswith("PENDING")   # channel anchors stay a separate input

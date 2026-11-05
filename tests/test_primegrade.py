"""[EXP-800 Prime] The diamond plan's Prime tests (A4, B1, B2) graded on simulated media_session rows."""
from redzone.lab import primegrade as P

T0 = 1_793_950_000.0       # wall at the start (a real clock, so Prime positions are epochs in ms)
UP0 = 5_000_000.0


def stream(stick="stickA", delay=6.4, drift=0.0, hours=1.0, every=60.0, jumps=(), pause=None, counter=False):
    """Probe rows every 1 s; media_session re-anchors every `every` s at the content clock c(t), which runs at
    1 + drift. `jumps` = walls where the content clock steps +1 s; `pause` = (wall, seconds)."""
    rows, c, last_t, anchor, state = [], T0 - delay, T0, None, "playing"
    end = T0 + hours * 3600
    t = T0
    while t <= end:
        if state == "playing":
            c += (t - last_t) * (1 + drift)            # content runs only while playing
        if pause and pause[0] <= t < pause[0] + pause[1]:
            if state == "playing":
                state, anchor = "paused", (t, c)
        else:
            if state == "paused":
                state, anchor = "playing", (t, c)
            if anchor is None or (t - anchor[0]) >= every:
                c += sum(1.0 for j in jumps if anchor is not None and anchor[0] < j <= t)
                anchor = (t, c)
        last_t = t
        a_t, a_c = anchor
        pos = (a_c - T0 + 86_000.0) * 1000 if counter else a_c * 1000
        rows.append({"t": t, "stick": stick, "app": "prime", "channel": "tnf", "uptime_ms": UP0 + (t - T0) * 1000,
                     "ms": {"state": state, "position": pos, "updated": UP0 + (a_t - T0) * 1000, "speed": 1.0},
                     "tune_t": None, "pred_behind_s": None})
        t += 1.0
    return rows


def test_a_steady_stream_with_a_fast_content_clock_is_flat_once_drift_is_fitted():
    out = P.flat(stream(drift=0.01))["stick1|prime"]
    assert abs(out["drift_s_per_s"] + 0.01) < 1e-4 and out["jumps"] == 0
    assert out["a4_flat"] == "PASS" and out["b1_flat"] == "PASS"


def test_unexplained_jumps_count_per_playing_hour():
    out = P.flat(stream(jumps=(T0 + 900.5, T0 + 1800.5, T0 + 2700.5)))["stick1|prime"]
    assert out["jumps"] == 3 and out["a4_flat"] == "FAIL" and out["b1_flat"] == "FAIL"
    out = P.flat(stream(jumps=(T0 + 900.5,)))["stick1|prime"]
    assert out["a4_flat"] == "FAIL" and out["b1_flat"] == "PASS"       # 1 per hour is within B1's 2


def test_thin_flat_data_reads_pending_but_a_conclusive_jump_still_fails():
    out = P.flat(stream(hours=0.25, every=60.0))["stick1|prime"]       # 15 quiet steps, a quarter hour
    assert out["a4_flat"] == "PENDING" and out["b1_flat"] == "PENDING"
    out = P.flat(stream(hours=0.25, every=30.0))["stick1|prime"]       # A4's 15 min at 30 s anchors: 30 steps
    assert out["a4_flat"] == "PASS" and out["b1_flat"] == "PENDING"    # B1 needs a playing hour
    out = P.flat(stream(hours=0.25, every=30.0, jumps=(T0 + 100.5, T0 + 300.5, T0 + 500.5)))["stick1|prime"]
    assert out["a4_flat"] == "FAIL" and out["b1_flat"] == "FAIL"       # 3 jumps: already over the hour's 2


def test_a_pause_is_graded_against_the_commanded_length_not_against_itself():
    rows = []
    for k in range(3):                                                 # three sticks' worth of one 20 s pause
        rows += stream(stick=f"stick{k}", hours=0.2, pause=(T0 + 300, 20))
    assert P.flat(rows)["stick1|prime"]["jumps"] == 0                  # the pause is an event, not a jump
    cmds = [{"stick": f"stick{k}", "pause_t": T0 + 300, "play_t": T0 + 320} for k in range(3)]
    assert P.pauses(rows, cmds)["verdict"] == "PASS"
    assert P.pauses(rows, cmds[:2])["verdict"] == "PENDING"            # under MIN_PAUSES
    assert P.pauses(rows, [{**cmds[0], "play_t": T0 + 315}])["verdict"] == "FAIL"   # one off by 5 s is conclusive
    assert P.pauses(rows, [])["verdict"] == "PENDING"


def test_two_sticks_on_one_channel_must_agree_and_never_print_their_ids():
    a = stream(stick="stickA:5555", hours=0.1)
    out = P.sticks_agree(a + stream(stick="stickB:5555", hours=0.1, delay=6.6))
    assert out["tnf|stick1~stick2"]["verdict"] == "PASS" and "stickA" not in str(out)
    assert P.sticks_agree(a + stream(stick="stickB:5555", hours=0.1, delay=7.0))["tnf|stick1~stick2"]["verdict"] == "FAIL"
    thin = P.sticks_agree(a[:20] + stream(stick="stickB:5555", hours=0.1, delay=7.0)[:20])
    assert thin["tnf|stick1~stick2"]["verdict"] == "PENDING"            # 20 pairs, under MIN_PAIRS


def test_epoch_share_tells_an_epoch_stick_from_a_counter_stick():
    out = P.epoch_share(stream(hours=0.05) + stream(stick="stickB", hours=0.05, counter=True))
    assert out["stick1|prime|tnf"]["epoch_pct"] == 100.0 and out["stick2|prime|tnf"]["epoch_pct"] == 0.0


def test_key_moments_needs_five_seeks_within_two_seconds():
    km = [{"clip_epoch_s": 100.0 + d, "feed_wall_s": 100.0} for d in (-3.0, -2.5, -2.2, -1.8, -1.5)]
    assert P.key_moments(km)["verdict"] == "PASS" and P.key_moments(km[:4])["verdict"] == "PENDING"
    assert P.key_moments(km + [{"clip_epoch_s": 100.0, "feed_wall_s": 100.0}])["verdict"] == "FAIL"
    assert P.key_moments([km[0], {"clip_epoch_s": 100.0, "feed_wall_s": 100.0}])["verdict"] == "FAIL"  # 3 s: conclusive


def test_landing_sd_needs_five_prime_landings():
    def tunes(n, wobble):
        rows = []
        for k in range(n):
            t0 = T0 + 600.0 * k
            rows.append({"t": t0 + 2.0, "stick": "stickA", "app": "prime", "channel": "tnf",
                         "uptime_ms": UP0 + (t0 + 2.0 - T0) * 1000, "tune_t": t0, "pred_behind_s": None,
                         "ms": {"state": "playing", "position": (t0 + 2.0 - 6.4 + wobble * (k % 2)) * 1000,
                                "updated": UP0 + (t0 + 2.0 - T0) * 1000, "speed": 1.0}})
        return rows
    assert P.landings(tunes(4, 0.1))["tnf"]["verdict"] == "PENDING"
    assert P.landings(tunes(7, 0.1))["tnf"]["verdict"] == "PASS"
    assert P.landings(tunes(7, 2.0))["tnf"]["verdict"] == "FAIL"

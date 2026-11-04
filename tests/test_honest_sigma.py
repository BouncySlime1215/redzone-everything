"""[auto-anchors] Honest sync doubt in the sims: ad-break anchors, per-tune landing error, honesty grade."""
import random
from types import SimpleNamespace as NS

from redzone.lab import backtest as B
from redzone.models import League


class Cfg(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def play(t, per=1, clk="10:00"):
    return {"type": {"text": t}, "period": {"number": per}, "clock": {"displayValue": clk}}


def test_ad_breaks_follow_official_stoppages_and_nhl_tv_timeouts():
    nfl = B.Replay(League.NFL, "f", {}, [play("Rush"), play("Official Timeout"), play("Pass")], [100.0, 200.0, 300.0])
    nhl = B.Replay(League.NHL, "h", {}, [play("Stoppage", 1, "15:00"), play("Stoppage", 1, "13:30"),
                                         play("Stoppage", 1, "13:00"), play("Stoppage", 1, "9:00")],
                   [10.0, 20.0, 30.0, 40.0])
    cfg = Cfg({"sync.break_anchor.true_gap_s": 15.0, "sync.break_anchor.true_gap_sd_s": 0.0})
    out = B.ad_breaks([nfl, nhl], 50.0, cfg, random.Random(0))
    assert out["f"] == [(200.0, 265.0)]                          # whistle + lag + the gap to the ad
    assert [w for w, _ in out["h"]] == [20.0, 40.0]              # first stoppage past 14:00 and past 10:00


def test_honesty_summary_counts_misses_and_small_doubt():
    h = B.honesty_summary([(1.0, 3.0), (7.0, 3.0), (0.5, 9.0), (2.0, 2.0)])
    assert h["n"] == 4 and h["within_2sigma"] == 0.75 and h["sigma_le_8"] == 0.75 and h["sigma_le_3"] == 0.75
    assert B.honesty_summary([]) == {"n": 0}


def test_anchor_tick_feeds_one_break_matched_like_live():
    got = []
    sync = None
    d = NS(behind=0.0)
    delays = NS(get=lambda t: d, tuned=lambda t: got.append(("tuned", t)),
                anchor=lambda target, z, kind, t, sd: got.append((target, round(z, 1), kind, t, round(sd, 2))))
    sw = [B.SwitchLog(1000.0, None, "g", "chase", "", 2.0, 2.0)]
    cfg = Cfg({"sync.break_anchor.start_bias_s": 15.0, "sync.break_anchor.bias_sd_s": 10.0})
    dec = NS(action="stay", target="g")
    B._anchor_tick(delays, sync, dec, sw, {"g": "yt:g"}, {"g": [(990.0, 1060.0)]}, cfg, 5.0, 1070.0, 10.0)
    # the ad comes up 2 s late (this tune's unknown landing): z = 1062 - 990 - 15
    assert got == [("yt:g", 57.0, "break", 1062.0, 10.05)]
    got.clear()
    B._anchor_tick(delays, sync, dec, sw, {"g": "yt:g"}, {"g": [(990.0, 1060.0)]}, cfg, 5.0, 1080.0, 10.0)
    assert got == []                                             # one anchor per break


def test_noise_cal_learns_the_real_error_and_never_widens():
    from redzone.decider.screen import Delay, NoiseCal
    rng = random.Random(1)
    cal = NoiseCal(min_n=20, min_sigma_s=2.0)
    assert cal.sigma("yt") is None
    for _ in range(400):                          # true error U(0, 6), anchor noise sd 10
        cal.add("yt", rng.uniform(0, 6) + rng.gauss(0, 10), 10.0)
    s = cal.sigma("yt")
    assert 3.0 <= s <= 9.0                         # an upper bound on ~3.5 s rms, honest side
    d = Delay(100.0, 3.0, calibrated_at=0.0)
    d.cal_sigma = s
    assert d.current_sigma(5400.0) == min(9.0, s)  # aging model 3 + 6 = 9, the learned doubt below it
    d.cal_sigma = 50.0
    assert d.current_sigma(5400.0) == 9.0          # never wider than the model


def test_first_anchor_after_a_tune_feeds_the_calibration():
    from redzone.decider.screen import DelayBook, NoiseCal
    book = DelayBook({"youtubetv": 40.0}, 9.0, clock=lambda: 0.0)
    cal = book.attach_cal(NoiseCal(min_n=1, min_sigma_s=2.0))
    book.anchor("youtubetv:g", 45.0, "break", 0.0, 1.0)     # not tuned yet: no residual
    assert not cal.pool
    book.tuned("youtubetv:g")
    book.anchor("youtubetv:g", 45.0, "break", 0.0, 1.0)
    book.anchor("youtubetv:g", 47.0, "break", 1.0, 1.0)     # second one after the same tune: ignored
    assert [round(r) for r, _ in cal.pool["youtubetv"]] == [5]
    assert book.get("youtubetv:g").cal_sigma is not None

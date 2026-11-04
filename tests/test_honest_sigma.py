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
    sync = NS(measure=lambda target, z, kind, t, sd: got.append((target, round(z, 1), kind, t, round(sd, 2))))
    d = NS(behind=0.0)
    delays = NS(get=lambda t: d, tuned=lambda t: got.append(("tuned", t)))
    sw = [B.SwitchLog(1000.0, None, "g", "chase", "", 2.0, 2.0)]
    cfg = Cfg({"sync.break_anchor.start_bias_s": 15.0, "sync.break_anchor.bias_sd_s": 10.0})
    dec = NS(action="stay", target="g")
    B._anchor_tick(delays, sync, dec, sw, {"g": "yt:g"}, {"g": [(990.0, 1060.0)]}, cfg, 5.0, 1070.0, 10.0)
    # the ad comes up 2 s late (this tune's unknown landing): z = 1062 - 990 - 15
    assert got == [("yt:g", 57.0, "break", 1062.0, 10.05)]
    got.clear()
    B._anchor_tick(delays, sync, dec, sw, {"g": "yt:g"}, {"g": [(990.0, 1060.0)]}, cfg, 5.0, 1080.0, 10.0)
    assert got == []                                             # one anchor per break

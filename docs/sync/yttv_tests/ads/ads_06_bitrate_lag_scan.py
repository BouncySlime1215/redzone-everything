#!/usr/bin/env python3
"""ads_06 decoder-bitrate lag scan: commercials change the decoder bitrate; scan D for the best fit.

Hypothesis: MediaCodecLogger prints the video decoder bitrate every 5 s (readable logcat, no DRM
touch). Commercials are encoded differently from the live game (higher motion / different encoder,
sometimes 720p), so the bitrate regime flips at each break start and end. In-game DAI pods are rare
on ESPN (ads_01), but bitrate works for pass-through national ads too. If we shift every real-world
break start (timeout/period-end wall) by a candidate D, the mean log-bitrate step across those
shifted times peaks at the true D.

Procedure (read-only): logcat -d MediaCodecLogger on the stick every 45 s for --minutes (dedup lines),
series (epoch, kbps); for D in 0..90 s step 0.5: score(D) = mean over breaks of
|mean(log kbps in [t, t+30)) - mean(log kbps in [t-30, t))| with t = wall + D, only for breaks while
the stick showed that game. Report the peak D, peak/second-lobe contrast and a per-break table.

Pass rule: >=4 breaks contribute, peak contrast >= 1.5 (best vs best outside +-6 s) and the peak D
lies within 2 s of the ads_02 / app-calibrated D for that tune (logged; compared offline).
"""
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_06_bitrate_lag_scan"


def win(series, a, b):
    v = [math.log(k + 1) for t, k in series if a <= t < b]
    return st.fmean(v) if len(v) >= 3 else None


def analyse(ev, logs, plays, args, out, ctx):
    anc = L.anchors(plays, args.game)
    best_all = None
    for h in ctx["hosts"]:
        series = [(t, k) for t, _, k in L.bitrate_series(logs.get(h, []))]
        if len(series) < 20:
            out.w("skip", stick=h[-8:-5], reason=f"{len(series)} bitrate samples")
            continue
        use = [a for a in anc if L.stick_on_game(ev, h, a["wall"] + 30, a["game"], plays)]
        scores = []
        for i in range(0, 181):
            D = i * 0.5
            vals = []
            for a in use:
                t = a["wall"] + D
                pre, post = win(series, t - 30, t), win(series, t, t + 30)
                if pre is not None and post is not None:
                    vals.append(abs(post - pre))
            if vals:
                scores.append((D, st.fmean(vals), len(vals)))
        if not scores:
            out.w("skip", stick=h[-8:-5], reason=f"no breaks while stick showed the game ({len(use)} anchors)")
            continue
        pk = max(scores, key=lambda s: s[1])
        side = [s[1] for s in scores if abs(s[0] - pk[0]) > 6]
        contrast = pk[1] / max(side) if side and max(side) > 0 else None
        row = out.w("scan", stick=h[-8:-5], breaks=pk[2], D_peak=pk[0], score=round(pk[1], 3),
                    contrast=round(contrast, 2) if contrast else None,
                    curve=[(s[0], round(s[1], 3)) for s in scores[::4]])
        if best_all is None or (row["contrast"] or 0) > (best_all["contrast"] or 0):
            best_all = row
    passed = bool(best_all and best_all["breaks"] >= 4 and (best_all["contrast"] or 0) >= 1.5)
    out.summary(passed, best=best_all and {k: best_all[k] for k in ("stick", "breaks", "D_peak", "contrast")},
                note="compare D_peak to ads_02 / app sync for the same tune before trusting")


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "dump MediaCodecLogger bitrate lines (read-only logcat) and scan D=0..90 s for the shift that "
                    "best lines real break starts up with bitrate steps",
                    log_tags="MediaCodecLogger:I *:S"))

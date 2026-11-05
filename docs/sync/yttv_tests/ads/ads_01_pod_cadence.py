#!/usr/bin/env python3
"""ads_01 pod cadence: YTTV DAI ad pods show up as media_session 'updated' firing once per spot.

Hypothesis: while YTTV stitches a dynamic ad pod, media_session PlaybackState is re-published at
every spot boundary (10/15/30 s grid, measured 10-16 s), while position keeps advancing in real
time. During normal program it is quiet for minutes. So a run of >=2 updates spaced 8-17 s apart is
an ad pod, and its first update is the pod start ON THE STICK (wall clock, sub-second, no polling
error because 'updated' is the stick's own uptime stamp).

Procedure (read-only): poll `dumpsys media_session` + /proc/uptime on the chosen stick every --poll s
for --minutes; wall(update) = boot_epoch + updated/1000; dedupe by 'updated'; find runs (adslib.find_pods).
Report each pod (start, spots, gaps, title, in-game?) and the spot-gap histogram.

Pass rule: >=80% of within-pod gaps sit on the DAI grid (10, 15 or 30 s +-1 s) AND at least one pod
falls inside a live NBA game window on that channel (else verdict 'no_in_game_dai' = this channel
does not replace in-game ads, so pods only anchor studio shows; other ad tests then rely on bitrate).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_01_pod_cadence"


def in_game(pod, plays):
    g = L.game_for_title(pod["title"], plays)
    if not g:
        return None
    ws = [p["wall"] for p in plays if p["game"] == g]
    return g if ws and min(ws) - 600 <= pod["start"] <= max(ws) + 600 else None


def analyse(ev, logs, plays, args, out, ctx):
    pods = L.find_pods(ev)
    gaps, n_game = [], 0
    for p in pods:
        g = in_game(p, plays)
        n_game += bool(g)
        gaps += p["gaps"]
        out.w("pod", stick=p["h"], channel=p["sub"], title=p["title"][:40], start=round(p["start"], 2),
              start_hms=L.hhmmss(p["start"]), spots=p["spots"], gaps=p["gaps"], end_est=round(p["end_est"], 2),
              game=g)
    on_grid = [g for g in gaps if any(abs(g - q) <= 1.0 for q in (10, 15, 30))]
    frac = len(on_grid) / len(gaps) if gaps else 0
    hist = {}
    for g in gaps:
        hist[str(round(g))] = hist.get(str(round(g)), 0) + 1
    passed = bool(gaps) and frac >= 0.8 and n_game > 0
    verdict = "pass" if passed else ("no_pods" if not pods else ("no_in_game_dai" if n_game == 0 else "off_grid"))
    out.summary(passed, verdict=verdict, pods=len(pods), pods_in_game=n_game, gap_on_grid=round(frac, 3),
                gap_hist=dict(sorted(hist.items(), key=lambda kv: int(kv[0]))), gap_stats=L.robust(gaps))


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "poll media_session on the hidden stick every 1 s, timestamp each 'updated' with boot epoch, "
                    "and list DAI pods (runs of 8-17 s updates) with in-game flag"))

#!/usr/bin/env python3
"""ads_07 VPUD resolution switches: sub-second decoder events at ad/program splices?

Hypothesis: the MediaTek video decoder logs `VPUD: Resolution Changed!!BitStream WxH = (1280x720)`
with an ms epoch stamp whenever the stream's coded size changes. Recordings show 720p -> 1080p pairs
~8 s apart, some at tunes (ABR ramp-up) and some with no tune (20:26:00, 20:29:26 on .150). If the
non-tune ones sit at ad splices (DAI spots or commercial slates are often 720p), they are sub-second
break edges: D = vpud_wall - real break wall.

Procedure (read-only): logcat -d VPUD/MediaCodecLogger with -v epoch every 45 s and media_session
polling; classify each resolution change as 'tune' (within 20 s after a media_session position
reset / title change), 'pod' (within 1.5 s of a DAI spot boundary), 'break' (D = event - nearest
earlier real break start in 5..90 s) or 'other'.

Pass rule: >=3 non-tune events and >=60% of them are 'pod' or 'break' with the break D values
within 2 s of each other.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_07_vpud_resolution"


def analyse(ev, logs, plays, args, out, ctx):
    anc = L.anchors(plays, args.game)
    pods = L.find_pods(ev)
    resets = [s[0]["w"] for s in L.segments(ev)]
    cls_n, breakD, nontune = {}, [], 0
    for h in ctx["hosts"]:
        bounds = [b for p in pods if p["host"] == h for b in p["bounds"]]
        my_resets = [w for w, s in zip(resets, L.segments(ev)) if s[0]["host"] == h]
        for t, w, hh in L.res_changes(logs.get(h, [])):
            cls, D = "other", None
            if any(0 <= t - r <= 20 or 0 <= r - t <= 10 for r in my_resets):
                cls = "tune"
            elif any(abs(t - b) <= 1.5 for b in bounds):
                cls = "pod"
            else:
                e = L.showing(ev, h, t)
                g = L.game_cached(e["title"], plays) if e else None
                cands = [t - a["wall"] for a in anc if (g is None or a["game"] == g) and 5 <= t - a["wall"] <= 90]
                if g and cands:
                    cls, D = "break", round(min(cands), 2)
            if cls != "tune":
                nontune += 1
            if D is not None:
                breakD.append(D)
            cls_n[cls] = cls_n.get(cls, 0) + 1
            out.w("event", stick=h[-8:-5], at=L.hhmmss(t), size=f"{w}x{hh}", cls=cls, D=D)
    good = cls_n.get("pod", 0) + cls_n.get("break", 0)
    s = L.robust(breakD)
    passed = nontune >= 3 and good / max(1, nontune) >= 0.6 and (s.get("n", 0) < 2 or s["max"] - s["min"] <= 2)
    out.summary(passed, classes=cls_n, non_tune=nontune, break_D=s)


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "dump VPUD 'Resolution Changed' decoder events (ms epoch) and classify them as tune / DAI "
                    "pod boundary / real break edge (+D) / other"))

#!/usr/bin/env python3
"""ads_08 logcat enrichment: which log tags fire around ad splices (MPB_DRM, AmazonHdmiService, ...)?

Hypothesis: some system component reacts to ad splices: MPB_DRM CodecQuery bursts (format re-checks
when the ad has a different codec/HDR profile), AmazonHdmiService / MtkHdmiService / hwcomposer
(mode or HDR switches), whad-MediaSession onPlaybackStateChanged/onMetadataChanged, netd bandwidth
alerts (new ad segments from a different CDN). A tag that fires >=3x more often within +-10 s of a
known splice than at baseline is a candidate edge detector with its own ms epoch stamp.

Procedure (read-only): logcat -d -v epoch (ALL tags, -t 3000) every 45 s + media_session polling for
--minutes. Splices = DAI spot boundaries (ads_01) plus real break walls + prior D (29 s) as a weak
second set. For every tag: rate_near = hits within +-10 s of a splice / near-seconds, rate_base = hits
elsewhere / other seconds; enrichment = rate_near / rate_base. Also lists the 5 most common message
stems per enriched tag.

Pass rule: at least one tag (excluding whad-MediaSession, which IS the splice source) has >=5 near
hits and enrichment >= 3 -> promote it to its own edge test.
"""
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_08_logcat_enrichment"
WIN = 10.0


def analyse(ev, logs, plays, args, out, ctx):
    pods = L.find_pods(ev)
    anc = L.anchors(plays, args.game)
    results = []
    for h in ctx["hosts"]:
        lines = logs.get(h, [])
        stamped = [(L.log_epoch(x), L.log_tag(x), x) for x in lines]
        stamped = [s for s in stamped if s[0] and s[1]]
        if len(stamped) < 50:
            out.w("skip", stick=h[-8:-5], reason=f"{len(stamped)} log lines")
            continue
        t0, t1 = min(s[0] for s in stamped), max(s[0] for s in stamped)
        splices = sorted({b for p in pods if p["host"] == h for b in p["bounds"]})
        splices += [a["wall"] + 29.0 for a in anc if L.stick_on_game(ev, h, a["wall"] + 29, a["game"], plays)]
        splices = sorted(s for s in splices if t0 <= s <= t1)
        if not splices:
            out.w("skip", stick=h[-8:-5], reason="no splices inside the log span")
            continue
        # near-seconds = union of windows, computed on a 1 s grid (cheap)
        near_set = set()
        for s in splices:
            near_set.update(range(int(s - WIN), int(s + WIN) + 1))
        span = int(t1 - t0) + 1
        near_s = sum(1 for k in near_set if t0 <= k <= t1) or 1
        base_s = max(1, span - near_s)
        near, base, stems = Counter(), Counter(), defaultdict(Counter)
        for t, tag, x in stamped:
            if int(t) in near_set:
                near[tag] += 1
                stems[tag][re.sub(r"[\d.]+", "#", x.split(":", 1)[-1])[:60]] += 1
            else:
                base[tag] += 1
        for tag in set(near) | set(base):
            rn, rb = near[tag] / near_s, (base[tag] + 0.5) / base_s
            results.append(dict(stick=h[-8:-5], tag=tag, near=near[tag], base=base[tag], enrich=round(rn / rb, 2)))
        for r in sorted([r for r in results if r["stick"] == h[-8:-5]], key=lambda r: -r["enrich"])[:10]:
            out.w("tag", **r, stems=stems[r["tag"]].most_common(5))
        out.w("span", stick=h[-8:-5], splices=len(splices), near_s=near_s, base_s=base_s)
    cands = [r for r in results if r["near"] >= 5 and r["enrich"] >= 3 and "whad" not in r["tag"]]
    cands.sort(key=lambda r: -r["enrich"])
    out.summary(bool(cands), candidates=cands[:5], tags_seen=len(results))


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "dump all-tag logcat (read-only) and rank tags by how much more often they fire within +-10 s "
                    "of ad splices than at baseline", log_tags=""))

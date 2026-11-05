#!/usr/bin/env python3
"""ads_09 whad-MediaSession event clock: exact spot-boundary times without polling error.

Hypothesis: Fire OS logs every YTTV PlaybackState change as
`whad-MediaSession: onPlaybackStateChanged: ... position=P ... speed=S, updated=U` with a logcat
epoch stamp. epoch - U/1000 must equal the stick's boot epoch to a few ms, which (a) validates the
boot-epoch conversion every other test uses, and (b) gives each ad-spot boundary as an absolute
wall time to ~10 ms, even when the dumpsys poll misses an update (two updates within one poll).
Counts how many boundaries dumpsys polling missed.

Procedure (read-only): logcat -d -v epoch whad-MediaSession:I every 45 s + dumpsys polling for
--minutes. For each whad line: boot_i = epoch - updated/1000. Compare spread of boot_i and against
boot from /proc/uptime (live) or min(t - updated) (dry-run). Count whad updates absent from the
dumpsys-derived event set.

Pass rule: boot_i spread (MAD) <= 0.02 s and |median boot_i - uptime boot| <= 0.15 s
(dry-run: <= poll period) -> whad lines are a trustworthy ms-precise edge clock.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_09_whad_event_clock"
RX = re.compile(r"^(\d{9,}\.\d+)\s.*whad-MediaSession: onPlaybackStateChanged: (\S+), state=PlaybackState "
                r"\{state=(\d+), position=(-?\d+).*?speed=([\d.]+), updated=(\d+)")


def analyse(ev, logs, plays, args, out, ctx):
    all_ok, rows = [], []
    for h in ctx["hosts"]:
        recs = []
        for x in logs.get(h, []):
            m = RX.search(x)
            if m:
                recs.append((float(m.group(1)), m.group(2)[:20], int(m.group(3)), int(m.group(4)),
                             float(m.group(5)), int(m.group(6))))
        if not recs:
            out.w("skip", stick=h[-8:-5], reason="no whad-MediaSession lines")
            continue
        boots = [r[0] - r[5] / 1000 for r in recs]
        sb = L.robust(boots)
        ref = ctx.get("boot", {}).get(h)
        ref = ref if isinstance(ref, (int, float)) else (ref[0] if ref else None)
        upd_polled = {round(e["w"] - sb["median"], 1) for e in ev if e["host"] == h}
        whad_upd = {round(r[5] / 1000, 1) for r in recs if r[2] == 3 and r[3] > 0}
        missed = len([u for u in whad_upd if u not in upd_polled])
        row = out.w("stick", stick=h[-8:-5], lines=len(recs), boot_whad=sb, boot_ref=ref,
                    diff_ref=round(sb["median"] - ref, 3) if ref else None,
                    playing_updates=len(whad_upd), missed_by_poll=missed,
                    sessions=len({r[1] for r in recs}))
        rows.append(row)
        all_ok.append(sb["mad"] <= 0.02 and (row["diff_ref"] is None or abs(row["diff_ref"]) <= 0.15))
    out.summary(bool(all_ok) and all(all_ok), sticks=len(rows),
                note="dry-run boot_ref is itself the whad median when whad lines exist (tautological); "
                     "live run compares with /proc/uptime")


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "dump whad-MediaSession onPlaybackStateChanged lines (read-only) and check epoch - updated "
                    "== boot epoch to ms, counting spot boundaries the dumpsys poll missed",
                    log_tags="whad-MediaSession:I *:S"))

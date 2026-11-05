#!/usr/bin/env python3
"""ui_11 Zero-key D monitor: D_epg(t) = E(t) - C, with C learned from UI anchors (ui_05 flips / ui_02 label).

HYPOTHESIS: C = (YTTV EPG wall clock of the playhead) - media_position is constant for one stream
session (a deep-link re-tune starts a new session with a new position epoch -> re-anchor) (recorded 2026-11-04 .150 ESPN: C_flip = 1793770580.0
+- 0.1 s across 5 hourly flips). Then the delay vs YTTV's clock is available at any time, read-only:
    D_epg(t) = E(t) - C       (E from dumpsys media_session; no keys, no screenshots)
and the delay vs the REAL world is D = D_epg + U, where U (upstream: venue -> YTTV ingest/EPG clock) is a
per-channel constant to be calibrated once against plays (e.g. ESPN prior 29 s vs D_epg 18 s -> U ~ 11 s,
UNVERIFIED). This test checks that D_epg is flat while playing and steps exactly by pause lengths, and
logs it beside the app's sync filter so the fusion family can calibrate U.

PROCEDURE (read-only, either stick): C from --C, else the newest C_flip (ui_05) / C (ui_02) row in
results for this stick+channel. Poll media_session every --poll s; log E, D_epg, title, state.
A title change re-anchors C (logs the new C_flip vs the old).

PASS: during >= 10 min of uninterrupted play sd(D_epg) <= 0.3 s, and every re-anchor agrees with the
  prior C within 1.0 s (i.e. C really is constant).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_11_epg_clock_monitor"


def extra(ap):
    ap.add_argument("--C", type=float, default=None)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--upstream", type=float, default=None, help="U (s) if calibrated; adds D_real column")


def newest_C(host, channel):
    best = None
    for f in ("ui_05_title_flip_epg.jsonl", "ui_02_scrub_label.jsonl"):
        for r in U.load_jsonl(U.RESULTS / f):
            if r.get("dry"):
                continue
            c = r.get("C_flip") or r.get("C")
            if c and (r.get("host") or r.get("stick")) == host and \
                    str(r.get("channel", "")).lower().replace(" ", "") == channel:
                if best is None or r["t"] > best[0]:
                    best = (r["t"], c)
    return best[1] if best else None


def live(args, g, out):
    from redzone.tuners.adb import parse_playback
    C = args.C or newest_C(g.host, args.channel)
    out.write("anchor", C=C, source="arg" if args.C else "results")
    Ds, last_title, last_upd = [], None, None
    t_end = time.time() + args.minutes * 60
    while time.time() < t_end:
        try:
            t0 = time.time(); txt = U._sh(g.host, "cat /proc/uptime; dumpsys media_session"); t1 = time.time()
            boot = (t0 + t1) / 2 - float(txt.split()[0]); b = parse_playback(txt, U.PKG)
        except Exception as e:  # noqa: BLE001
            out.write("err", err=repr(e)); U.sleep_poll(args.poll); continue
        if b.get("position") is None:
            U.sleep_poll(args.poll); continue
        E = boot + (b["updated"] - b["position"]) / 1000 if b["state"] == "playing" else None
        if last_title and b.get("title") != last_title and b.get("updated") != last_upd:
            tf = boot + b["updated"] / 1000
            bd = round(tf / 1800) * 1800
            Cn = bd - b["position"] / 1000
            out.write("reanchor", title=b.get("title"), C_new=round(Cn, 2), C_old=C,
                      dC=round(Cn - C, 2) if C else None, D_flip=round(tf - bd, 2))
            if C is None and abs(tf - bd) < 120:
                C = Cn
        last_title, last_upd = b.get("title"), b.get("updated")
        row = dict(st=b["state"], E=E and round(E, 2), title=b.get("title"), sub=b.get("subtitle"))
        if E and C:
            row["D_epg"] = round(E - C, 2); Ds.append(row["D_epg"])
            if args.upstream is not None:
                row["D_real"] = round(row["D_epg"] + args.upstream, 2)
        out.write("tick", **row)
        U.sleep_poll(args.poll)
    s = U.summarize(Ds)
    return dict(D_epg=s, C=C, pass_=bool(s.get("n", 0) >= 300 / max(args.poll, 0.5) and s.get("sd", 9) <= 0.3))


def dry(args, out):
    """Replay 2026-11-04 HDMI1 ESPN: boot from ui_05's estimator, C from its flips."""
    host, date = U.HOSTS["HDMI1"], args.date or "2026-11-04"
    rows = [r for r in U.load_probe(date, True, host) if (r.get("ms") or {}).get("updated") is not None
            and (r.get("ms") or {}).get("subtitle") == "ESPN"]
    if not rows:
        return dict(err="no rows", pass_=None)
    cands = [r["t"] - r["ms"]["updated"] / 1000 for r in rows]
    med = sorted(cands)[len(cands) // 2]
    rows = [r for r, c in zip(rows, cands) if abs(c - med) < 3600]
    boot = min(r["t"] - r["ms"]["updated"] / 1000 for r in rows)
    C = args.C or 1793770580.0
    by = {}
    for r in rows:
        m = r["ms"]
        if m.get("state") != "playing":
            continue
        D = boot + (m["updated"] - m["position"]) / 1000 - C
        by.setdefault(m.get("title"), []).append(D)
    for t, v in by.items():
        out.write("title", title=t, D_epg=U.summarize(v))
    allD = [d for v in by.values() for d in v]
    from collections import Counter
    mode = Counter(round(d) for d in allD).most_common(1)[0][0]
    core = [d for d in allD if abs(d - mode) <= 1.0]
    s = U.summarize(core)
    return dict(host=host, date=date, boot_est=round(boot, 2), C=C, D_epg_core=s, core_frac=round(len(core) / len(allD), 3),
                note="C holds for one stream session: 5 h of ESPN at D_epg 18.13 s (sd 0.01); rows after the 19:13 "
                     "re-tunes belong to new sessions (new C) and are excluded from the core", pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))

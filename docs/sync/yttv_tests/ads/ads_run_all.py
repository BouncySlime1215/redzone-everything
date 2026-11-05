#!/usr/bin/env python3
"""Run every ads_NN test from ONE shared read-only capture (fast during games).

Live: polls media_session on both sticks + streams all-tag logcat for --minutes (read-only, never
presses keys or tunes), saves the capture to results/ads_capture_<epoch>.json, then runs each
test's analyse() on it, each writing its own results/<id>.jsonl. Replay a saved capture with
--from-capture PATH. --dry-run uses the recorded days instead. Prints one line per test.

Usage:
  python3 ads_run_all.py --dry-run
  python3 ads_run_all.py --minutes 30 --channel espn          # tonight, during a game
  python3 ads_run_all.py --from-capture ../results/ads_capture_1793900000.json
"""
import glob
import importlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adslib as L  # noqa: E402


def main():
    p = L.make_parser("ads_run_all", __doc__)
    p.add_argument("--from-capture", default=None)
    p.add_argument("--only", default=None, help="comma list of test number prefixes, e.g. 01,05,11")
    args = p.parse_args()
    if args.dry_run:
        days = L.days_of(args)
        ev, logs, segs = L.load_probe(days)
        plays = L.load_plays(days)
        boot = {h: [s["boot"] for s in segs.get(h, [])] for h in L.HOSTS.values()}
        src = f"recordings {days}"
    elif args.from_capture:
        cap = json.load(open(args.from_capture))
        ev, logs, boot, plays = cap["events"], cap["logs"], cap["boot"], cap.get("plays") or L.live_plays()
        src = args.from_capture
    else:
        vis, why = L.visible_hosts()
        hosts = list(L.HOSTS.values())
        print(f"[ads_run_all] LIVE read-only capture on both sticks for {args.minutes} min ({why}; visible={sorted(vis)})")
        ev, logs, boot = L.collect_live(hosts, args.minutes, args.poll, logcat=True, tags="")
        plays = L.live_plays()
        path = f"{L.RESULTS}/ads_capture_{int(time.time())}.json"
        os.makedirs(L.RESULTS, exist_ok=True)
        json.dump(dict(events=ev, logs=logs, boot=boot, plays=plays, visible=sorted(vis)), open(path, "w"))
        src = path
        print(f"[ads_run_all] capture saved: {path}")
    only = set(args.only.split(",")) if args.only else None
    rc = 0
    for f in sorted(glob.glob(f"{HERE}/ads_[0-9][0-9]_*.py")):
        mod_name = os.path.basename(f)[:-3]
        if only and mod_name[4:6] not in only:
            continue
        m = importlib.import_module(mod_name)
        a = L.make_parser(m.ID, m.__doc__).parse_args([a for a in sys.argv[1:]
                                                        if not a.startswith("--from-capture") and not a.startswith("--only")
                                                        and a != args.from_capture and a != args.only])
        out = L.Out(m.ID, a)
        hosts = list(L.HOSTS.values()) if m.ID.startswith("ads_05") else L.dry_hosts(a)
        e2 = L.filter_events(ev, a, hosts)
        out.w("source", src=src)
        try:
            m.analyse(e2, logs, plays, a, out, dict(hosts=hosts, boot=boot))
        except Exception as e:  # report and continue: one broken test must not stop the others
            rc = 1
            out.summary(None, error=repr(e))
            print(f"[{m.ID}] ERROR {e!r}")
    return rc


if __name__ == "__main__":
    sys.exit(main())

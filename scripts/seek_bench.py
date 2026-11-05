#!/usr/bin/env python3
"""[T7] Rewind reliability per app and channel on the HIDDEN stick: SEEK_TO vs key presses, and forced ads.

    python scripts/seek_bench.py preflight HOST                      # read-only: sessions, SEEK_TO bit, hidden?
    python scripts/seek_bench.py run HOST --channel nbatv [--backs 15,30,60,90] [--reps 2]
           [--methods seek_to,keys] [--watch-s 15] [--out data/reports/seek_bench_<date>.jsonl]
    python scripts/seek_bench.py report data/reports/seek_bench_<date>.jsonl [data/seeks.jsonl ...]

Rules (Nick): hidden stick only, only while Nick watches, never once games are live, never Peacock, no DRM or
HDCP bypass (this reads MediaSession numbers only, never frames). `run` refuses unless the app's /api/state
confirms HOST is the hidden stick; --tv-off is the only override (Nick says the TV is off). Sticks are written
as --alias (stickA), never by address, so the jsonl can be shared.

Each trial on the stick already playing the channel (tune it first; this script never tunes):
  1. read the session (rzseek list): position on the stick's clock, title, duration;
  2. rewind B seconds by METHOD: `seek_to` = redzone.seek_to with no key fallback (one exact seek + corrections),
     `keys` = the app's key path (seek_model presses + PLAY), measured on the stick's clock the same way;
  3. watch it for --watch-s at ~2 Hz: an ad is flagged when the title/duration changes or reads like an ad
     (is_ad_suspect), or the picture jumps > 2 s by itself (an ad pod or the app snapping back to live), or it
     stops playing > 3 s;
  4. seek forward by what it landed (back toward live) and record that landing too (restore_err_s).

`report` prints per app x channel x method: n, landing error p50/p90 of |err|, mean err (+ = further back than
asked), settle p50/p90, ads, and PASS (n >= 3, p90 |err| <= 1 s, settle p50 <= 3 s, 0 ads), the gate for
turning replay.seek_to.enabled on for that app. Extra files (the Mac's data/seeks.jsonl, livelab seekcal rows)
are summarized as logged rows (asked_s / landed_s, method keys unless the row says otherwise).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.tuners import seek_to as st  # noqa: E402

STATE_URL = "http://localhost:8778/api/state"
JUMP_S = 2.0
STALL_S = 3.0


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def bench_cfg(timeout_s: float = 4.0) -> Cfg:
    """The app's seek model (default_config) + replay.seek_to.* for the bench."""
    from redzone import config
    keys = ("replay.seek_key", "replay.seek_step_s", "replay.seek_wake_presses", "replay.seek_stall_min_s",
            "replay.seek_stall_max_s", "replay.seek_to.retries", "replay.seek_to.tolerance_s",
            "replay.seek_to.dex_path", "replay.seek_to.prime_key_step_s", "replay.seek_to.prime_key_swallowed")
    c = Cfg({k: config.default(k) for k in keys})
    c["replay.seek_to.enabled"] = True
    c["replay.seek_to.timeout_s"] = timeout_s
    return c


def app_of(pkg: str | None) -> str:
    p = (pkg or "").lower()
    if "youtube" in p:
        return "youtubetv"
    if "peacock" in p:
        return "peacock"
    if "firebat" in p or "avod" in p or "primevideo" in p or "amazon.video" in p:
        return "prime"
    return p or "?"


def screen_state(url: str = STATE_URL) -> dict:
    """{'hidden': set(hosts), 'ok': bool, 'why': str} from the app (fails safe: nothing hidden)."""
    try:
        with urllib.request.urlopen(url, timeout=2.5) as r:
            d = json.load(r)
    except Exception as e:
        return {"hidden": set(), "ok": False, "why": f"state fetch failed: {e!r}"[:120]}
    health = d.get("health") or {}
    vis, hid = set(), set()
    for s in health.get("sticks") or []:
        role = str(s.get("role") or "").lower().replace("_", " ")
        h = s.get("host")
        if not h:
            continue
        if role in ("on screen", "onscreen", "visible", "active", "live"):
            vis.add(h)
        elif role in ("standby", "hidden", "parked", "off screen", "offscreen"):
            hid.add(h)
    on = (health.get("agent") or {}).get("on_screen")
    if on:
        vis.add(on)
        hid.discard(on)
    if len(vis) != 1 or vis & hid:
        return {"hidden": set(), "ok": False, "why": f"ambiguous roles ({len(vis)} visible)"}
    return {"hidden": hid, "ok": True, "why": "api/state"}


async def rz(stick, args: str, timeout: float = 8.0) -> list[dict]:
    r = await stick.shell(f"{st.RZ} {args}", timeout=timeout)
    return st.parse_list(r.out if r.ok or r.out else "")


async def session(stick, pkg: str | None = None) -> dict | None:
    rows = [x for x in await rz(stick, "list") if "pkg" in x and "error" not in x]
    if pkg:
        rows = [x for x in rows if x.get("pkg") == pkg]
    playing = [x for x in rows if x.get("state") == st.STATE_PLAYING]
    return (playing or rows or [None])[0]


async def watch(stick, pkg: str, meta0: dict, secs: float) -> dict:
    """Read the session ~2x a second for `secs`: ad evidence, self-jumps, stalls."""
    ev = {"ad_title": False, "jumps": 0, "max_jump_s": 0.0, "stall_s": 0.0, "titles": []}
    prev, t_end, stalled_since = None, time.monotonic() + secs, None
    while time.monotonic() < t_end:
        x = await session(stick, pkg)
        if x:
            if st.is_ad_suspect(meta0, x):
                ev["ad_title"] = True
            t = x.get("title")
            if t and t not in ev["titles"] and len(ev["titles"]) < 4:
                ev["titles"].append(t)
            if x.get("state") != st.STATE_PLAYING:
                stalled_since = stalled_since or time.monotonic()
                ev["stall_s"] = max(ev["stall_s"], time.monotonic() - stalled_since)
            else:
                stalled_since = None
            if prev and prev.get("state") == st.STATE_PLAYING and x.get("state") == st.STATE_PLAYING:
                moved = st.landed_back_s(prev, x)      # 0 while it just plays on
                if moved is not None and abs(moved) > JUMP_S:
                    ev["jumps"] += 1
                    ev["max_jump_s"] = max(ev["max_jump_s"], round(abs(moved), 1))
            prev = x
        await asyncio.sleep(0.5)
    ev["stall_s"] = round(ev["stall_s"], 1)
    return ev


async def key_trial(stick, pkg: str, app: str, back_s: float, cfg, timeout_s: float = 12.0) -> dict:
    """The app's key path, measured on the stick's clock like a SEEK_TO."""
    before = await session(stick, pkg)
    ok, presses = await st._keys(stick, back_s, cfg, app)
    await stick.shell("input keyevent KEYCODE_MEDIA_PLAY", timeout=4)
    t0, after = time.monotonic(), None
    while time.monotonic() - t0 < timeout_s:
        x = await session(stick, pkg)
        if x and x.get("state") == st.STATE_PLAYING and before and (x.get("updated") or 0) > (before.get("updated") or 0):
            after = x
            break
        await asyncio.sleep(0.2)
    landed = st.landed_back_s(before, after) if before and after else None
    return {"ok": ok and after is not None, "method": "keys", "presses": presses, "attempts": 0,
            "landed_s": None if landed is None else round(landed, 2),
            "err_s": None if landed is None else round(landed - back_s, 2),
            "settle_s": round(time.monotonic() - t0, 2) if after else None, "timed_out": after is None,
            "meta0": st._meta(before or {}), "meta1": st._meta(after or {})}


async def run(args) -> int:
    from redzone.tuners.adb import Stick
    if not args.tv_off:
        ss = screen_state()
        if not ss["ok"] or args.host not in ss["hidden"]:
            print(f"REFUSED: {args.alias} is not confirmed hidden ({ss['why']}). Hidden stick only; --tv-off only "
                  "if Nick says the TV is off.", file=sys.stderr)
            return 2
    stick = Stick(args.host, name=args.alias)
    stick.state = "connected"
    cfg = bench_cfg(args.timeout_s)
    if not await st.ensure_dex(stick, cfg, ROOT):
        print("rzseek.dex is not on the stick and could not be pushed (build it: sh tools/rzseek/build.sh)",
              file=sys.stderr)
        return 2
    s0 = await session(stick)
    if not s0 or s0.get("state") != st.STATE_PLAYING:
        print(f"nothing playing on {args.alias}: tune the channel first", file=sys.stderr)
        return 2
    pkg, app = s0["pkg"], app_of(s0.get("pkg"))
    if app == "peacock":
        print("REFUSED: never Peacock", file=sys.stderr)
        return 2
    seek_ok = bool(int(s0.get("actions") or 0) & st.SEEK_TO_BIT)
    print(f"{args.alias}: {app} ({pkg}) channel={args.channel} SEEK_TO={'yes' if seek_ok else 'NO'}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    backs = [float(b) for b in args.backs.split(",") if b.strip()]
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    n = 0
    with out.open("a") as fh:
        for rep in range(args.reps):
            for m in methods:
                for b in backs:
                    if m == "seek_to":
                        r = (await st.seek_to(stick, b, app=app, cfg=cfg, pkg=pkg, allow_keys=False)).row()
                    else:
                        r = await key_trial(stick, pkg, app, b, cfg)
                    ev = await watch(stick, pkg, r.get("meta0") or {}, args.watch_s)
                    restore = None
                    if r.get("landed_s"):
                        back = await st.seek_to(stick, -float(r["landed_s"]), app=app, cfg=cfg, pkg=pkg,
                                                allow_keys=False)
                        restore = back.err_s
                    ad = bool(r.get("ad_suspect") or ev["ad_title"] or ev["jumps"] or ev["stall_s"] > STALL_S)
                    row = {"t": round(time.time(), 3), "stick": args.alias, "app": app, "channel": args.channel,
                           "rep": rep, "asked_s": b, **{k: r.get(k) for k in (
                               "ok", "method", "landed_s", "err_s", "settle_s", "attempts", "presses", "timed_out")},
                           "ad": ad, "ad_evidence": {"title": bool(r.get("ad_suspect") or ev["ad_title"]),
                                                     "jumps": ev["jumps"], "max_jump_s": ev["max_jump_s"],
                                                     "stall_s": ev["stall_s"]},
                           "restore_err_s": restore,
                           # the T7 tester's grader schema (tests/test_t7_seek_offline.py) reads these names
                           "host": args.alias, "ad_suspect": ad, "sent": r.get("presses") or 0,
                           "reason": r.get("reason", "")}
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    n += 1
                    print(f"  {m:8s} back {b:5.1f}s -> landed {row['landed_s']} err {row['err_s']} "
                          f"settle {row['settle_s']} ad={ad}")
                    await asyncio.sleep(args.gap_s)
    print(f"{n} trials -> {out}")
    return 0


async def preflight(args) -> int:
    from redzone.tuners.adb import Stick
    ss = screen_state()
    stick = Stick(args.host, name=args.alias)
    stick.state = "connected"
    print(f"screen: {ss['why']}; {args.alias} hidden={args.host in ss['hidden']}")
    dex = await st.ensure_dex(stick, bench_cfg(), ROOT)
    print(f"rzseek.dex on stick: {dex}")
    if dex:
        for x in await rz(stick, "list"):
            if "pkg" in x:
                print(f"  {app_of(x.get('pkg')):10s} state={x.get('state')} SEEK_TO="
                      f"{bool(int(x.get('actions') or 0) & st.SEEK_TO_BIT)} title={x.get('title')!r}")
            else:
                print(f"  {x}")
    return 0


def _q(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))], 2)


def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r.get("app") or app_of(r.get("target", "").split(":")[0] if r.get("target") else None),
               r.get("channel") or (r.get("target", "").split(":", 1)[-1] if r.get("target") else "?"),
               r.get("method") or "keys")
        groups.setdefault(key, []).append(r)
    out = []
    for (app, ch, m), rs in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        errs = [float(r["err_s"]) if r.get("err_s") is not None else float(r["landed_s"]) - float(r["asked_s"])
                for r in rs if r.get("landed_s") is not None and r.get("asked_s") is not None]
        settle = [float(r["settle_s"]) for r in rs if r.get("settle_s") is not None]
        ads = sum(1 for r in rs if r.get("ad") or r.get("ad_suspect"))
        absd = [abs(e) for e in errs]
        p90, s50 = _q(absd, 0.9), _q(settle, 0.5)
        passed = len(errs) >= 3 and p90 is not None and p90 <= 1.0 and s50 is not None and s50 <= 3.0 and ads == 0
        out.append({"app": app, "channel": ch, "method": m, "n": len(rs), "n_landed": len(errs),
                    "abs_err_p50": _q(absd, 0.5), "abs_err_p90": p90,
                    "err_mean": round(statistics.fmean(errs), 2) if errs else None,
                    "short_n": sum(1 for e in errs if e < -1.0), "settle_p50": s50, "settle_p90": _q(settle, 0.9),
                    "ads": ads, "verdict": "PASS" if passed else ("INSUFFICIENT" if len(errs) < 3 else "FAIL")})
    return out


def report(args) -> int:
    rows = []
    for f in args.files:
        for line in Path(f).read_text().splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and "asked_s" in d:
                rows.append(d)
    res = summarize(rows)
    if args.json:
        print(json.dumps(res, indent=1))
        return 0
    print("| app | channel | method | n | abs err p50 / p90 s | mean err s | short >1 s | settle p50 / p90 s "
          "| ads | verdict |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in res:
        print(f"| {r['app']} | {r['channel']} | {r['method']} | {r['n']} | {r['abs_err_p50']} / {r['abs_err_p90']} "
              f"| {r['err_mean']} | {r['short_n']} | {r['settle_p50']} / {r['settle_p90']} | {r['ads']} "
              f"| {r['verdict']} |")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight")
    p.add_argument("host")
    p.add_argument("--alias", default="stickA")
    r = sub.add_parser("run")
    r.add_argument("host")
    r.add_argument("--alias", default="stickA")
    r.add_argument("--channel", required=True, help="label only (e.g. nbatv, espn2, tnf)")
    r.add_argument("--backs", default="15,30,60,90")
    r.add_argument("--reps", type=int, default=2)
    r.add_argument("--methods", default="seek_to,keys")
    r.add_argument("--watch-s", type=float, default=15.0)
    r.add_argument("--gap-s", type=float, default=2.0)
    r.add_argument("--timeout-s", type=float, default=4.0)
    r.add_argument("--tv-off", action="store_true", help="Nick says the TV is off: skip the hidden-stick check")
    r.add_argument("--out", default=f"data/reports/seek_bench_{date.today().isoformat()}.jsonl")
    g = sub.add_parser("report")
    g.add_argument("files", nargs="+")
    g.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        return report(a)
    return asyncio.run(run(a) if a.cmd == "run" else preflight(a))


if __name__ == "__main__":
    sys.exit(main())

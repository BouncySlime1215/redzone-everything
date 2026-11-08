"""Buffered jumps on the real TV (both YouTube TV sticks scrubbed ~75 s behind live, cuts planned from the play-by-play).
Turns it on / off through the running app's settings API (undoable) and reads what it did. Writes nothing but the
setting. Only while Nick watches (the hidden stick is scrubbed with overlay keys).

    .venv/bin/python scripts/buffered_jump_try.py on          # jump_mode + buffered_jump + the 34.9 s edge prior
    .venv/bin/python scripts/buffered_jump_try.py on --eager  # ...cutting at every shared dead ball (more cuts)
    .venv/bin/python scripts/buffered_jump_try.py watch       # every 60 s: jumps/min, bj why, holds, scrubs; and per
                                                              # 10-min window a PASS/FAIL line (Nick's pass bar)
    .venv/bin/python scripts/buffered_jump_try.py report --since-min 20
    .venv/bin/python scripts/buffered_jump_try.py off         # buffered_jump false (jump mode stays as it was)

What to look for: data/buffer.jsonl "scrubbed" rows (each fresh tune label-read 45-130 s behind), jump rows with
why bj_snap_next / bj_long_dead and behind_s on both sides, and on screen: every cut lands on the line-up a few
seconds before a snap, never mid-play. Expected (sim, 37 NFL slates): ~1.1 plays/min shown, 0.4-1 cuts/min
snap-timed (+ break cuts); eager ~4-5 cuts/min.
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jump_try import LOG, ROOT, STATUS, post, rows, summary  # noqa: E402

BUFFER = ROOT / "data" / "buffer.jsonl"
EDGE_S = 34.9    # YouTube TV's live edge behind real life (the edge prior)
AUDIT_LAG_S = 150.0   # judge a window this long after it ends: the play-by-play has logged its last plays by then


def _summary(gid: str, league: str | None, cache: dict):
    """(league, plays) for gid from ESPN's public summary (numbers and kinds only; nothing is written), or None."""
    if gid in cache:
        return cache[gid]
    import urllib.request
    from redzone.models import League
    from redzone.sources.espn import summary_url
    import buffered_jump_sim as sim
    out = None
    for lg in ([league] if league else []) + ["nfl", "cfb", "nba", "cbb", "wcbb"]:
        try:
            with urllib.request.urlopen(summary_url(League(lg), gid), timeout=15) as r:
                plays = sim.load_game("", League(lg), json.load(r))
            if len(plays) >= 10:
                out = (League(lg), plays)
                break
        except Exception:
            continue
    cache[gid] = out
    return out


def pass_window(w0: float, w1: float, cache: dict | None = None) -> dict:
    """[Nick 22:15/22:17 Nov 7 pass bar] One 10-minute window of the real TV: PASS = 75+ jumps on screen AND 0 ad
    seconds AND every shown play in full AND 0 mid-play joins / leaves. The rules are audited after the fact against
    ESPN's play-by-play (wallclocks), each picture at edge + the label-read behind the jump logged (a parked stick's
    pause is in it once rolled). Jumps without a 'from'/'to' or an unreadable game count but can't be audited."""
    import buffered_jump_sim as sim
    cache = {} if cache is None else cache
    js = sorted((r for r in rows(w0 - 3600) if r.get("event") is None and r.get("ok") and not r.get("dry_run")),
                key=lambda r: r["ts"])
    before = [r for r in js if r["ts"] < w0]
    inw = [r for r in js if w0 <= r["ts"] < w1]
    out = {"jumps": len(inw), "audited": False}
    if not inw:
        out["pass"] = False
        return out
    flip = 3.2

    def mu(r, key):
        return EDGE_S + float(r.get(key) or 0.0)
    # the screen: the game the window starts on, then each jump's target from its landing on
    segs, cut_at, mid_at = [], [], []
    g0 = before[-1] if before else None
    cur, cur_mu, cur_lg = (g0["to"], mu(g0, "behind_s"), g0.get("to_league")) if g0 else \
        (inw[0].get("from"), mu(inw[0], "here_behind_s"), inw[0].get("from_league"))
    seg0, games = w0, {}
    missing = 0
    for r in inw:
        land = r["ts"] + float(r.get("took_s") or flip)
        for gid, lg in ((cur, cur_lg), (r.get("to"), r.get("to_league"))):
            if gid and gid not in games:
                games[gid] = _summary(str(gid), lg, cache)
        if cur and games.get(cur):
            segs.append((cur, seg0, land, cur_mu))
            sp = sim.play_spans(games[cur][1], games[cur][0])
            if sim.in_play(sp, land - flip - cur_mu, land - cur_mu):
                mid_at.append(r["ts"])
        else:
            missing += 1
        to, to_mu = r.get("to"), mu(r, "behind_s")
        if to and games.get(to):
            sp = sim.play_spans(games[to][1], games[to][0])
            if sim.in_play(sp, land - to_mu, land + 0.5 - to_mu):
                mid_at.append(r["ts"])
        else:
            missing += 1
        cut_at.append(r["ts"])
        cur, cur_mu, cur_lg, seg0 = to, to_mu, r.get("to_league"), land
    if cur and games.get(cur):
        segs.append((cur, seg0, w1, cur_mu))
    spans = {g: sim.play_spans(v[1], v[0]) for g, v in games.items() if v}
    ads = {g: sim.ad_windows(v[1], v[0]) for g, v in games.items() if v}
    sim.PASS_WINDOW_S = w1 - w0
    bar = sim.pass_bar(segs, spans, ads, cut_at, mid_at, w0, w1)["window_rows"]
    row = bar[0] if bar else {"jumps": len(inw), "ad_s": 0.0, "partial_plays": 0, "mid_play": 0, "pass": False}
    out.update(row, audited=missing == 0, unaudited_jumps=missing)
    out["pass"] = bool(row["pass"]) and missing == 0
    return out


def pass_line(w0: float, w1: float, cache: dict | None = None) -> str:
    v = pass_window(w0, w1, cache)
    rules = (f"ads {v.get('ad_s', '?')} s, cut-off plays {v.get('partial_plays', '?')}, mid-play joins/leaves "
             f"{v.get('mid_play', '?')}" + ("" if v.get("audited") else f" (unaudited jumps {v.get('unaudited_jumps', '-')})"))
    return (f"{time.strftime('%H:%M', time.localtime(w0))}-{time.strftime('%H:%M', time.localtime(w1))} "
            f"{'PASS' if v['pass'] else 'FAIL'}: {v['jumps']}/75 jumps | {rules}")


def bj_summary(since: float, minutes: float) -> str:
    rs = [r for r in rows(since) if r.get("event") is None]
    bj = [r for r in rs if str(r.get("why", "")).startswith("bj_")]
    ok = [r for r in bj if r.get("ok") and not r.get("dry_run")]
    holds = Counter()
    for r in rows(since, STATUS):
        holds.update({k: v for k, v in (r.get("holds") or {}).items() if k.startswith("bj_")})
    sc = rows(since, BUFFER)
    scr = [r for r in sc if r.get("kind") == "scrubbed"]
    fail = Counter((r.get("why") or "?")[:60] for r in sc if r.get("kind") == "scrub_failed")
    behind = sorted(r.get("behind_s", 0) for r in scr)
    snap = sorted(r.get("h_snap_s", 0) for r in ok)
    return (f"buffered: {len(ok)}/{len(bj)} cuts ok = {len(ok) / max(minutes, 1e-9):.1f}/min | why "
            f"{dict(Counter(r['why'] for r in bj))} | snap after landing median "
            f"{snap[len(snap) // 2] if snap else '-'} s | scrubs ok {len(scr)} (behind {behind[0] if behind else '-'}"
            f"..{behind[-1] if behind else '-'} s) failed {dict(fail)} | editor holds {dict(holds)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["on", "off", "watch", "report"])
    ap.add_argument("--port", type=int, default=None, help="the app's port (default: server.port in data/config.yaml, else 8778)")
    ap.add_argument("--eager", action="store_true", help="on: cut at every shared dead ball")
    ap.add_argument("--since-min", type=float, default=None)
    a = ap.parse_args()
    if a.cmd == "on":
        post(a.port, {"jump_mode": {"enabled": True}, "delay": {"edge_prior": {"enabled": True}},
                      "buffered_jump": {"enabled": True, "eager": bool(a.eager)}})
    elif a.cmd == "off":
        post(a.port, {"buffered_jump": {"enabled": False}})
    elif a.cmd == "report":
        since = time.time() - a.since_min * 60 if a.since_min else 0.0
        rs = rows(since)
        span = a.since_min or ((rs[-1]["ts"] - rs[0]["ts"]) / 60 if len(rs) > 1 else 1.0)
        print(summary(rs, span, rows(since, STATUS)))
        print(bj_summary(since, span))
        if rs:
            cache, w0 = {}, max(since, rs[0]["ts"])
            while w0 + 600 <= time.time() - AUDIT_LAG_S:
                print("  PASS BAR", pass_line(w0, w0 + 600, cache))
                w0 += 600
    else:
        t0 = time.time()
        judged, cache = t0, {}
        while True:
            time.sleep(60)
            print(time.strftime("%H:%M"), "last 1 min:", bj_summary(time.time() - 60, 1.0))
            print("      since start:", bj_summary(t0, (time.time() - t0) / 60))
            # [Nick 22:17] one PASS/FAIL line per 10-minute window (75 jumps + the four hard rules), judged once its
            # play-by-play is in
            while time.time() >= judged + 600 + AUDIT_LAG_S:
                print("  PASS BAR", pass_line(judged, judged + 600, cache))
                judged += 600
                cache.clear()
            sys.stdout.flush()


if __name__ == "__main__":
    assert LOG  # jump rows live in data/jumps.jsonl
    main()

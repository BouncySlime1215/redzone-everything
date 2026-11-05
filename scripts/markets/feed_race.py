#!/usr/bin/env python3
"""TNF feed race (tracker item 4, Nick 9:50 AM Nov 5): which source sees a score first, and by how much it leads
the Prime screen. SHADOW ONLY: nothing here feeds the app, nothing is shown, no betting UI.

Sources, all public, $0, no accounts:
  * `espn_scoreboard`, `espn_summary`: polled from the cloud box (`espn` subcommand), each every 2 s and staggered,
    with If-None-Match / If-Modified-Since; one row per score change, stamped with our receive time.
  * `polymarket`: the #315 jump triggers (scripts/markets/poly_live_trigger.py), receive time.
  * `app_feed`: the live app's own feed first-seen for the play the score bug matched (sync.jsonl: t - z).
  * `screen`: the Prime score bug's onset on the TV (sync.jsonl rows with kind "score": t). Numbers only, never frames.

    python scripts/markets/feed_race.py espn --teams JAX,BAL --hours 5 --out out/race
    python scripts/markets/feed_race.py grade --espn out/race/espn.jsonl \
        --poly out/markets_live/polymarket/nfl/20261105.jsonl --sync sync.jsonl --game-key nfl:20261105:JAX@BAL \
        --since 2026-11-06T01:00:00Z --out out/race/grades.json
    # fallback when the live app has no score-bug read (RC6.2): source order only, no screen lead
    python scripts/markets/feed_race.py order --espn race_derived.jsonl --poly race_derived.jsonl \
        --app-race race.jsonl --game-key nfl:20261105:JAX@BAL --out out/race/order.json

Clocks: the cloud box and the Mac must both be NTP-synced; the residual (~0.1 s) is far below the leads measured.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

UA = "redzone-research/0.1"
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
POLL_S = 2.0
MATCH_S = 120.0        # a source's sighting must fall within this of the screen onset to be the same score
POLY_BEFORE_S = 90.0   # a market trigger counts for a score if it lands in [screen - 90 s, screen + 30 s]
POLY_AFTER_S = 30.0


# ------------------------------------------------------------------ pure parsing (tested)

def scores_from_competition(comp: dict, teams: set[str]) -> tuple[str, tuple[int, int]] | None:
    """An ESPN competition -> ("AWAY@HOME", (away, home)) if it is the game whose teams are `teams`."""
    cs = comp.get("competitors") or []
    side = {}
    for c in cs:
        ab = ((c.get("team") or {}).get("abbreviation") or "").upper()
        try:
            side[c.get("homeAway")] = (ab, int(float(c.get("score") or 0)))
        except (TypeError, ValueError):
            return None
    if set(side) != {"home", "away"} or {side["home"][0], side["away"][0]} != teams:
        return None
    return f"{side['away'][0]}@{side['home'][0]}", (side["away"][1], side["home"][1])


def scoreboard_game(doc: dict, teams: set[str]) -> tuple[str, str, tuple[int, int]] | None:
    """Scoreboard JSON -> (event id, "AWAY@HOME", score) for the game, else None."""
    for ev in doc.get("events") or []:
        for comp in ev.get("competitions") or []:
            r = scores_from_competition(comp, teams)
            if r:
                return str(ev.get("id") or ""), r[0], r[1]
    return None


def summary_game(doc: dict, teams: set[str]) -> tuple[str, tuple[int, int]] | None:
    for comp in (doc.get("header") or {}).get("competitions") or []:
        r = scores_from_competition(comp, teams)
        if r:
            return r
    return None


class ChangeLog:
    """Keeps the last score per source and returns a row only when it changes (first sighting)."""

    def __init__(self):
        self.last: dict[str, tuple[int, int]] = {}

    def see(self, source: str, matchup: str, score: tuple[int, int], recv: float) -> dict | None:
        prev = self.last.get(source)
        self.last[source] = score
        if prev is None or prev == score:
            return None if prev == score else {"source": source, "matchup": matchup, "recv_ts": recv,
                                               "score": list(score), "prev": None}
        return {"source": source, "matchup": matchup, "recv_ts": recv, "score": list(score), "prev": list(prev)}


def screen_rows(sync_lines, since: float, until: float, target_hint: str = "") -> list[dict]:
    """sync.jsonl -> [{screen_ts, app_feed_ts, target}] for score-bug samples (kind 'score') in the window."""
    out = []
    for line in sync_lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") != "score" or r.get("t") is None or r.get("z") is None:
            continue
        t, z = float(r["t"]), float(r["z"])
        tgt = str(r.get("target") or "")
        if since <= t <= until and (not target_hint or target_hint.lower() in tgt.lower()):
            out.append({"screen_ts": t, "app_feed_ts": t - z, "target": tgt,
                        **({"score": list(r["score"])} if r.get("score") else {})})
    return sorted(out, key=lambda x: x["screen_ts"])


def grade(screens: list[dict], espn: list[dict], poly: list[dict], game_key: str) -> dict:
    """Per screen score change: when each source first had it, and its lead over the screen (s, + = earlier)."""
    matchup = game_key.split(":")[-1]          # Polymarket slugs may date a night game by its UTC day
    trig = sorted(r["recv_ts"] for r in poly if r.get("row") == "trigger"
                  and str(r.get("game_key", "")).split(":")[-1] == matchup)
    by_src: dict[str, list[dict]] = {}
    for r in espn:
        if r.get("prev") is not None:              # the first poll of a source is not a score change
            by_src.setdefault(r["source"], []).append(r)
    # sync.jsonl score rows carry no score, so a source's changes are matched IN ORDER (scores happen in the same
    # order everywhere): each screen change takes the nearest later-unused row after the last match, never crossing.
    for rows in by_src.values():
        rows.sort(key=lambda r: r["recv_ts"])
    events, last = [], {s: -1 for s in by_src}
    t_used = -1
    for s in screens:
        ev = {"screen_ts": s["screen_ts"], "target": s["target"], "lead_s": {"app_feed": s["screen_ts"] - s["app_feed_ts"]}}
        for src, rows in by_src.items():
            want = s.get("score")                  # when the screen read carries the score (#326), match on it
            cand = [(abs(r["recv_ts"] - s["screen_ts"]), i) for i, r in enumerate(rows)
                    if i > last[src] and abs(r["recv_ts"] - s["screen_ts"]) <= MATCH_S
                    and (want is None or list(r["score"]) == list(want))]
            if cand:
                i = min(cand)[1]
                last[src] = i
                ev["lead_s"][src] = s["screen_ts"] - rows[i]["recv_ts"]
                ev.setdefault("score", rows[i]["score"])
        # each market trigger counts for one score at most (the first unused one in the window)
        p = [(j, t) for j, t in enumerate(trig)
             if j > t_used and s["screen_ts"] - POLY_BEFORE_S <= t <= s["screen_ts"] + POLY_AFTER_S]
        if p:
            t_used = p[0][0]
            ev["lead_s"]["polymarket"] = s["screen_ts"] - p[0][1]
        events.append(ev)
    srcs = sorted({k for e in events for k in e["lead_s"]})
    summary = {}
    for k in srcs:
        v = [e["lead_s"][k] for e in events if k in e["lead_s"]]
        summary[k] = {"n": len(v), "median_lead_s": round(statistics.median(v), 2),
                      "min_lead_s": round(min(v), 2), "max_lead_s": round(max(v), 2),
                      "share_ahead_of_screen": round(sum(x > 0 for x in v) / len(v), 3)}
    leader = max(summary, key=lambda k: (summary[k]["n"] >= max(1, len(events) // 2), summary[k]["median_lead_s"])) \
        if summary else None
    return {"game_key": game_key, "events": len(events), "summary": summary, "leader": leader, "rows": events,
            "note": "lead_s = screen onset - source first-seen; positive means the source had the score first"}


ORDER_NOTE = "source order only, no screen lead: RC6.2 has no Prime score-bug read, so nothing here is timed against the TV"


def app_sightings(race_rows: list[dict], scores: set[tuple[int, int]]) -> dict[tuple[int, int], dict[str, float]]:
    """RC6.2 data/recordings/<day>/race.jsonl -> {score: {"app:<source>": first-seen}} for the game whose score
    labels best overlap `scores` (the ESPN score changes). Row: {"t","game","label","first","others": {src: lag_s}}."""
    by_game: dict[str, list[dict]] = {}
    for r in race_rows:
        lab = str(r.get("label") or "")
        if lab.startswith("score ") and r.get("t") is not None:
            by_game.setdefault(str(r.get("game")), []).append(r)

    def sc(r):
        try:
            a, h = r["label"].split()[1].split("-")
            return int(a), int(h)
        except (IndexError, ValueError):
            return None

    if not by_game:
        return {}
    game = max(by_game, key=lambda g: len({sc(r) for r in by_game[g]} & scores))
    out: dict[tuple[int, int], dict[str, float]] = {}
    for r in by_game[game]:
        s = sc(r)
        if s not in scores:
            continue
        seen = {f"app:{r['first']}": float(r["t"])}
        seen.update({f"app:{k}": float(r["t"]) + float(v) for k, v in (r.get("others") or {}).items()})
        cur = out.setdefault(s, {})
        for k, v in seen.items():                  # the merger may log a label twice: keep the earliest copy
            cur[k] = min(v, cur.get(k, v))
    return out


def order(espn: list[dict], poly: list[dict], app_race: list[dict], game_key: str) -> dict:
    """Fallback grade (no screen): per score change, when each source first had it and its lag behind the first."""
    matchup = game_key.split(":")[-1]
    first: dict[tuple[int, int], dict[str, float]] = {}
    for r in espn:
        if r.get("prev") is None or r.get("source") == "polymarket":
            continue
        s = tuple(r["score"])
        first.setdefault(s, {})
        first[s][r["source"]] = min(float(r["recv_ts"]), first[s].get(r["source"], float("inf")))
    for s, seen in app_sightings(app_race, set(first)).items():
        first[s].update(seen)
    trig = sorted(float(r["recv_ts"]) for r in poly if r.get("row") == "trigger"
                  and str(r.get("game_key", "")).split(":")[-1] == matchup)
    events, t_used = [], -1
    for s, seen in sorted(first.items(), key=lambda kv: min(kv[1].values())):
        t0 = min(seen.values())
        p = [(j, t) for j, t in enumerate(trig) if j > t_used and t0 - POLY_BEFORE_S <= t <= t0 + POLY_AFTER_S]
        if p:
            t_used = p[0][0]
            seen = {**seen, "polymarket": p[0][1]}
        t0 = min(seen.values())
        events.append({"score": list(s), "first": min(seen, key=seen.get),
                       "lag_s": {k: round(v - t0, 2) for k, v in sorted(seen.items())}})
    srcs = sorted({k for e in events for k in e["lag_s"]})
    summary = {}
    for k in srcs:
        v = [e["lag_s"][k] for e in events if k in e["lag_s"]]
        summary[k] = {"n": len(v), "first_n": sum(e["first"] == k for e in events),
                      "median_lag_s": round(statistics.median(v), 2), "max_lag_s": round(max(v), 2)}
    return {"game_key": game_key, "mode": "order", "events": len(events), "summary": summary, "rows": events,
            "note": ORDER_NOTE + "; lag_s = source first-seen - earliest source (0 = first)"}


# ------------------------------------------------------------------ network

def poll_espn(teams: set[str], hours: float, out: Path) -> None:
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = UA
    cache: dict[str, dict] = {}
    backoff: dict[str, float] = {}
    log, ev_id, stop = ChangeLog(), None, time.time() + hours * 3600
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as fh:
        while time.time() < stop:
            for name in ("espn_scoreboard", "espn_summary"):
                t0 = time.time()
                url, params = (f"{SITE}/scoreboard", None) if name == "espn_scoreboard" else (f"{SITE}/summary", {"event": ev_id})
                if name == "espn_summary" and not ev_id:
                    time.sleep(POLL_S / 2)
                    continue
                h = {}
                if cache.get(name, {}).get("etag"):
                    h["If-None-Match"] = cache[name]["etag"]
                if cache.get(name, {}).get("lm"):
                    h["If-Modified-Since"] = cache[name]["lm"]
                try:
                    r = s.get(url, params=params, headers=h, timeout=10)
                except Exception as e:  # noqa: BLE001
                    print(f"{name}: {type(e).__name__}", file=sys.stderr)
                    time.sleep(POLL_S)
                    continue
                recv = time.time()
                if r.status_code in (403, 429) or r.status_code >= 500:   # back off: rate-limit manners
                    backoff[name] = min(300.0, backoff.get(name, 2.5) * 2)  # 5 s, doubling, like sources/http.py
                    try:
                        wait = max(backoff[name], float(r.headers.get("Retry-After") or 0))
                    except ValueError:
                        wait = backoff[name]
                    print(f"{name}: HTTP {r.status_code}, backing off {wait:.0f} s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                backoff.pop(name, None)
                if r.status_code == 200:
                    cache[name] = {"etag": r.headers.get("ETag"), "lm": r.headers.get("Last-Modified")}
                    doc = r.json()
                    if name == "espn_scoreboard":
                        g = scoreboard_game(doc, teams)
                        if g:
                            ev_id = ev_id or g[0]
                            row = log.see(name, g[1], g[2], recv)
                        else:
                            row = None
                    else:
                        g2 = summary_game(doc, teams)
                        row = log.see(name, g2[0], g2[1], recv) if g2 else None
                    if row:
                        fh.write(json.dumps(row) + "\n")
                        fh.flush()
                time.sleep(max(0.0, POLL_S / 2 - (time.time() - t0)))


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p and p.exists() else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("espn")
    e.add_argument("--teams", default="JAX,BAL")
    e.add_argument("--hours", type=float, default=5.0)
    e.add_argument("--out", type=Path, default=Path("out/race"))
    g = sub.add_parser("grade")
    g.add_argument("--espn", type=Path, required=True)
    g.add_argument("--poly", type=Path)
    g.add_argument("--sync", type=Path, required=True, help="the app's data/sync.jsonl, copied off the Mac after the game")
    g.add_argument("--game-key", default="nfl:20261105:JAX@BAL")
    g.add_argument("--target", default="", help="substring of the Prime target name in sync.jsonl")
    g.add_argument("--since", required=True)
    g.add_argument("--until", default="2100-01-01T00:00:00Z")
    g.add_argument("--out", type=Path, default=Path("out/race/grades.json"))
    o = sub.add_parser("order", help="fallback without a screen read: " + ORDER_NOTE)
    o.add_argument("--espn", type=Path, required=True)
    o.add_argument("--poly", type=Path)
    o.add_argument("--app-race", type=Path, help="RC6.2 data/recordings/<day>/race.jsonl, copied read-only after the game")
    o.add_argument("--game-key", default="nfl:20261105:JAX@BAL")
    o.add_argument("--out", type=Path, default=Path("out/race/order.json"))
    a = ap.parse_args(argv)
    if a.cmd == "order":
        res = order(_jsonl(a.espn), _jsonl(a.poly) if a.poly else [], _jsonl(a.app_race) if a.app_race else [],
                    a.game_key)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps({k: res[k] for k in ("game_key", "mode", "events", "summary", "note")}, indent=1))
        return 0
    if a.cmd == "espn":
        poll_espn({t.strip().upper() for t in a.teams.split(",")}, a.hours, a.out / "espn.jsonl")
        return 0
    screens = screen_rows(a.sync.read_text().splitlines(), _ts(a.since), _ts(a.until), a.target)
    res = grade(screens, _jsonl(a.espn), _jsonl(a.poly) if a.poly else [], a.game_key)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: res[k] for k in ("game_key", "events", "leader", "summary")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

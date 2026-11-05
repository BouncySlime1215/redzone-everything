#!/usr/bin/env python3
"""Data H: human highlight set from game recaps -> plays only (night, gid, period, clock, team). No text kept.

    python scripts/gamectx/recap_highlights.py --archive data/archive --out out/highlights [--league nba nfl]

The judge for EXP-1721 (oracle reward v2): a play is a "human highlight" when the game's recap (the archived
ESPN summary `article.story`) names it. Each recap sentence is read in memory, its anchors pulled out
(player ids / last names, clock "with 1:02 left", period words, a running score "98-95", yards "25-yard",
action words), and matched to the one play of that game that fits best. Only the play key + match numbers
are written; the sentence is dropped. A sentence whose only anchor is the final score is skipped (that is
the result, not a moment).
FIT NIGHTS ONLY: train + valid of the active pin's split (redzone.lab.splits); LOCKED nights are never read.
Personal-use, not for commercial release.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PERIOD_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
ACTIONS = {
    "nba": ["3-pointer", "three-pointer", "dunk", "layup", "jumper", "free throw", "block", "steal", "alley-oop",
            "tip-in", "putback", "hook", "fadeaway", "floater", "turnover"],
    "nfl": ["touchdown", "field goal", "interception", "intercept", "fumble", "sack", "safety", "punt",
            "kickoff", "pass", "run", "two-point", "blocked", "return", "fourth down", "fourth-and"],
}
IN_PLAY = {"3-pointer": "three point", "three-pointer": "three point", "intercept": "intercept"}
LINK = re.compile(r'<a [^>]*href="[^"]*/(?:player|id)/_/id/(\d+)/[^"]*"[^>]*>(.*?)</a>', re.I | re.S)
TAG = re.compile(r"<[^>]+>")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})\s+(?:left|remaining|to go|to play)")
SECS = re.compile(r"(\d+(?:\.\d)?)\s+seconds?\s+(?:left|remaining|to go|to play)")
SCORE = re.compile(r"\b(\d{1,3})-(\d{1,3})\b(?!\s*(?:run|spurt|burst|edge|advantage in|in the|first|second|third|fourth|quarter|half|period))")
NOT_A_MOMENT = ("enters the game", "timeout", "end of", "start of", "jump ball", "substitution", "challenge")
YARDS = re.compile(r"\b(\d{1,2})-yard")
PERIOD = re.compile(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:quarter|period)|\b(overtime|OT)\b", re.I)


def clock_s(v: str) -> float | None:
    m = re.match(r"(\d+):(\d+(?:\.\d+)?)", v or "")
    if m:
        return int(m[1]) * 60 + float(m[2])
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sentences(story: str) -> list[tuple[str, list[str], list[str]]]:
    """[(plain sentence, athlete ids, last names)] for one recap, in memory only."""
    out = []
    for para in re.split(r"</?p>|\n", story or ""):
        ids = {m[1]: TAG.sub("", m[2]).strip() for m in LINK.finditer(para)}
        plain = unescape(TAG.sub("", para))
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"])", plain):
            if len(s) < 20:
                continue
            here = [(i, n) for i, n in ids.items() if n and n in s]
            out.append((s, [i for i, _ in here], [n.split()[-1] for _, n in here]))
    return out


def plays_of(league: str, d: dict) -> list[dict]:
    """Flat play list: seq, period, clock_s, team (abbr), score, text (in memory), participant ids."""
    comps = (d.get("header", {}).get("competitions") or [{}])[0].get("competitors", [])
    abbr = {str(c.get("team", {}).get("id")): c.get("team", {}).get("abbreviation", "") for c in comps}
    home = next((str(c["team"]["id"]) for c in comps if c.get("homeAway") == "home"), None)
    raw = []
    if league == "nfl":
        for dr in (d.get("drives") or {}).get("previous", []):
            t = dr.get("team", {}).get("abbreviation", "")
            for p in dr.get("plays", []):
                raw.append((p, t))
    else:
        for p in d.get("plays") or []:
            raw.append((p, abbr.get(str((p.get("team") or {}).get("id")), "")))
    out = []
    for p, t in raw:
        out.append({"seq": str(p.get("sequenceNumber") or p.get("id")), "period": (p.get("period") or {}).get("number"),
                    "clock": clock_s((p.get("clock") or {}).get("displayValue")), "team": t,
                    "hs": p.get("homeScore"), "as": p.get("awayScore"), "scoring": bool(p.get("scoringPlay")),
                    "text": (p.get("text") or "").lower(),
                    "ath": {str((x.get("athlete") or {}).get("id")) for x in p.get("participants") or []}})
    return out, home


def anchors(league: str, s: str) -> dict:
    low = s.lower()
    clk = None
    m = CLOCK.search(low)
    if m:
        clk = int(m[1]) * 60 + int(m[2])
    else:
        m = SECS.search(low)
        if m:
            clk = float(m[1])
    pm = PERIOD.search(s)
    per = (PERIOD_WORDS.get(pm[1].lower()) if pm and pm[1] else 5) if pm else None
    if pm and not pm[1]:
        per = 5
    scores = [tuple(sorted((int(a), int(b)))) for a, b in SCORE.findall(s)
              if not (league == "nfl" and int(a) < 11 and int(b) < 11 and "-yard" in s)]
    return {"clock": clk, "period": per, "scores": scores, "yards": [int(y) for y in YARDS.findall(low)],
            "actions": [a for a in ACTIONS[league] if a in low]}


def match(league: str, plays: list[dict], ids: list[str], names: list[str], a: dict, final: tuple) -> tuple:
    """(best play, score, fields) or (None, 0, '')."""
    strong = a["clock"] is not None or a["yards"] or any(sc != final for sc in a["scores"])
    if not strong and not (a["period"] and a["actions"] and (ids or names)):
        return None, 0, ""
    best, bs, bf, tie = None, 0, "", False
    for p in plays:
        if any(w in p["text"] for w in NOT_A_MOMENT):
            continue
        sc, f = 0, []
        if ids and p["ath"] & set(ids) or names and any(n.lower() in p["text"] for n in names):
            sc += 2
            f.append("player")
        if a["clock"] is not None and p["clock"] is not None and abs(p["clock"] - a["clock"]) <= 2:
            sc += 3
            f.append("clock")
        if a["period"] and p["period"] == a["period"]:
            sc += 1
            f.append("period")
        elif a["period"] and p["period"] != a["period"]:
            continue
        if p["hs"] is not None and any(s == tuple(sorted((p["hs"], p["as"]))) and s != final for s in a["scores"]):
            sc += 3 if p["scoring"] else 0
            f += ["score"] if p["scoring"] else []
        if any(f"{y} yd" in p["text"] or f"for {y} yard" in p["text"] for y in a["yards"]):
            sc += 2
            f.append("yards")
        if any(IN_PLAY.get(w, w.split("-")[0][:5]) in p["text"] for w in a["actions"]):
            sc += 1
            f.append("action")
        if "player" not in f and "clock" not in f and "score" not in f:
            continue
        if league == "nba" and "clock" not in f and "score" not in f and "action" not in f:
            continue                     # a name alone (e.g. the assist man) is not this play
        if sc > bs:
            best, bs, bf, tie = p, sc, "+".join(f), False
        elif sc == bs and best is not None and p["seq"] != best["seq"]:
            tie = True
    if tie or bs < 4:
        return None, 0, ""
    return best, bs, bf


def build(archive: Path, league: str) -> tuple[list[dict], dict]:
    from redzone.lab import splits
    try:
        from redzone.lab import evaldata
        end = evaldata.window_end(league, None)
    except Exception:  # noqa: BLE001
        end = None
    d = archive / league
    an = splits.archive_nights(d, end)
    s = splits.league_split(league, an)
    fit = set(s.train + s.valid)
    rows, stats = [], {"fit_nights": len(fit), "locked_nights_skipped": len(s.test), "games": 0,
                       "games_with_recap": 0, "sentences": 0, "matched": 0}
    for night in sorted(fit):
        for gid in an[night]:
            p = d / f"summary_{gid}.json"
            if not p.exists():
                continue
            stats["games"] += 1
            js = json.loads(p.read_text())
            story = (js.get("article") or {}).get("story") or ""
            if len(story) < 200:
                continue
            stats["games_with_recap"] += 1
            plays, _ = plays_of(league, js)
            if not plays:
                continue
            last = [q for q in plays if q["hs"] is not None]
            final = tuple(sorted((last[-1]["hs"], last[-1]["as"]))) if last else ()
            seen = set()
            for sent, ids, names in sentences(story):
                stats["sentences"] += 1
                best, sc, f = match(league, plays, ids, names, anchors(league, sent), final)
                if best is None or best["seq"] in seen:
                    continue
                seen.add(best["seq"])
                c = best["clock"]
                rows.append({"league": league, "night": night, "gid": gid, "seq": best["seq"], "period": best["period"],
                             "clock": f"{int(c // 60)}:{c % 60:04.1f}" if c is not None else "", "clock_s": c,
                             "team": best["team"], "scoring": int(best["scoring"]), "match_score": sc,
                             "matched_fields": f, "source": "espn_recap"})
    stats["matched"] = len(rows)
    stats["games_with_highlight"] = len({r["gid"] for r in rows})
    return rows, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", type=Path, default=Path("data/archive"))
    ap.add_argument("--out", type=Path, default=Path("out/highlights"))
    ap.add_argument("--league", nargs="+", default=["nba", "nfl"])
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    cov = {"note": "plays only, no text; fit (train+valid) nights only; personal-use, not for commercial release"}
    for lg in a.league:
        rows, st = build(a.archive, lg)
        cov[lg] = st
        with (a.out / f"{lg}_recap_highlights.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["league"])
            w.writeheader()
            w.writerows(rows)
        print(f"{lg}: {st['matched']} highlight plays in {st['games_with_highlight']}/{st['games_with_recap']} "
              f"recapped games, {st['fit_nights']} fit nights ({st['locked_nights_skipped']} locked skipped)")
    (a.out / "coverage.json").write_text(json.dumps(cov, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

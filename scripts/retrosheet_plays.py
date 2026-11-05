#!/usr/bin/env python3
"""Retrosheet event files -> the ESPN-format MLB play dicts the tokenizer reads (Wave 1, green).

    python scripts/retrosheet_plays.py 2023 --out RAW      # writes RAW/mlb/retro_2023.jsonl.gz

Serves: North Star "data scale" (the NFM's only green MLB play-by-play; docs/datasources/ingest-plan.md Wave 1 #3);
owner: lane 1 (Noggin training).

Source: the Chadwick Baseball Bureau mirror of Retrosheet on GitHub (raw.githubusercontent.com/chadwickbureau/
retrosheet, branch master: seasons/<y>/TEAM<y> and <y><TEAM>.EV[AN]). Static files, fetched one at a time with a gap
(MIN_GAP_S). Retrosheet's terms apply and its notice travels with every derived file and model card:

    The information used here was obtained free of charge from and is copyrighted by Retrosheet.
    Interested parties may contact Retrosheet at "www.retrosheet.org".

Each game becomes {"id", "date", "matchup": "AWAY @ HOME", "plays": [...]}, one dict per play line (no-play "NP"
lines dropped), plus a "Pitching Change" entry when a pitcher (fielding position 1) is subbed in mid-game and an
"End Inning" entry at each half-inning change. Retrosheet has no wall clocks, so "wallclock" is absent and the
tokenizer writes UNK_DT: no time label is ever invented. Night = the game date. Nothing on or after
factory.EVAL_FIRST_NIGHT["mlb"] is written.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIRROR = "https://raw.githubusercontent.com/chadwickbureau/retrosheet/master/seasons/{y}/{name}"
MIN_GAP_S = 0.5
NOTICE = ('The information used here was obtained free of charge from and is copyrighted by Retrosheet. '
          'Interested parties may contact Retrosheet at "www.retrosheet.org".')
# event-code prefix -> the ESPN play type text (longest prefixes first)
KINDS = (("HR", "Home Run"), ("IW", "Intentional Walk"), ("HP", "Hit By Pitch"), ("FC", "Fielders Choice"),
         ("SB", "Stolen Base"), ("CS", "Caught Stealing"), ("PO", "Pickoff"), ("WP", "Wild Pitch"),
         ("PB", "Passed Ball"), ("BK", "Balk"), ("DI", "Defensive Indifference"), ("OA", "Other Advance"),
         ("FLE", "Error"), ("H", "Home Run"), ("K", "Strikeout"), ("W", "Walk"), ("I", "Intentional Walk"),
         ("S", "Single"), ("D", "Double"), ("T", "Triple"), ("E", "Error"), ("C", "Catcher Interference"))


def kind(event: str) -> str:
    if event[:1].isdigit():
        return "Out"
    for pre, name in KINDS:
        if event.startswith(pre):
            return name
    return "Play"


def runs(event: str) -> int:
    """Runs scored on one play: explicit runner moves to home ("-H"), plus the batter on a home run unless the
    advance list already moves him."""
    body, _, adv = event.partition(".")
    moves = [m for m in adv.split(";") if m]
    # "3-H" scores; "3XH(25E2)" is out at home unless an error in the parentheses negates the out
    n = sum(1 for m in moves if "-H" in m.split("(")[0] or ("XH" in m.split("(")[0] and "E" in m.partition("(")[2]))
    homer = body.startswith("HR") or (body.startswith("H") and not body.startswith("HP"))
    if homer and not any(m.startswith("B-") or m.startswith("BX") for m in moves):
        n += 1
    return n


def parse(text: str) -> list[dict]:
    """One event file's text -> games."""
    games, g = [], None
    pitcher = {}
    for line in text.splitlines():
        f = line.split(",")
        tag = f[0]
        if tag == "id":
            g = {"id": f[1], "info": {}, "plays": [], "score": [0, 0], "half": None, "started": False}
            games.append(g)
            pitcher = {}
        elif g is None:
            continue
        elif tag == "info" and len(f) >= 3:
            g["info"][f[1]] = f[2]
        elif tag in ("start", "sub") and len(f) >= 6:
            side, pos = f[3], f[5]
            if pos == "1":
                if tag == "sub" and g["started"] and pitcher.get(side) != f[1]:
                    g["plays"].append(_entry(g, "Pitching Change", f"pitcher change: {f[2].strip(chr(34))}"))
                pitcher[side] = f[1]
        elif tag == "play" and len(f) >= 7:
            inning, side, ev = int(f[1]), f[2], f[6]
            half = (inning, side)
            if g["half"] is not None and half != g["half"]:
                g["plays"].append(_entry(g, "End Inning", "end of half-inning"))
            g["half"], g["started"] = half, True
            if ev == "NP":
                continue
            r = runs(ev)
            if r:
                g["score"][0 if side == "0" else 1] += r      # side 0 = visitors batting
            p = _entry(g, kind(ev), ev)
            if r:
                p["scoringPlay"], p["scoreValue"] = True, r
            g["plays"].append(p)
    out = []
    for g in games:
        if g["plays"]:
            g["plays"].append(_entry(g, "End Inning", "end of game"))
        d = g["info"].get("date", "").replace("/", "-")
        out.append({"id": g["id"], "date": d, "matchup": f"{g['info'].get('visteam', '?')} @ "
                    f"{g['info'].get('hometeam', '?')}", "plays": g["plays"], "source": "retrosheet"})
    return out


def _entry(g: dict, type_text: str, text: str) -> dict:
    inning = g["half"][0] if g["half"] else 1
    return {"type": {"text": type_text}, "text": text, "period": {"number": inning},
            "awayScore": g["score"][0], "homeScore": g["score"][1]}


def _get(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read().decode("latin-1")
    except Exception as e:  # noqa: BLE001 - a missing team file is skipped and counted
        print(f"  {url.rsplit('/', 1)[-1]}: {type(e).__name__}", flush=True)
        return None


def fetch_season(year: int, out: Path) -> Path | None:
    """All of one season's regular-season event files -> out/mlb/retro_<year>.jsonl.gz (cached)."""
    from redzone.noggin import factory as F
    dst = out / "mlb" / f"retro_{year}.jsonl.gz"
    if dst.exists():
        return dst
    teams = _get(MIRROR.format(y=year, name=f"TEAM{year}"))
    if not teams:
        return None
    cut = F.EVAL_FIRST_NIGHT.get("mlb", "99999999")
    games = []
    for row in teams.splitlines():
        t = row.split(",")
        if len(t) < 2 or t[1] not in ("A", "N") or t[0] in ("ALS", "NLS") or "all-star" in row.lower() or "all star" in row.lower():
            continue
        time.sleep(MIN_GAP_S)
        txt = _get(MIRROR.format(y=year, name=f"{year}{t[0]}.EV{t[1]}"))
        if txt:
            games += [g for g in parse(txt) if g["date"].replace("-", "") < cut]
    if not games:
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f:
        for g in games:
            f.write(json.dumps(g) + "\n")
    tmp.replace(dst)
    print(f"retrosheet {year}: {len(games)} games", flush=True)
    return dst


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("years", type=int, nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    for y in a.years:
        fetch_season(y, a.out)
    print(NOTICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

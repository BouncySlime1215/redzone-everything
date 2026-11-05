#!/usr/bin/env python3
"""Pre-game line movement for crew H's daily pre-game report (Nick 12:00 PM Nov 5). DERIVED numbers only.

Per upcoming game: spread / total / moneyline at open and now with the move (ESPN summary `pickcenter`, public,
plan B personal use), and the Polymarket game price now vs 24 h ago with its traded volume (public read APIs).
Public betting %: no free source whose terms allow automated reads was found (Action Network, Covers, VSiN
forbid scraping), so the field stays null and the report says so. Kalshi is red. Lines are importance
INPUTS only: never shown as betting info, never a nudge. Personal-use, not for commercial release.

    python scripts/markets/pregame_lines.py --league nba --date 2026-11-05 --out out/pregame
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polymarket as PM  # noqa: E402

TAG = "personal-use, not for commercial release"
SITE = {"nba": "basketball/nba", "nfl": "football/nfl", "cbb": "basketball/mens-college-basketball",
        "cfb": "football/college-football", "nhl": "hockey/nhl", "mlb": "baseball/mlb"}


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(v).replace("EVEN", "+100").replace("PK", "0"))
    return float(m.group(0)) if m else None


def implied(ml: float | None) -> float | None:
    if ml is None or ml == 0:
        return None
    return 100.0 / (ml + 100.0) if ml > 0 else -ml / (-ml + 100.0)


def vig_free(h: float | None, a: float | None) -> float | None:
    ph, pa = implied(h), implied(a)
    return None if ph is None or pa is None else round(ph / (ph + pa), 4)


def _oc(d: dict | None, *path) -> tuple[float | None, float | None]:
    """(open, close/current) number from ESPN's {open:{line|odds}, close:{...}} blocks."""
    for k in path:
        d = (d or {}).get(k) if isinstance(d, dict) else None
    d = d or {}
    def g(x):
        x = d.get(x) or {}
        return _num(x.get("line") if x.get("line") is not None else x.get("odds"))
    return g("open"), g("close") if d.get("close") else g("current")


def parse_pickcenter(pc: list[dict]) -> dict:
    """ESPN summary `pickcenter` -> open/now/move for spread (home), total and vig-free home moneyline prob."""
    out = {"provider": None, "spread_open": None, "spread_now": None, "total_open": None, "total_now": None,
           "ml_p_home_open": None, "ml_p_home_now": None}
    for p in pc or []:
        out["provider"] = (p.get("provider") or {}).get("name")
        out["spread_now"] = _num(p.get("spread"))
        out["total_now"] = _num(p.get("overUnder"))
        h, a = p.get("homeTeamOdds") or {}, p.get("awayTeamOdds") or {}
        out["ml_p_home_now"] = vig_free(_num(h.get("moneyLine")), _num(a.get("moneyLine")))
        so, sc = _oc(p, "pointSpread", "home")
        to, tc = _oc(p, "total", "over")
        mho, mhc = _oc(p, "moneyline", "home")
        mao, mac = _oc(p, "moneyline", "away")
        out["spread_open"] = so
        out["spread_now"] = out["spread_now"] if out["spread_now"] is not None else sc
        out["total_open"] = to
        out["total_now"] = out["total_now"] if out["total_now"] is not None else tc
        out["ml_p_home_open"] = vig_free(mho, mao)
        if out["ml_p_home_now"] is None:
            out["ml_p_home_now"] = vig_free(mhc, mac)
        break                                   # first provider only (ESPN lists its default book first)
    for k in ("spread", "total", "ml_p_home"):
        o, n = out[f"{k}_open"], out[f"{k}_now"]
        out[f"{k}_move"] = None if o is None or n is None else round(n - o, 4)
    return out


def run(league: str, date: str, out: Path) -> dict:
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = PM.UA
    base = f"https://site.api.espn.com/apis/site/v2/sports/{SITE[league]}"
    sb = s.get(f"{base}/scoreboard", params={"dates": date.replace("-", "")}, timeout=20).json()
    rows, debug = [], None
    poly = {}
    try:
        from poly_live_trigger import tonight_markets
        poly = {m["game_key"].split(":")[-1]: m for m in tonight_markets(league, date).values()}
    except SystemExit:
        poly = {}
    c = PM.Client()
    for ev in sb.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        side = {x.get("homeAway"): (x.get("team") or {}).get("abbreviation", "") for x in comp.get("competitors") or []}
        matchup = f"{side.get('away', '')}@{side.get('home', '')}"
        time.sleep(1.0)
        summ = s.get(f"{base}/summary", params={"event": ev.get("id")}, timeout=20)
        pc = summ.json().get("pickcenter") if summ.status_code == 200 else None
        if debug is None and pc:
            debug = {"pickcenter_keys": sorted(pc[0].keys())}
        r = {"league": league, "date": date, "espn_id": ev.get("id"), "matchup": matchup,
             "start_utc": ev.get("date"), **parse_pickcenter(pc or []),
             "public_bet_pct_home": None, "public_bet_pct_note": "no free source whose terms allow automated reads"}
        pm = poly.get(matchup) or next((v for k, v in poly.items() if set(k.split("@")) == set(matchup.split("@"))), None)
        if pm:
            now = int(time.time())
            h = c.get(f"{PM.CLOB}/prices-history", {"market": pm["market_id"], "startTs": now - 26 * 3600,
                                                   "endTs": now, "fidelity": 60}) or {}
            pts = sorted(h.get("history") or [], key=lambda x: x["t"])
            f = (lambda p: 1.0 - p) if pm["invert"] else (lambda p: p)
            if pts:
                ago = min(pts, key=lambda x: abs(x["t"] - (now - 86400)))
                r.update({"poly_p_home_now": round(f(float(pts[-1]["p"])), 4),
                          "poly_p_home_24h_ago": round(f(float(ago["p"])), 4)})
                r["poly_move_24h"] = round(r["poly_p_home_now"] - r["poly_p_home_24h_ago"], 4)
            try:
                r["poly_volume_usd"] = float(((pm.get("check") or {}).get("volumeNum")) or 0) or None
            except (TypeError, ValueError):
                r["poly_volume_usd"] = None
        r["tag"] = TAG
        rows.append(r)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"pregame_lines_{league}_{date.replace('-', '')}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    res = {"league": league, "date": date, "games": len(rows),
           "with_spread": sum(r["spread_now"] is not None for r in rows),
           "with_open": sum(r["spread_open"] is not None for r in rows),
           "with_poly": sum("poly_p_home_now" in r for r in rows), "debug": debug,
           "generated_utc": datetime.now(timezone.utc).isoformat(), "file": str(p)}
    print(json.dumps(res))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--league", default="nba", choices=sorted(SITE))
    ap.add_argument("--date", default=datetime.now(PM.EASTERN).strftime("%Y-%m-%d"))
    ap.add_argument("--out", type=Path, default=Path("out/pregame"))
    a = ap.parse_args(argv)
    run(a.league, a.date, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

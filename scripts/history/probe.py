"""How far back does each ESPN league go, and are older seasons as complete as recent ones? Numbers only, no data kept.

Why: B-01 asked "unknowns about older seasons answered" (WORKPLAN): the corpus plan (tracks/B-foundation-model.md section 4)
says whether older college and women's games have usable play-by-play is UNKNOWN until the first fetch, and older
NBA and NHL seasons lack wall clocks. This module measures it from real responses at the same polite rate as the fetcher.

For each league and season it reads the calendar (one request: does the season exist, how many units), then for `samples`
units spread across the season reads the slate (how many games, how many final) and one final game's summary, and records
shape numbers: plays, the share of plays with a wall clock, a game clock, a period, a score, a type, athlete ids; response
bytes; whether football keeps its plays under `drives`. Nothing from the plays is saved. Fenced nights are never sampled.
"""
from __future__ import annotations

import statistics
from typing import Any, Callable

from .espn import BASE, _get, flatten_plays, parse_calendar, parse_stub, probe_params, unit_fenced, unit_params
from .leagues import LEAGUES, fenced
from .net import Blocked, FetchError, PoliteClient

HEADER_KEYS = ("content-type", "content-encoding", "cache-control", "server", "age", "etag", "last-modified", "x-cache", "expires")


def _frac(plays: list[dict], pred: Callable[[dict], bool]) -> float:
    return round(sum(1 for p in plays if pred(p)) / len(plays), 3) if plays else 0.0


def summary_shape(lg_code: str, summary: dict, nbytes: int) -> dict[str, Any]:
    plays = flatten_plays(LEAGUES[lg_code], summary)
    return {
        "plays": len(plays), "bytes": nbytes, "wallclock_available": summary.get("wallclockAvailable"),
        "has_plays_key": "plays" in summary, "has_drives_key": "drives" in summary,
        "wall": _frac(plays, lambda p: bool(p.get("wallclock"))),
        "clock": _frac(plays, lambda p: _get(p, "clock", "displayValue") not in (None, "")),
        "period": _frac(plays, lambda p: _get(p, "period", "number") is not None),
        "score": _frac(plays, lambda p: p.get("homeScore") is not None and p.get("awayScore") is not None),
        "type": _frac(plays, lambda p: bool(_get(p, "type", "text"))),
        "athletes": _frac(plays, lambda p: bool(p.get("participants"))),
        "top_keys": sorted(summary.keys())[:30],
    }


def pick_units(lg_code: str, units: list[dict], samples: int) -> list[dict]:
    """`samples` units spread evenly through the season, skipping any unit that touches a fence."""
    lg = LEAGUES[lg_code]
    ok = [u for u in units if not unit_fenced(lg, u) and (not u.get("start") or not fenced(lg_code, u["start"]))]
    if not ok or samples <= 0:
        return []
    idx = sorted({min(len(ok) - 1, int(len(ok) * (i + 1) / (samples + 1))) for i in range(samples)})
    return [ok[i] for i in idx]


def spread(n: int, k: int) -> list[int]:
    """k indices spread evenly over range(n) (all of them when k >= n)."""
    if n <= 0 or k <= 0:
        return []
    if k >= n:
        return list(range(n))
    return sorted({min(n - 1, int(n * (i + 0.5) / k)) for i in range(k)})


def probe_season(client: PoliteClient, lg_code: str, season: int, samples: int, per_slate: int = 1) -> dict[str, Any]:
    lg = LEAGUES[lg_code]
    out: dict[str, Any] = {"league": lg_code, "season": season, "samples": []}
    r = client.get_json(f"{BASE}/{lg.path}/scoreboard", probe_params(lg, season))
    out["headers"] = {k: r.headers[k] for k in HEADER_KEYS if k in r.headers}
    lobj = (r.data.get("leagues") or [{}])[0] if isinstance(r.data.get("leagues"), list) else {}
    cal = parse_calendar(lg, season, r.data)
    out["calendar"] = {"units": len(cal["units"]), "whitelist": lobj.get("calendarIsWhitelist"), "season_year": cal["season_year"],
                       "start": str(lobj.get("calendarStartDate") or "")[:10], "end": str(lobj.get("calendarEndDate") or "")[:10],
                       "note": cal["note"], "events_on_probe_response": len(r.data.get("events") or [])}
    if not cal["units"] or samples <= 0:
        return out
    for unit in pick_units(lg_code, cal["units"], samples):
        s: dict[str, Any] = {"unit": unit["key"]}
        sl = client.get_json(f"{BASE}/{lg.path}/scoreboard", unit_params(lg, season, unit))
        events = [e for e in (sl.data.get("events") or []) if isinstance(e, dict)]
        stubs = [x for x in (parse_stub(e) for e in events) if x]
        final = [x for x in stubs if x["state"] == "post" and not fenced(lg_code, x["night"])]
        s.update(events=len(events), final=len(final), slate_bytes=sl.nbytes)
        s["games"] = []
        for i in spread(len(final), per_slate):
            g = final[i]
            try:
                gr = client.get_json(f"{BASE}/{lg.path}/summary", {"event": g["id"]}, allow_404=True)
                s["games"].append({"id": g["id"], "status": gr.status, **(summary_shape(lg_code, gr.data, gr.nbytes) if gr.status == 200 else {})})
            except Blocked:
                raise
            except FetchError as e:
                s["games"].append({"id": g["id"], "error": str(e)[:160]})
        out["samples"].append(s)
    return out


def season_games(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [g for s in row.get("samples", []) for g in s.get("games", []) if g.get("status") == 200]


def season_stats(row: dict[str, Any]) -> dict[str, Any]:
    """Sampled games, how many have plays, median plays, and the shares with a wall clock and a game clock (among games with plays)."""
    gs = season_games(row)
    wp = [g for g in gs if g.get("plays")]
    pct = lambda key: round(sum(1 for g in wp if g[key] >= 0.9) / len(wp), 2) if wp else None   # noqa: E731
    return {"sampled": len(gs), "with_plays": len(wp), "median_plays": statistics.median(g["plays"] for g in wp) if wp else None,
            "wall": pct("wall"), "clock": pct("clock"), "units": (row.get("calendar") or {}).get("units")}


def probe(client: PoliteClient, leagues: list[str], seasons: list[int], samples: int, log: Callable[[str], None] = print,
          per_slate: int = 1) -> dict[str, Any]:
    evidence: dict[str, Any] = {"samples_per_season": samples, "games_per_slate": per_slate, "leagues": {}}
    for code in leagues:
        rows = []
        for season in seasons:
            try:
                row = probe_season(client, code, season, samples, per_slate)
            except Blocked:
                raise
            except FetchError as e:
                row = {"league": code, "season": season, "error": str(e)[:200]}
            rows.append(row)
            st = season_stats(row)
            log(f"{code} {season}: units={st['units']} sampled={st['sampled']} with_plays={st['with_plays']} median_plays={st['median_plays']} "
                f"wall>=0.9={st['wall']} clock>=0.9={st['clock']} {row.get('error', '')}")
        evidence["leagues"][code] = {"seasons": rows}
    return evidence


def table(evidence: dict[str, Any]) -> str:
    lines = [f"{'league':<6} {'season':>6} {'units':>5} {'sampled':>7} {'w/plays':>7} {'median plays':>12} {'wall>=.9':>9} {'clock>=.9':>9}"]
    for d in evidence["leagues"].values():
        for r in d["seasons"]:
            st = season_stats(r)
            f = lambda v: "-" if v is None else str(v)   # noqa: E731
            lines.append(f"{r['league']:<6} {r['season']:>6} {f(st['units']):>5} {st['sampled']:>7} {st['with_plays']:>7} {f(st['median_plays']):>12} {f(st['wall']):>9} {f(st['clock']):>9}")
    return "\n".join(lines)

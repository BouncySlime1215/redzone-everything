"""CONDITIONS context shards (Crew B, worker B3): hourly weather at kickoff for outdoor games.

    # historical (cloud: Kaggle / GitHub Actions; this container cannot reach open-meteo.com)
    python scripts/context/weather.py archive --league nfl --games games.csv --out shards/ --cache wx-cache/
    python scripts/context/weather.py archive --league mlb --retro-dir gamelogs/ --out shards/ --cache wx-cache/
    # tonight's pregame (asof = fetch time, must be before kickoff)
    python scripts/context/weather.py forecast --league nfl --games games.csv --out shards/ --hours 36
    # count the calls a run would make, no network
    python scripts/context/weather.py archive --league nfl --games games.csv --out shards/ --plan-only

Source: Open-Meteo historical archive https://archive-api.open-meteo.com/v1/archive (ERA5-based reanalysis),
CC BY 4.0, free for non-commercial use, <= 10,000 calls/day. ONE call per venue-season (start_date..end_date
spanning that venue's games), so NFL 1999-2026 is ~700 calls and MLB 2000-2025 ~800: one day's budget for both,
and the JSON is cached on disk so a rerun costs nothing. Forecast: https://api.open-meteo.com/v1/forecast (same
terms), one call per venue per run.

Fallback (documented, not wired): NOAA NCEI Global Hourly (ISD) / GHCNh, public domain, station nearest the venue:
https://www.ncei.noaa.gov/access/services/data/v1?dataset=global-hourly&stations=<USAF+WBAN>&startDate=..&endDate=..
&format=json (see noaa_isd_url). Use it if Open-Meteo is down or its terms change.

Facts (kind "new", source "open-meteo-archive" / "open-meteo-forecast"), per game (team null):
    temp_f, wind_mph (10 m), precip_mm (sum over the hour ending at the kickoff hour), snow (1 if snowfall > 0 in the
    kickoff hour or the hour before), plus roof_open (derived from the schedule) for retractable roofs.
asof: archive rows use asof = kickoff - 1 min (the reanalysis value at the kickoff hour stands in for the
forecast a viewer had at kickoff); forecast rows use asof = fetch time and are dropped when fetch >= kickoff.
Dome / closed-roof games get no weather rows. Venues: redzone/context/venues.csv (lat/lon/tz/roof); CFB venues
come from CFBD /venues (scripts/context/cfbd_context.py venues) in the same columns.
Why: Hourly kickoff weather for outdoor games, as a context shard (docstring, Data B commit).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from redzone.context.schedules import load_nfl, load_retrosheet  # noqa: E402
from redzone.context.tokens import PERSONAL_USE, Fact, apply_edges, fit_edges, ledger_counts, to_frame  # noqa: E402

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
HOURLY = "temperature_2m,precipitation,snowfall,wind_speed_10m"
LICENSE = f"Open-Meteo CC BY 4.0, free for non-commercial use, credit Open-Meteo (open-meteo.com); {PERSONAL_USE}"
VENUES_CSV = ROOT / "redzone" / "context" / "venues.csv"
TRAIN_CUTOFF = {"nfl": "2022-09-01", "mlb": "2022-03-01", "cfb": "2022-08-01"}
DAILY_CALL_CAP = 10_000
OUTDOOR = {"outdoors", "open", "retractable", "outdoor"}


def load_venues(path=VENUES_CSV, league: str | None = None) -> pd.DataFrame:
    v = pd.read_csv(path, dtype={"venue_id": str})
    return v[v["league"] == league].copy() if league else v


def with_venues(games: pd.DataFrame, venues: pd.DataFrame) -> pd.DataFrame:
    """Join lat/lon/tz; MLB kickoff (no times in the logs) = 13:10 local day / 19:10 local night."""
    g = games.merge(venues[["venue_id", "lat", "lon", "tz", "roof"]].rename(columns={"roof": "venue_roof"}),
                    on="venue_id", how="left")
    if "daynight" in g:
        k = []
        for _, r in g.iterrows():
            if pd.isna(r["tz"]):
                k.append(r["kickoff_utc"]); continue
            local = r["kickoff_utc"].tz_convert(None).normalize() + pd.Timedelta(hours=13 if r["daynight"] == "D" else 19,
                                                                                 minutes=10)
            k.append(local.tz_localize(r["tz"], nonexistent="shift_forward", ambiguous=False).tz_convert("UTC"))
        g["kickoff_utc"] = pd.to_datetime(pd.Series(k, index=g.index), utc=True)
    return g


def is_outdoor(roof, venue_roof) -> bool:
    if isinstance(roof, str) and roof:
        return roof in OUTDOOR - {"retractable"}           # per-game nflverse roof: outdoors / open / dome / closed
    return isinstance(venue_roof, str) and venue_roof in {"outdoors", "outdoor"}


def plan_calls(g: pd.DataFrame) -> pd.DataFrame:
    """One archive call per venue-season over outdoor games with known coordinates."""
    m = g.apply(lambda r: is_outdoor(r.get("roof"), r.get("venue_roof")), axis=1) & g["lat"].notna()
    o = g[m]
    d = o["kickoff_utc"].dt.tz_convert("UTC")
    o = o.assign(day=d.dt.strftime("%Y-%m-%d"))
    return (o.groupby(["venue_id", "season"])
             .agg(lat=("lat", "first"), lon=("lon", "first"), start=("day", "min"), end=("day", "max"),
                  games=("game_id", "size"))
             .reset_index())


def archive_url(lat, lon, start, end) -> str:
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "start_date": start, "end_date": end,
         "hourly": HOURLY, "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "mm",
         "timezone": "GMT"}
    return ARCHIVE + "?" + urllib.parse.urlencode(q)


def forecast_url(lat, lon, start, end) -> str:
    return archive_url(lat, lon, start, end).replace(ARCHIVE, FORECAST)


def noaa_isd_url(station: str, start: str, end: str) -> str:
    """Documented fallback: NOAA NCEI Global Hourly (public domain). Needs a venue -> station crosswalk."""
    q = {"dataset": "global-hourly", "stations": station, "startDate": start, "endDate": end, "format": "json",
         "units": "metric"}
    return "https://www.ncei.noaa.gov/access/services/data/v1?" + urllib.parse.urlencode(q)


def parse_hourly(js: dict) -> pd.DataFrame:
    """Open-Meteo JSON (timezone=GMT, iso8601 times) -> UTC-indexed hourly frame. Units must match the request."""
    h = js["hourly"]
    u = js.get("hourly_units", {})
    if u.get("temperature_2m") not in (None, "°F"):
        raise ValueError(f"expected °F, got {u.get('temperature_2m')}")
    if u.get("wind_speed_10m") not in (None, "mp/h", "mph"):
        raise ValueError(f"expected mph, got {u.get('wind_speed_10m')}")
    t = pd.to_datetime(h["time"]).tz_localize("UTC")
    return pd.DataFrame({"temp_f": h["temperature_2m"], "wind_mph": h["wind_speed_10m"],
                         "precip_mm": h["precipitation"], "snow_cm": h["snowfall"]}, index=t).astype(float)


def facts_for(games: pd.DataFrame, hourly: pd.DataFrame, source: str, asof_utc: pd.Timestamp | None = None) -> list[Fact]:
    """Weather facts for games at one venue. asof_utc None = archive rule (kickoff - 1 min); else fetch time."""
    out = []
    for _, r in games.iterrows():
        k = r["kickoff_utc"].tz_convert("UTC")
        if asof_utc is not None and asof_utc >= k:
            continue                              # a forecast fetched at/after kickoff is not a pregame fact
        hour = k.floor("h")
        if hour not in hourly.index:
            continue
        row = hourly.loc[hour]
        prev = hourly.loc[hour - pd.Timedelta(hours=1)] if hour - pd.Timedelta(hours=1) in hourly.index else row
        asof = (k - pd.Timedelta(minutes=1)) if asof_utc is None else asof_utc
        a = asof.strftime("%Y-%m-%dT%H:%M:%SZ")
        vals = {"temp_f": round(float(row["temp_f"]), 1), "wind_mph": round(float(row["wind_mph"]), 1),
                "precip_mm": round(float(row["precip_mm"]), 2),
                "snow": int((row["snow_cm"] > 0) or (prev["snow_cm"] > 0))}
        for f, v in vals.items():
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            out.append(Fact(r["game_id"], None, a, f, v, "new", source, LICENSE))
        if r.get("venue_roof") == "retractable" and isinstance(r.get("roof"), str):
            out.append(Fact(r["game_id"], None, a, "roof_open", int(r["roof"] == "open"), "derived",
                            "derived-schedule-roof", "derived from nflverse schedules (CC-BY-4.0); " + PERSONAL_USE))
    return out


def fetch_json(url: str, cache: Path | None, key: str, pause_s: float = 0.25, tries: int = 4) -> dict:
    if cache:
        p = cache / f"{key}.json"
        if p.exists():
            return json.loads(p.read_text())
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "redzone-personal/0.1 (non-commercial)"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                js = json.loads(resp.read())
            if cache:
                cache.mkdir(parents=True, exist_ok=True)
                (cache / f"{key}.json").write_text(json.dumps(js))
            time.sleep(pause_s)
            return js
        except Exception as e:   # 429 / 5xx: back off
            last = e
            time.sleep(2 ** i * 15)
    print(f"open-meteo failed for {key}, skipped: {last}", flush=True)
    return {}


def load_games(a) -> pd.DataFrame:
    if a.league == "nfl":
        return load_nfl(a.games)
    if a.league == "mlb":
        return load_retrosheet(a.retro_dir)
    raise SystemExit("cfb: use scripts/context/cfbd_context.py (CFBD /games/weather), or pass --games-csv with "
                     "game_id,season,kickoff_utc,venue_id,roof and --venues from cfbd_context.py venues")


def run_archive(a) -> dict:
    g = load_games(a) if not a.games_csv else pd.read_csv(a.games_csv, parse_dates=["kickoff_utc"])
    if a.first:
        g = g[g["season"] >= a.first]
    if a.last:
        g = g[g["season"] <= a.last]
    g = g[g["home_score"].notna()] if "home_score" in g else g
    g = with_venues(g, load_venues(a.venues, a.league))
    plan = plan_calls(g)
    missing = sorted(set(g.loc[g["lat"].isna(), "venue_id"].dropna()))
    print(f"{a.league}: {len(plan)} archive calls planned (cap {DAILY_CALL_CAP}/day); venues without coords: {missing}")
    if a.plan_only:
        return {"calls": len(plan), "missing_venues": missing}
    if len(plan) > a.max_calls:
        plan = plan.head(a.max_calls)
        print(f"capped to {a.max_calls} calls this run; rerun tomorrow (cache keeps what landed)")
    facts = []
    cache = Path(a.cache) if a.cache else None
    for i, (_, c) in enumerate(plan.iterrows()):
        # A venue-season spans ~5 months, which Open-Meteo bills as ~10 calls (it weights by 2-week blocks), and the
        # free tier allows 5,000 weighted calls an hour: pace at --pause seconds and skip a call that keeps failing.
        js = fetch_json(archive_url(c["lat"], c["lon"], c["start"], c["end"]), cache, f"{c['venue_id']}_{c['season']}",
                        pause_s=a.pause)
        if i % 25 == 0:
            print(f"{i}/{len(plan)} calls, {len(facts)} facts", flush=True)
        if not js:
            continue
        hourly = parse_hourly(js)
        sub = g[(g["venue_id"] == c["venue_id"]) & (g["season"] == c["season"])]
        sub = sub[sub.apply(lambda r: is_outdoor(r.get("roof"), r.get("venue_roof")), axis=1)]
        facts += facts_for(sub, hourly, "open-meteo-archive")
    return write(a.league, g, facts, Path(a.out), "weather")


def run_forecast(a) -> dict:
    now = pd.Timestamp.now(tz="UTC")
    g = load_games(a)
    g = with_venues(g, load_venues(a.venues, a.league))
    g = g[(g["kickoff_utc"] > now) & (g["kickoff_utc"] <= now + pd.Timedelta(hours=a.hours))]
    plan = plan_calls(g)
    facts = []
    for _, c in plan.iterrows():
        js = fetch_json(forecast_url(c["lat"], c["lon"], c["start"], c["end"]), None, "fc")
        fetched = pd.Timestamp.now(tz="UTC")
        sub = g[g["venue_id"] == c["venue_id"]]
        sub = sub[sub.apply(lambda r: is_outdoor(r.get("roof"), r.get("venue_roof")), axis=1)]
        facts += facts_for(sub, parse_hourly(js), "open-meteo-forecast", asof_utc=fetched)
    df = to_frame(facts)
    d = Path(a.out) / f"{a.league}-weather"
    d.mkdir(parents=True, exist_ok=True)
    ep = d / "edges.json"
    if ep.exists() and len(df):
        df = apply_edges(df, json.loads(ep.read_text())["edges"])
    df.to_parquet(d / f"forecast-{now.strftime('%Y%m%dT%H%M')}.parquet", index=False)
    return ledger_counts(df)


def write(league, games, facts, out: Path, kind: str) -> dict:
    df = to_frame(facts)
    d = out / f"{league}-{kind}"
    d.mkdir(parents=True, exist_ok=True)
    kick = games.set_index("game_id")["kickoff_utc"]
    seas = games.set_index("game_id")["season"]
    train = df["game_id"].map(kick).lt(pd.Timestamp(TRAIN_CUTOFF[league], tz="UTC")).to_numpy()
    edges = fit_edges(df, train)
    (d / "edges.json").write_text(json.dumps({"train_cutoff_utc": TRAIN_CUTOFF[league], "n_buckets": 16,
                                              "edges": edges}, indent=1))
    df = apply_edges(df, edges)
    for s, part in df.groupby(df["game_id"].map(seas)):
        part.to_parquet(d / f"{int(s)}.parquet", index=False)
    c = ledger_counts(df)
    print(json.dumps(c))
    return c


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["archive", "forecast"])
    ap.add_argument("--league", choices=["nfl", "mlb", "cfb"], required=True)
    ap.add_argument("--games")
    ap.add_argument("--retro-dir")
    ap.add_argument("--games-csv", help="generic games table (cfb hook)")
    ap.add_argument("--venues", default=str(VENUES_CSV))
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache")
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--hours", type=float, default=36)
    ap.add_argument("--max-calls", type=int, default=9000)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--last", type=int, default=0, help="last season to include (chunk a long backfill)")
    ap.add_argument("--pause", type=float, default=8.0, help="seconds between archive calls (free-tier hourly cap)")
    a = ap.parse_args(argv)
    return run_archive(a) if a.mode == "archive" else run_forecast(a)


if __name__ == "__main__":
    main()

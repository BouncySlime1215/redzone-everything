"""CFB context shards from the CollegeFootballData API (Crew B, worker B3). Runs on Kaggle / GitHub Actions.

    CFBD_API_KEY=<secret> python scripts/context/cfbd_context.py facts --years 2014-2025 --out shards/ --cache cfbd-cache/
    CFBD_API_KEY=<secret> python scripts/context/cfbd_context.py venues --out redzone/context/venues_cfb.csv
    python scripts/context/cfbd_context.py facts --offline-dir tests/fixtures/cfbd --years 2023 --out /tmp/x  # no key

The key comes ONLY from the env var CFBD_API_KEY (a Kaggle secret / Actions secret). It is never printed, logged,
cached or written: the on-disk cache holds response bodies keyed by endpoint+params only.

Budget: the CFBD free tier is metered per call (1,000 calls/month on the free tier at the time of writing; check
https://collegefootballdata.com/key before raising it). Every call here is per SEASON, never per game:
/games, /games/media, /games/weather, /rankings, /talent for season Y and /records for Y-1 = 6 calls a season,
+1 for /venues. 2014-2025 = 73 calls; the cache makes reruns free. --max-calls (default 150) is a hard stop.

Endpoints and facts (source ids cfbd-*, kind "new" unless noted; crew D owns /lines, not used here):
  /games          neutral_site, conference_game, venue_id (categorical);
                  prev_game_excitement, prev_home_attendance: POST-game numbers, so only ever taken from the team's
                  EARLIER game whose result was in (start + 5 h <= asof) - never from this game.
  /games/media    tv_network (categorical outlet), national_tv (0/1), tv_tier (broadcast|cable|conference|stream)
  /games/weather  temp_f, wind_mph, precip, snow (outdoor games; units as CFBD returns them: °F, mph; precip in
                  the API's unit, kept raw as `precip`)  -- may need a CFBD Patreon tier; 401/403 is skipped.
  /rankings       ap_rank, coaches_rank, cfp_rank per team from the latest poll of a week BEFORE the game's week
                  (postseason games: last regular-season poll; the post-bowl final poll is never used)
  /records (Y-1)  prev_season_win_pct per team
  /talent         talent (247 roster composite, roster-based and published pre-season)
asof: kickoff - 1 h for every row. game_id = the CFBD game id (crosswalk to the play table in lane 1).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from redzone.context.tokens import PERSONAL_USE, Fact, apply_edges, fit_edges, ledger_counts, to_frame  # noqa: E402

BASE = "https://api.collegefootballdata.com"
LICENSE = f"CollegeFootballData.com API, free tier, credit CFBD (collegefootballdata.com); {PERSONAL_USE}"
TRAIN_CUTOFF = "2022-08-01"
BROADCAST = {"abc", "cbs", "nbc", "fox"}
CABLE = {"espn", "espn2", "espnu", "fs1", "fs2", "cbssn", "cbs sports network", "tnt", "trutv", "usa", "nfl network",
         "espnews", "cw"}
CONFERENCE = {"btn", "big ten network", "sec network", "secn", "acc network", "accn", "pac-12 network", "longhorn network",
              "lhn", "accnx", "sec network+"}
NATIONAL = BROADCAST | CABLE
POLLS = {"AP Top 25": "ap_rank", "Coaches Poll": "coaches_rank", "Playoff Committee Rankings": "cfp_rank"}


def _g(d: dict, *names, default=None):
    """CFBD v2 is camelCase, v1 was snake_case: accept either."""
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


class Client:
    def __init__(self, cache: Path | None = None, offline_dir: Path | None = None, max_calls: int = 150):
        self.cache, self.offline, self.max_calls, self.calls = cache, offline_dir, max_calls, 0

    def get(self, path: str, **params):
        key = (path.strip("/").replace("/", "_") + "_" + "_".join(f"{k}-{v}" for k, v in sorted(params.items()))).strip("_")
        for d in (self.offline, self.cache):
            if d and (d / f"{key}.json").exists():
                return json.loads((d / f"{key}.json").read_text())
        if self.offline:
            return None                                     # fixture missing = endpoint not covered offline
        if self.calls >= self.max_calls:
            raise RuntimeError(f"CFBD call budget hit ({self.max_calls}); rerun later, the cache keeps progress")
        tok = os.environ.get("CFBD_API_KEY")
        if not tok:
            raise SystemExit("CFBD_API_KEY is not set (Nick adds it as a Kaggle/Actions secret)")
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                js = json.loads(r.read())
        except urllib.error.HTTPError as e:   # message carries the URL only, never the header
            if e.code in (401, 403):
                print(f"cfbd {path}: HTTP {e.code} (tier may not include it), skipped")
                return None
            raise RuntimeError(f"cfbd {path} {params}: HTTP {e.code}") from None
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)
            (self.cache / f"{key}.json").write_text(json.dumps(js))
        time.sleep(0.5)
        return js


def _ts(s) -> pd.Timestamp | None:
    if not s:
        return None
    t = pd.Timestamp(s)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _iso(t: pd.Timestamp) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def tv_tier(outlet: str) -> str:
    o = outlet.strip().lower()
    return ("broadcast" if o in BROADCAST else "cable" if o in CABLE else "conference" if o in CONFERENCE
            else "stream")


def season_facts(cl: Client, year: int) -> tuple[list[Fact], pd.DataFrame]:
    games = cl.get("/games", year=year, seasonType="both") or []
    rows = []
    for g in games:
        k = _ts(_g(g, "startDate", "start_date"))
        if k is None:
            continue
        rows.append({"game_id": str(g["id"]), "season": year, "week": int(_g(g, "week", default=0)),
                     "season_type": _g(g, "seasonType", "season_type", default="regular"), "kickoff_utc": k,
                     "home": _g(g, "homeTeam", "home_team"), "away": _g(g, "awayTeam", "away_team"),
                     "completed": bool(_g(g, "completed", default=False)),
                     "neutral": _g(g, "neutralSite", "neutral_site"), "conf_game": _g(g, "conferenceGame", "conference_game"),
                     "venue_id": _g(g, "venueId", "venue_id"), "attendance": _g(g, "attendance"),
                     "excitement": _g(g, "excitementIndex", "excitement_index")})
    G = pd.DataFrame(rows)
    if G.empty:
        return [], G
    G = G.sort_values("kickoff_utc").reset_index(drop=True)
    facts: list[Fact] = []
    asof = {r.game_id: r.kickoff_utc - pd.Timedelta(hours=1) for r in G.itertuples()}
    kick = dict(zip(G["game_id"], G["kickoff_utc"]))

    def F(gid, team, field, val, src, kind="new"):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return
        facts.append(Fact(gid, team, _iso(asof[gid]), field, val, kind, src, LICENSE))

    # /games: pregame facts + strictly prior-game post-game numbers
    for r in G.itertuples():
        F(r.game_id, None, "neutral_site", None if r.neutral is None else int(bool(r.neutral)), "cfbd-games")
        F(r.game_id, None, "conference_game", None if r.conf_game is None else int(bool(r.conf_game)), "cfbd-games")
        F(r.game_id, None, "venue_id", None if r.venue_id is None else f"v{r.venue_id}", "cfbd-games")
    for team in pd.unique(G[["home", "away"]].values.ravel()):
        tg = G[(G["home"] == team) | (G["away"] == team)]
        for r in tg.itertuples():
            a = asof[r.game_id]
            done = tg[tg["completed"] & (tg["kickoff_utc"] + pd.Timedelta(hours=5) <= a)]
            if len(done):
                last = done.iloc[-1]
                F(r.game_id, team, "prev_game_excitement", last["excitement"], "cfbd-games")
                home_done = done[done["home"] == team]
                if len(home_done):
                    F(r.game_id, team, "prev_home_attendance", home_done.iloc[-1]["attendance"], "cfbd-games")

    # /games/media
    media = cl.get("/games/media", year=year, seasonType="both") or []
    by_game: dict[str, list[str]] = {}
    for m in media:
        if str(_g(m, "mediaType", "media_type", default="")).lower() in ("tv", "web") and _g(m, "outlet"):
            by_game.setdefault(str(m["id"]), []).append(str(m["outlet"]))
    for gid, outlets in by_game.items():
        if gid not in asof:
            continue
        tiers = [tv_tier(o) for o in outlets]
        best = min(range(len(outlets)), key=lambda i: ["broadcast", "cable", "conference", "stream"].index(tiers[i]))
        F(gid, None, "tv_network", outlets[best], "cfbd-media")
        F(gid, None, "tv_tier", tiers[best], "cfbd-media")
        F(gid, None, "national_tv", int(any(o.strip().lower() in NATIONAL for o in outlets)), "cfbd-media")

    # /games/weather
    for w in cl.get("/games/weather", year=year, seasonType="both") or []:
        gid = str(w["id"])
        if gid not in asof or _g(w, "gameIndoors", "game_indoors"):
            continue
        F(gid, None, "temp_f", _g(w, "temperature"), "cfbd-weather")
        F(gid, None, "wind_mph", _g(w, "windSpeed", "wind_speed"), "cfbd-weather")
        F(gid, None, "precip", _g(w, "precipitation"), "cfbd-weather")
        sf = _g(w, "snowfall")
        F(gid, None, "snow", None if sf is None else int(float(sf) > 0), "cfbd-weather")

    # /rankings: poll of an earlier week only
    polls = []
    for wk in cl.get("/rankings", year=year, seasonType="both") or []:
        st = _g(wk, "seasonType", "season_type", default="regular")
        for p in wk.get("polls", []):
            f = POLLS.get(p.get("poll"))
            if not f:
                continue
            for rk in p.get("ranks", []):
                polls.append({"st": st, "week": int(wk["week"]), "field": f, "team": _g(rk, "school", "team"),
                              "rank": rk.get("rank")})
    P = pd.DataFrame(polls)
    if len(P):
        P = P[P["st"] == "regular"]
        for r in G.itertuples():
            limit = r.week if r.season_type == "regular" else 10 ** 6   # postseason: any regular poll (all are pre-bowl)
            for f in POLLS.values():
                prior = P[(P["field"] == f) & (P["week"] < limit)]
                if prior.empty:
                    continue
                cur = prior[prior["week"] == prior["week"].max()]
                for team in (r.home, r.away):
                    hit = cur[cur["team"] == team]
                    if len(hit):
                        F(r.game_id, team, f, int(hit.iloc[0]["rank"]), "cfbd-rankings")

    # /records (previous season) and /talent
    rec = {}
    for x in cl.get("/records", year=year - 1) or []:
        t = x.get("total", {})
        gp = (t.get("games") or 0)
        if gp:
            rec[x.get("team")] = (t.get("wins", 0) + 0.5 * t.get("ties", 0)) / gp
    tal = {_g(x, "team", "school"): float(x["talent"]) for x in (cl.get("/talent", year=year) or []) if x.get("talent")}
    for r in G.itertuples():
        for team in (r.home, r.away):
            if team in rec:
                F(r.game_id, team, "prev_season_win_pct", round(rec[team], 4), "cfbd-records")
            if team in tal:
                F(r.game_id, team, "talent", round(tal[team], 2), "cfbd-talent")
    G["kickoff_utc"] = G["game_id"].map(kick)
    return facts, G


def venues_csv(cl: Client) -> pd.DataFrame:
    """CFBD /venues -> rows in redzone/context/venues.csv columns (weather.py hook for CFB)."""
    rows = []
    for v in cl.get("/venues") or []:
        lat = _g(v, "latitude", default=(v.get("location") or {}).get("x"))
        lon = _g(v, "longitude", default=(v.get("location") or {}).get("y"))
        if lat is None or lon is None:
            continue
        rows.append({"league": "cfb", "venue_id": str(v["id"]), "name": v.get("name"), "team": "",
                     "lat": float(lat), "lon": float(lon), "tz": _g(v, "timezone", default=""),
                     "roof": "dome" if v.get("dome") else "outdoors", "first_season": _g(v, "constructionYear", "year_constructed"),
                     "last_season": ""})
    return pd.DataFrame(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["facts", "venues"])
    ap.add_argument("--years", default="2014-2025")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache")
    ap.add_argument("--offline-dir", help="fixture JSON folder (tests), no network, no key")
    ap.add_argument("--max-calls", type=int, default=150)
    a = ap.parse_args(argv)
    cl = Client(Path(a.cache) if a.cache else None, Path(a.offline_dir) if a.offline_dir else None, a.max_calls)
    if a.mode == "venues":
        v = venues_csv(cl)
        v.to_csv(a.out, index=False)
        print(f"{len(v)} venues -> {a.out}; calls {cl.calls}")
        return v
    y0, _, y1 = a.years.partition("-")
    years = range(int(y0), int(y1 or y0) + 1)
    out = Path(a.out) / "cfb-context"
    out.mkdir(parents=True, exist_ok=True)
    frames, kicks = {}, []
    for y in years:
        f, G = season_facts(cl, y)
        if f:
            frames[y] = to_frame(f)
            kicks.append(G[["game_id", "kickoff_utc"]])
        print(f"cfb {y}: {len(f)} facts; calls so far {cl.calls}")
    if not frames:
        return {}
    allf = pd.concat(frames.values(), ignore_index=True)
    kick = pd.concat(kicks).drop_duplicates("game_id").set_index("game_id")["kickoff_utc"]
    edges = fit_edges(allf, allf["game_id"].map(kick).lt(pd.Timestamp(TRAIN_CUTOFF, tz="UTC")).to_numpy())
    (out / "edges.json").write_text(json.dumps({"train_cutoff_utc": TRAIN_CUTOFF, "n_buckets": 16, "edges": edges}))
    for y, df in frames.items():
        apply_edges(df, edges).to_parquet(out / f"{y}.parquet", index=False)
    c = ledger_counts(allf)
    c["cfbd_calls"] = cl.calls
    print(json.dumps(c))
    return c


if __name__ == "__main__":
    main()

"""NBA context (Data Crew B, worker B4): schedule + results, id crosswalk, rest/travel and L2M referee facts.

Schedule source: sportsdataverse hoopR NBA schedules (ESPN-derived; one parquet per season named by its END year)
    https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_schedules/nba_schedule_<Y>.parquet
`game_id` is the ESPN event id, the id the NBA play table uses (hoopR pbp in redzone/noggin/factory.py). The NBA.com
id (00224xxxxx, used by the nba-cdn play rows from shufinskiy/nba_data, 2020-21 on) is joined in as `nba_game_id`
by date + team pair (`crosswalk`). Seasons here are named by their START year (2024 = 2024-25), like every shard.

Every fact here is pregame and leak-safe: rest/travel use only the schedule of games that tipped before this one
(dates and venues, never results); the L2M referee tendency uses only L2M reports of games that ended before.
"""
from __future__ import annotations

import math
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from redzone.context.standings import NBA_TEAMS
from redzone.context.tokens import PERSONAL_USE, Fact

HOOPR_SCHEDULE_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
                      "espn_nba_schedules/nba_schedule_{y}.parquet")
L2M_URL = "https://raw.githubusercontent.com/atlhawksfanatic/L2M/master/1-tidy/L2M/L2M_stats_nba.csv"
NBA_SCHEDULE_LICENSE = f"sportsdataverse hoopR schedules (MIT; ESPN-derived data); {PERSONAL_USE}"
NBA_L2M_LICENSE = (f"atlhawksfanatic/L2M (MIT) over the NBA's public Last Two Minute reports (official.nba.com); "
                   f"credit both; {PERSONAL_USE}")
NBA_SRC_SCHEDULE = "hoopr"            # terms.json id
NBA_SRC_L2M = "nba_l2m"
NBA_GAME_S = 3 * 3600            # a result counts 3 h after tip (games run ~2h15m)
NBA_ASOF_LEAD = pd.Timedelta(minutes=30)
VENUES_CSV = Path(__file__).with_name("venues.csv")
# ESPN abbr -> NBA.com tricode (crosswalk only; facts keep the ESPN abbr the play table uses)
NBA_ESPN_TO_TRI = {"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "NJ": "NJN",
                   "SEA": "SEA"}
SCHEDULE_COLS = ["id", "start_date", "time_valid", "neutral_site", "season", "season_type", "home_abbreviation",
                 "away_abbreviation", "home_score", "away_score", "status_type_completed", "venue_address_city",
                 "notes_headline", "broadcast", "broadcast_market", "broadcast_name"]


# --------------------------------------------------------------------------------------------- schedule

def load_hoopr(paths) -> pd.DataFrame:
    """hoopR schedule parquets -> one row per NBA game between two of the 30 franchises (all-star, exhibitions and
    international friendlies dropped). Columns: the standings.py schema plus venue_city, time_valid.
    game_type: REG (counts in the standings), POST (playoffs, play-in, and the NBA Cup final, which does not count)."""
    frames = []
    for p in sorted(map(str, paths)):
        d = pd.read_parquet(p)
        frames.append(d[[c for c in SCHEDULE_COLS if c in d.columns]])
    d = pd.concat(frames, ignore_index=True).drop_duplicates("id")
    d = d[d["home_abbreviation"].isin(NBA_TEAMS) & d["away_abbreviation"].isin(NBA_TEAMS)]
    d = d[d["season_type"].isin([2, 3, 5])]
    notes = d.get("notes_headline", pd.Series("", index=d.index)).fillna("").str.lower()
    cup_final = notes.str.contains("championship") & (d["season_type"] == 2)
    kick = pd.to_datetime(d["start_date"], utc=True, format="mixed")
    done = d["status_type_completed"].fillna(False).astype(bool)
    hs = pd.to_numeric(d["home_score"], errors="coerce").where(done)
    as_ = pd.to_numeric(d["away_score"], errors="coerce").where(done)
    out = pd.DataFrame({
        "game_id": d["id"].astype("int64").astype(str), "season": d["season"].astype(int) - 1,
        "game_type": np.where((d["season_type"] == 2) & ~cup_final, "REG", "POST"),
        "kickoff_utc": kick, "final_utc": kick + pd.Timedelta(seconds=NBA_GAME_S),
        "home": d["home_abbreviation"], "away": d["away_abbreviation"],
        "home_score": hs, "away_score": as_, "neutral": d["neutral_site"].fillna(False).astype(bool),
        "venue_city": d.get("venue_address_city"), "time_valid": d.get("time_valid", True),
        "playin": (d["season_type"] == 5).to_numpy(),
        "broadcast": d.get("broadcast", pd.Series("", index=d.index)).fillna("").astype(str).str.strip(),
        "broadcast_market": d.get("broadcast_market", pd.Series("", index=d.index)).fillna("").astype(str),
        "broadcast_name": d.get("broadcast_name", pd.Series("", index=d.index)).fillna("").astype(str),
    })
    return out.sort_values(["kickoff_utc", "game_id"]).reset_index(drop=True)


def crosswalk(games: pd.DataFrame, cdn_games: pd.DataFrame) -> pd.Series:
    """NBA.com game id per ESPN game, matched on the tricode pair and the nearest first-action time (<= 6 h).
    cdn_games: gameId, t0 (first timeActual, UTC ISO), teams ("BKN|GSW"). Unmatched -> empty string."""
    c = cdn_games.copy()
    c["t0"] = pd.to_datetime(c["t0"], utc=True, format="mixed")
    c["key"] = c["teams"].astype(str)
    tri = lambda s: s.map(lambda x: NBA_ESPN_TO_TRI.get(x, x))  # noqa: E731
    pair = ["|".join(sorted(p)) for p in zip(tri(games["home"]), tri(games["away"]))]
    by = {k: g.sort_values("t0") for k, g in c.groupby("key")}
    out = []
    for k, t in zip(pair, games["kickoff_utc"]):
        g = by.get(k)
        if g is None:
            out.append("")
            continue
        dt = (g["t0"] - t).abs()
        i = dt.idxmin()
        out.append(str(int(g.at[i, "gameId"])).zfill(10) if dt[i] <= pd.Timedelta(hours=6) else "")
    return pd.Series(out, index=games.index, dtype=str)


def schedule_rows(games: pd.DataFrame, league: str = "nba") -> pd.DataFrame:
    """run_buzz.py --schedule CSV: game_id, league, home, away, start_utc, season (+ nba_game_id when present)."""
    out = pd.DataFrame({"game_id": games["game_id"], "league": league, "home": games["home"], "away": games["away"],
                        "start_utc": games["kickoff_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "season": games["season"]})
    for c in ("nba_game_id", "game_type", "home_score", "away_score"):
        if c in games:
            out[c] = games[c]
    return out


# ---------------------------------------------------------------------------------------- national TV

NBA_TV_ORDER = [("ABC", "ABC"), ("NBC", "NBC"), ("TNT", "TNT"), ("ESPN", "ESPN"), ("PRIME", "Prime Video"),
                ("PEACOCK", "Peacock"), ("NBCSN", "NBCSN"), ("NBA TV", "NBA TV"), ("TRUTV", "TNT")]


def nba_network(broadcast: str) -> str:
    """ESPN's national broadcast string ("NBC/Peacock", "ABC/ESPN", "ESPN/ESPN2", "TNT/truTV") -> the primary
    national network (broadcast TV first: ABC > NBC > TNT > ESPN > Prime Video > Peacock > NBCSN > NBA TV)."""
    up = str(broadcast or "").upper()
    toks = [t.strip() for t in up.replace(",", "/").split("/") if t.strip()]
    for key, name in NBA_TV_ORDER:
        if any(t == key or t.startswith(key + " ") or (key == "PRIME" and "PRIME" in t) or
               (key == "ESPN" and t.startswith("ESPN")) for t in toks):
            return name
    return toks[0].title() if toks else ""


NBA_CANADA_TV = ("TSN", "SPORTSNET", "SN", "SN1", "SNNOW")   # Raptors national-in-Canada feeds: not US national


def national_tv(games: pd.DataFrame) -> pd.DataFrame:
    """One row per game: date (ET), home, away, network (primary national network or ""), networks (ESPN's raw
    national string), national (bool), broadcast_known (ESPN listed any broadcaster for the game)."""
    et = ZoneInfo("America/New_York")
    raw = games["broadcast"].where(games["broadcast"] != "",
                                   games["broadcast_name"].where(games["broadcast_market"] == "national", ""))
    canada = raw.str.upper().str.split("/").map(lambda ts: bool(ts) and all(t.strip() in NBA_CANADA_TV for t in ts))
    raw = raw.where(~canada, "")
    nat = raw != ""
    return pd.DataFrame({
        "date": games["kickoff_utc"].dt.tz_convert(et).dt.strftime("%Y-%m-%d"), "home": games["home"],
        "away": games["away"], "network": raw.map(nba_network), "national": nat, "networks": raw,
        "broadcast_known": nat | (games["broadcast_name"] != ""), "game_id": games["game_id"],
        "nba_game_id": games.get("nba_game_id", ""), "start_utc": games["kickoff_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": games["season"], "game_type": games["game_type"],
    })


def tv_facts(tv: pd.DataFrame, games: pd.DataFrame) -> list[Fact]:
    """tv_network (value_str) + national_tv (0/1), kind new, game level, asof = tip - 30 min (national windows are
    set weeks ahead). Only games where ESPN listed a broadcaster."""
    kick = games.set_index("game_id")["kickoff_utc"]
    out = []
    for r in tv[tv["broadcast_known"]].itertuples(index=False):
        asof = (kick[r.game_id] - NBA_ASOF_LEAD).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append(Fact(r.game_id, None, asof, "national_tv", int(r.national), "new", NBA_SRC_SCHEDULE,
                        NBA_SCHEDULE_LICENSE))
        if r.network:
            out.append(Fact(r.game_id, None, asof, "tv_network", r.network, "new", NBA_SRC_SCHEDULE,
                            NBA_SCHEDULE_LICENSE))
    return out


# ------------------------------------------------------------------------------------ rest and travel

def load_venues(path=VENUES_CSV) -> pd.DataFrame:
    v = pd.read_csv(path)
    v = v[v["league"] == "nba"].copy()
    v["city"] = v["venue_id"].str.removeprefix("nba:")
    return v.set_index("city")


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


def _utc_offset_h(tz: str, ts: pd.Timestamp) -> float:
    return ts.tz_convert(ZoneInfo(tz)).utcoffset().total_seconds() / 3600.0


def game_cities(games: pd.DataFrame, venues: pd.DataFrame) -> pd.Series:
    """The city each game is played in: the schedule's venue city when known, else the home team's arena from the
    static venues.csv table (team + season range). Never looks at other games, so nothing from later leaks in."""
    city = games["venue_city"].where(games["venue_city"].isin(venues.index))
    v = venues.reset_index()

    def home_city(team, season):
        c = v[(v["team"] == team) & (v["first_season"] <= season + 1)
              & (v["last_season"].isna() | (v["last_season"] >= season + 1))]
        return c.sort_values("first_season")["city"].iat[-1] if len(c) else None
    miss = city.isna()
    city[miss] = [home_city(h, s) for h, s in zip(games.loc[miss, "home"], games.loc[miss, "season"])]
    return city


def rest_travel_facts(games: pd.DataFrame, venues: pd.DataFrame | None = None) -> list[Fact]:
    """Per team per game (kind derived), asof = tip - 30 min, from the team's EARLIER games only (their tip times
    and cities, never a result): rest_days (ET calendar days between games minus 1; 0 = back-to-back),
    back_to_back, games_in_last_7 (games tipped in the 7 days before), road_trip_game (n-th straight away game,
    0 at home), home_stand_game, travel_km (great circle from the previous game's city), tz_shift_h (UTC offset
    here minus there). A team's first game of a season gets only road_trip_game / home_stand_game."""
    venues = load_venues() if venues is None else venues
    g = games.assign(city=game_cities(games, venues)).sort_values(["kickoff_utc", "game_id"])
    long = pd.concat([
        pd.DataFrame({"game_id": g["game_id"], "season": g["season"], "team": g["home"], "home": True,
                      "kick": g["kickoff_utc"], "city": g["city"], "neutral": g["neutral"]}),
        pd.DataFrame({"game_id": g["game_id"], "season": g["season"], "team": g["away"], "home": False,
                      "kick": g["kickoff_utc"], "city": g["city"], "neutral": g["neutral"]}),
    ]).sort_values(["kick", "game_id"], kind="stable")
    et = ZoneInfo("America/New_York")
    facts: list[Fact] = []
    for (season, team), t in long.groupby(["season", "team"], sort=False):
        prev = None
        kicks: list[pd.Timestamp] = []
        road = stand = 0
        for r in t.itertuples(index=False):
            asof = (r.kick - NBA_ASOF_LEAD).strftime("%Y-%m-%dT%H:%M:%SZ")

            def F(fld, val, gid=r.game_id, asof=asof, team=team):
                facts.append(Fact(gid, team, asof, fld, val, "derived", NBA_SRC_SCHEDULE, NBA_SCHEDULE_LICENSE))
            away_game = not r.home and not r.neutral
            road = road + 1 if away_game else 0
            stand = stand + 1 if r.home and not r.neutral else 0
            F("road_trip_game", road)
            F("home_stand_game", stand)
            if prev is not None:
                d = (r.kick.tz_convert(et).normalize().tz_localize(None)
                     - prev.kick.tz_convert(et).normalize().tz_localize(None)).days
                F("rest_days", min(max(d - 1, 0), 10))
                F("back_to_back", int(d <= 1))
                F("games_in_last_7", sum(1 for k in kicks if r.kick - k < pd.Timedelta(days=7)))
                if r.city in venues.index and prev.city in venues.index:
                    a, b = venues.loc[prev.city], venues.loc[r.city]
                    F("travel_km", round(haversine_km(a.lat, a.lon, b.lat, b.lon)))
                    F("tz_shift_h", _utc_offset_h(b.tz, r.kick) - _utc_offset_h(a.tz, prev.kick))
            kicks.append(r.kick)
            prev = r
    return facts


# --------------------------------------------------------------------------------------- L2M referees

def load_l2m(path) -> pd.DataFrame:
    """L2M_stats_nba.csv (one row per graded call) -> one row per game: nba_game_id, date, officials (ids), calls
    graded, incorrect calls (IC + INC), and the decision mix. Only numbers and official ids are kept."""
    use = ["GAME_ID", "gid", "date", "decision", "OFFICIAL_ID_1", "OFFICIAL_ID_2", "OFFICIAL_ID_3", "OFFICIAL_ID_4",
           "OFFICIAL_1", "OFFICIAL_2", "OFFICIAL_3", "home", "away"]
    d = pd.read_csv(path, usecols=lambda c: c in use, dtype=str, low_memory=False)
    gid = d["GAME_ID"].fillna(d.get("gid")).str.zfill(10)
    d = d.assign(gid=gid, dec=d["decision"].fillna("").str.upper().str.strip())
    d = d[d["gid"].str.match(r"^00[2-5]\d{7}$", na=False)]
    agg = d.groupby("gid").agg(
        date=("date", "first"), n_graded=("dec", "size"),
        n_incorrect=("dec", lambda s: int(s.isin(["IC", "INC"]).sum())),
        n_ic=("dec", lambda s: int((s == "IC").sum())), n_inc=("dec", lambda s: int((s == "INC").sum())),
        o1=("OFFICIAL_ID_1", "first"), o2=("OFFICIAL_ID_2", "first"), o3=("OFFICIAL_ID_3", "first"),
        o4=("OFFICIAL_ID_4", "first"), crew_chief=("OFFICIAL_1", "first"),
        home_tri=("home", "first"), away_tri=("away", "first")).reset_index()
    agg["date"] = pd.to_datetime(agg["date"], errors="coerce")
    return agg.rename(columns={"gid": "nba_game_id"})


def l2m_facts(games: pd.DataFrame, l2m: pd.DataFrame) -> list[Fact]:
    """Per game with an L2M report, joined on the NBA.com id when the crosswalk has it, else on the ET game date +
    the tricode pair (the L2M rows carry both; 2015-2020 have no cdn crosswalk). kind "new": officials (crew ids, game level, asof = tip - 30 min: the NBA publishes assignments
    at 9 AM ET on game day), and the post-game call grades (`*_postgame`, asof = next day 09:00 ET, only for
    prior-game features / aux targets). kind "derived": l2m_ref_incorrect_rate_prior = the crew's mean incorrect
    calls per L2M game over games that ENDED before this tip (leak-safe), and l2m_ref_games_prior."""
    et = ZoneInfo("America/New_York")
    g = games.copy()
    if "nba_game_id" not in g:
        g["nba_game_id"] = ""
    tri = lambda s: s.map(lambda x: NBA_ESPN_TO_TRI.get(x, x))  # noqa: E731
    g["_k"] = (g["kickoff_utc"].dt.tz_convert(et).dt.strftime("%Y-%m-%d") + "|"
               + ["|".join(sorted(p)) for p in zip(tri(g["home"]), tri(g["away"]))])
    lk = l2m.assign(_k=l2m["date"].dt.strftime("%Y-%m-%d") + "|" + ["|".join(sorted(map(str, p))) for p in
                                                                    zip(l2m["home_tri"], l2m["away_tri"])])
    by_id = g[g["nba_game_id"].astype(str).str.len() == 10].merge(l2m, on="nba_game_id", how="inner")
    rest = lk[~lk["nba_game_id"].isin(by_id["nba_game_id"])].drop(columns="nba_game_id")
    by_key = g.merge(rest, on="_k", how="inner")
    m = pd.concat([by_id, by_key], ignore_index=True).drop_duplicates("game_id").sort_values("kickoff_utc")
    facts: list[Fact] = []
    hist: dict[str, list[tuple[pd.Timestamp, int]]] = {}   # official id -> [(final_utc, incorrect)]
    for r in m.itertuples(index=False):
        asof = (r.kickoff_utc - NBA_ASOF_LEAD).strftime("%Y-%m-%dT%H:%M:%SZ")
        post = ((r.kickoff_utc.tz_convert(et).normalize() + pd.Timedelta(days=1, hours=9))
                .tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"))
        ids = [str(o).split(".")[0] for o in (r.o1, r.o2, r.o3, r.o4) if isinstance(o, str) and o not in ("", "NA")]

        def F(team, fld, val, a, kind):
            facts.append(Fact(r.game_id, team, a, fld, val, kind, NBA_SRC_L2M, NBA_L2M_LICENSE))
        if ids:
            F(None, "ref_crew_chief_id", f"ref_{ids[0]}", asof, "new")
            for k, o in enumerate(ids[1:3], start=2):
                F(None, f"ref_{k}_id", f"ref_{o}", asof, "new")
            prior = [x for o in ids for (t, x) in hist.get(o, []) if t <= r.kickoff_utc - NBA_ASOF_LEAD]
            F(None, "l2m_ref_games_prior", len(prior), asof, "derived")
            if prior:
                F(None, "l2m_ref_incorrect_rate_prior", round(float(np.mean(prior)), 3), asof, "derived")
        F(None, "l2m_calls_graded_postgame", int(r.n_graded), post, "new")
        F(None, "l2m_incorrect_calls_postgame", int(r.n_incorrect), post, "new")
        F(None, "l2m_incorrect_noncalls_postgame", int(r.n_inc), post, "new")
        for o in ids:
            hist.setdefault(o, []).append((r.final_utc, int(r.n_incorrect)))
    return facts

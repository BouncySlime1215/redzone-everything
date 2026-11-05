"""Land the first crew-B context facts from GitHub-hosted public data (worker B1).

Sources (all fetched from github.com / raw.githubusercontent.com, sequential, cached):
  nflverse-data releases: schedules/games.csv, injuries/injuries_YYYY.csv, officials/officials.csv,
                          stats_team/stats_team_week_YYYY.csv (penalty counts for the prior-only crew flag rate)
  chadwickbureau/retrosheet: seasons/YYYY/GLYYYY.TXT game logs + seasons/YYYY/YYYYTTT.EV[AN] event files (info only)

Writes one parquet per league-season in lane 1's vocab v2 format (redzone/context/tokens.py), edges.json per
league (buckets fit on TRAIN games only), and manifest-b1.json. Doc: docs/datasources/context-b1-landing.md.
No betting lines are emitted (crew D owns spread/total/moneyline).

    python scripts/context/land_github_ctx.py --raw <cache dir> --out /mnt/project-files/data-b/
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from redzone.context.tokens import PERSONAL_USE, Fact, apply_edges, fit_edges, ledger_counts, to_frame  # noqa: E402

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
RETRO = "https://raw.githubusercontent.com/chadwickbureau/retrosheet/master/seasons"
LIC_NFLVERSE = f"nflverse CC-BY-4.0, credit nflverse; {PERSONAL_USE}"
LIC_RETRO = ("Retrosheet: free, must credit 'The information used here was obtained free of charge from and is "
             "copyrighted by Retrosheet. Interested parties may contact Retrosheet at www.retrosheet.org'; "
             f"{PERSONAL_USE}")
ET = ZoneInfo("America/New_York")
NFL_TRAIN_END = pd.Timestamp("2022-09-01")
MLB_TRAIN_END = pd.Timestamp("2022-03-01")
NFL_GAME_HOURS = 4          # a game's penalty total counts as known only 4h after its kickoff
MIN_CREW_GAMES = 8          # crew flag-rate z needs this many prior games
OUTDOOR_ROOFS = {"outdoors", "open"}


def iso(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(x) else x


# ----------------------------------------------------------------------------------------------- NFL
def nfl_kickoff_utc(gameday: str, gametime) -> pd.Timestamp:
    """gameday + gametime are US/Eastern; a missing gametime (1999-2000 rows) defaults to 13:00 ET."""
    t = gametime if isinstance(gametime, str) and gametime.strip() else "13:00"
    return pd.Timestamp(f"{gameday} {t}").tz_localize(ET).tz_convert("UTC")


def nfl_games_index(games: pd.DataFrame) -> pd.DataFrame:
    g = games.copy()
    g["kickoff"] = [nfl_kickoff_utc(d, t) for d, t in zip(g["gameday"], g["gametime"])]
    g["asof"] = g["kickoff"] - pd.Timedelta(seconds=1)
    g["date"] = pd.to_datetime(g["gameday"])
    return g


def nfl_schedule_facts(g: pd.DataFrame) -> list[Fact]:
    src, lic = "nflverse-schedules", LIC_NFLVERSE
    out: list[Fact] = []
    for r in g.itertuples(index=False):
        a = iso(r.asof)

        def add(field, value, kind, team=None, _gid=r.game_id, _a=a):
            if value is None or (isinstance(value, float) and np.isnan(value)):
                return
            if isinstance(value, str) and not value.strip():
                return
            out.append(Fact(_gid, team, _a, field, value, kind, src, lic))

        roof = r.roof if isinstance(r.roof, str) else None
        add("roof", roof, "new")
        add("surface", r.surface if isinstance(r.surface, str) else None, "new")
        if roof in OUTDOOR_ROOFS:                     # observed at kickoff; dome/closed values are meaningless
            add("temp_f", _num(r.temp), "new")
            add("wind_mph", _num(r.wind), "new")
        add("referee", r.referee if isinstance(r.referee, str) else None, "new")
        add("stadium", r.stadium_id if isinstance(r.stadium_id, str) else None, "new")
        add("div_game", _num(r.div_game), "derived")
        et = r.kickoff.tz_convert(ET)
        add("weekday", et.strftime("%a"), "derived")
        add("kickoff_hour_et", float(et.hour + et.minute / 60), "derived")
        add("primetime", float(et.hour >= 19), "derived")
        hr, ar = _num(r.home_rest), _num(r.away_rest)
        add("rest_days", hr, "derived", r.home_team)
        add("rest_days", ar, "derived", r.away_team)
        if hr is not None and ar is not None:
            add("rest_diff", hr - ar, "derived")       # home minus away
        add("coach", r.home_coach if isinstance(r.home_coach, str) else None, "new", r.home_team)
        add("coach", r.away_coach if isinstance(r.away_coach, str) else None, "new", r.away_team)
    return out


def nfl_injury_facts(inj: pd.DataFrame, g: pd.DataFrame) -> tuple[list[Fact], int]:
    """Per team-game counts from the final weekly report. Rows modified after kickoff-1s are dropped.

    Returns (facts, dropped_rows)."""
    src, lic = "nflverse-injuries", LIC_NFLVERSE
    side = pd.concat([g[["game_id", "season", "week", "home_team", "asof"]].rename(columns={"home_team": "team"}),
                      g[["game_id", "season", "week", "away_team", "asof"]].rename(columns={"away_team": "team"})])
    d = inj.merge(side, on=["season", "week", "team"], how="inner")
    mod = d["date_modified"] if "date_modified" in d else pd.Series(pd.NA, index=d.index)
    d["mod"] = pd.to_datetime(mod, utc=True, errors="coerce")
    # 2009 (almost all) and 2025+ rows carry no date_modified: they are the official final pregame report, so
    # their asof falls back to kickoff-1s. A real timestamp after kickoff-1s is a post-kickoff edit: dropped.
    d["mod"] = d["mod"].fillna(d["asof"])
    keep = d["mod"] <= d["asof"]
    dropped = int((~keep).sum())
    d = d[keep].sort_values("mod").drop_duplicates(["game_id", "team", "gsis_id"], keep="last")
    st = d["report_status"].fillna("").str.lower()
    d["out"] = st.eq("out")
    d["dbt"] = st.eq("doubtful")
    d["q"] = st.eq("questionable")
    d["qb_out"] = d["out"] & d["position"].eq("QB")
    agg = d.groupby(["game_id", "team"]).agg(out=("out", "sum"), dbt=("dbt", "sum"), q=("q", "sum"),
                                             qb_out=("qb_out", "max"), mod=("mod", "max"), asof=("asof", "first"))
    out = []
    for (gid, team), r in agg.iterrows():
        a = iso(min(r["mod"], r["asof"]))
        for field, v in (("players_out", r["out"]), ("players_doubtful", r["dbt"]),
                         ("players_questionable", r["q"]), ("qb_out", r["qb_out"])):
            out.append(Fact(gid, team, a, field, float(v), "new", src, lic))
    return out, dropped


def nfl_official_facts(off: pd.DataFrame, stw: pd.DataFrame, g: pd.DataFrame) -> list[Fact]:
    """Crew chief id (officials release, 2015+) and a prior-only crew flag-rate z (schedules referee, 1999+).

    The z uses only games whose kickoff + 4h <= this game's asof (no leakage, incl. same-day overlaps)."""
    lic = LIC_NFLVERSE
    out = []
    if off is not None and len(off):
        ref = off[off["position"].eq("Referee")].copy()
        ref["old_game_id"] = ref["game_id"].astype(str)
        m = g.assign(old_game_id=g["old_game_id"].astype(str)).merge(
            ref[["old_game_id", "official_id"]], on="old_game_id", how="inner")
        for r in m.itertuples(index=False):
            out.append(Fact(r.game_id, None, iso(r.asof), "crew_chief_id", f"off{int(r.official_id)}", "new",
                            "nflverse-officials", lic))
    if stw is None or not len(stw):
        return out
    pen = stw.groupby("game_id")["penalties"].sum(min_count=2).dropna()
    s = g[g["referee"].notna()].merge(pen.rename("pen"), left_on="game_id", right_index=True, how="left")
    s = s.sort_values("kickoff").reset_index(drop=True)
    done = s[s["pen"].notna()].copy()
    done["known"] = done["kickoff"] + pd.Timedelta(hours=NFL_GAME_HOURS)
    done = done.sort_values("known")
    kt = done["known"].dt.tz_convert(None).to_numpy()
    cs, cs2 = np.cumsum(done["pen"].to_numpy()), np.cumsum(done["pen"].to_numpy() ** 2)
    by_ref = {k: (v["known"].dt.tz_convert(None).to_numpy(), np.cumsum(v["pen"].to_numpy())) for k, v in done.groupby("referee")}
    for r in s.itertuples(index=False):
        t = r.asof.tz_convert(None).to_datetime64()
        n = int(np.searchsorted(kt, t, side="right"))
        rk = by_ref.get(r.referee)
        if rk is None or n < 30:
            continue
        k = int(np.searchsorted(rk[0], t, side="right"))
        a = iso(r.asof)
        out.append(Fact(r.game_id, None, a, "ref_prior_games", float(k), "derived", "nflverse-stats-team", lic))
        if k < MIN_CREW_GAMES:
            continue
        mu = cs[n - 1] / n
        sd = np.sqrt(max(cs2[n - 1] / n - mu * mu, 1e-9))
        crew = rk[1][k - 1] / k
        out.append(Fact(r.game_id, None, a, "ref_flag_rate_z", float((crew - mu) / sd), "derived",
                        "nflverse-stats-team", lic))
    return out


# ----------------------------------------------------------------------------------------------- MLB
# Retrosheet game log columns (0-based): 0 date, 1 game number, 3 visitor, 6 home, 7 home league, 12 day/night,
# 16 park id, 17 attendance, 18 duration (POST-game: never emitted), 77 HP umpire id.
TEAM_TZ = {"ANA": "America/Los_Angeles", "LAN": "America/Los_Angeles", "OAK": "America/Los_Angeles",
           "SDN": "America/Los_Angeles", "SFN": "America/Los_Angeles", "SEA": "America/Los_Angeles",
           "ATH": "America/Los_Angeles", "ARI": "America/Phoenix", "COL": "America/Denver",
           "CHA": "America/Chicago", "CHN": "America/Chicago", "HOU": "America/Chicago", "KCA": "America/Chicago",
           "MIL": "America/Chicago", "MIN": "America/Chicago", "SLN": "America/Chicago", "TEX": "America/Chicago"}
PARK_TZ = {"TOK01": "Asia/Tokyo", "SYD01": "Australia/Sydney", "LON01": "Europe/London", "SEO01": "Asia/Seoul",
           "MEX02": "America/Mexico_City", "MNT01": "America/Monterrey", "SJU01": "America/Puerto_Rico",
           "LAS01": "America/Los_Angeles", "OMA01": "America/Chicago", "WIL02": "America/New_York",
           "DYE01": "America/Chicago", "MIL06": "America/Chicago", "CHI12": "America/Chicago",
           "BIR01": "America/Chicago", "FTB01": "America/New_York", "BRI01": "America/New_York",
           "SAC01": "America/Los_Angeles", "DUN01": "America/New_York", "TAM01": "America/New_York"}


def retro_game_id(row: list[str]) -> str:
    return f"{row[6]}{row[0]}{row[1]}"


def parse_event_info(text: str) -> dict[str, dict[str, str]]:
    """Return {game_id: {info key: value}} from a Retrosheet event file (only id/info records are read)."""
    res: dict[str, dict[str, str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("id,"):
            cur = res.setdefault(line[3:].strip(), {})
        elif line.startswith("info,") and cur is not None:
            parts = line.rstrip().split(",", 2)
            if len(parts) == 3:
                cur[parts[1]] = parts[2]
    return res


def mlb_first_pitch(date: str, starttime: str | None, home: str, park: str) -> pd.Timestamp | None:
    if not starttime or starttime in ("0:00", "0:00PM", "unknown"):
        return None
    t = starttime.strip().upper()
    try:
        hm = pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:]} {t}")
    except ValueError:
        return None
    tz = ZoneInfo(PARK_TZ.get(park) or TEAM_TZ.get(home, "America/New_York"))
    return hm.tz_localize(tz).tz_convert("UTC")


def mlb_facts(gl_rows: list[list[str]], info: dict[str, dict[str, str]]) -> list[Fact]:
    lic = LIC_RETRO
    out = []
    for r in gl_rows:
        if len(r) < 78:
            continue
        gid, date, home, park = retro_game_id(r), r[0], r[6], r[16]
        day0 = pd.Timestamp(f"{date[:4]}-{date[4:6]}-{date[6:]}", tz="UTC")
        ev = info.get(gid, {})
        fp = mlb_first_pitch(date, ev.get("starttime"), home, park)
        pre = iso(fp - pd.Timedelta(seconds=1)) if fp is not None else iso(day0 + pd.Timedelta(hours=9))
        post = iso(day0 + pd.Timedelta(days=1, hours=10))

        def add(field, value, kind, asof, src="retrosheet-gamelogs", _gid=gid):
            if value is None or (isinstance(value, str) and not value.strip()):
                return
            out.append(Fact(_gid, None, asof, field, value, kind, src, lic))

        add("hp_umpire", r[77] if r[77] and r[77] != "(none)" else None, "new", pre)
        add("park", park, "new", pre)
        add("day_night", {"D": "day", "N": "night"}.get(r[12]), "new", pre)
        add("dh_game", float(r[1] in ("1", "2")), "derived", pre)
        att = _num(r[17])
        add("attendance_postgame", att if att else None, "new", post)
        if ev:
            s = "retrosheet-events"
            temp = _num(ev.get("temp"))
            add("temp_f", temp if temp else None, "new", pre, s)          # 0 = unknown in Retrosheet
            ws = _num(ev.get("windspeed"))
            add("wind_mph", ws if ws is not None and ws >= 0 else None, "new", pre, s)
            for k, f in (("winddir", "wind_dir"), ("sky", "sky"), ("precip", "precip")):
                v = ev.get(k)
                add(f, v if v and v != "unknown" else None, "new", pre, s)
    return out


# ------------------------------------------------------------------------------------------- landing
def bucketize(df: pd.DataFrame, dates: pd.Series, train_end: pd.Timestamp):
    train = df["game_id"].map(dates) < train_end
    edges = fit_edges(df, train.fillna(False))
    return apply_edges(df, edges), edges


def write_league(df: pd.DataFrame, seasons: pd.Series, league: str, out_root: Path, edges: dict) -> dict:
    d = out_root / "shards" / league
    d.mkdir(parents=True, exist_ok=True)
    counts = {}
    season_of = df["game_id"].map(seasons)
    for s, part in df.groupby(season_of):
        part = part.sort_values(["asof_utc", "game_id", "field"]).reset_index(drop=True)
        part.to_parquet(d / f"{int(s)}.parquet", index=False)
        c = ledger_counts(part)
        counts[str(int(s))] = {k: c[k] for k in ("facts", "new", "derived", "games")}
    (d / "edges.json").write_text(json.dumps(edges, indent=1, sort_keys=True))
    tot = ledger_counts(df)
    return {"seasons": counts, "total": tot}


def fetch(url: str, dest: Path) -> Path | None:
    if dest.exists() and dest.stat().st_size > 100:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl", "-sSLf", "-o", str(dest), url], capture_output=True)
    time.sleep(0.2)
    return dest if r.returncode == 0 else None


def load_nfl(raw: Path, seasons) -> dict:
    n = raw / "nfl"
    games = pd.read_csv(fetch(f"{NFLVERSE}/schedules/games.csv", n / "games.csv"), low_memory=False)
    inj = [pd.read_csv(p, low_memory=False) for y in seasons
           if (p := fetch(f"{NFLVERSE}/injuries/injuries_{y}.csv", n / f"injuries_{y}.csv"))]
    stw = [pd.read_csv(p, usecols=["game_id", "team", "penalties"], low_memory=False) for y in seasons
           if (p := fetch(f"{NFLVERSE}/stats_team/stats_team_week_{y}.csv", n / f"stw_{y}.csv"))]
    off_p = fetch(f"{NFLVERSE}/officials/officials.csv", n / "officials.csv")
    return {"games": games, "inj": pd.concat(inj) if inj else None, "stw": pd.concat(stw) if stw else None,
            "off": pd.read_csv(off_p) if off_p else None}


def land_nfl(src: dict, out_root: Path) -> dict:
    g = nfl_games_index(src["games"])
    facts = nfl_schedule_facts(g)
    dropped = 0
    if src["inj"] is not None:
        f2, dropped = nfl_injury_facts(src["inj"], g)
        facts += f2
    facts += nfl_official_facts(src["off"], src["stw"], g)
    df = to_frame(facts)
    df, edges = bucketize(df, g.set_index("game_id")["date"], NFL_TRAIN_END)
    res = write_league(df, g.set_index("game_id")["season"], "nfl", out_root, edges)
    res["injury_rows_dropped_after_kickoff"] = dropped
    return res


def load_mlb(raw: Path, years) -> tuple[list[list[str]], dict]:
    m = raw / "mlb"
    rows, info = [], {}
    for y in years:
        p = fetch(f"{RETRO}/{y}/GL{y}.TXT", m / f"GL{y}.TXT") or fetch(f"{RETRO}/{y}/gl{y}.txt", m / f"GL{y}.TXT")
        if not p:
            print(f"mlb {y}: no game log", file=sys.stderr)
            continue
        yr = list(csv.reader(io.StringIO(p.read_text(encoding="latin-1"))))
        rows += yr
        for team, lg in sorted({(r[6], r[7]) for r in yr if len(r) > 7}):
            name = f"{y}{team}.EV{'A' if lg == 'AL' else 'N'}"
            ep = fetch(f"{RETRO}/{y}/{name}", m / "ev" / name)
            if ep:
                info.update(parse_event_info(ep.read_text(encoding="latin-1")))
    return rows, info


def land_mlb(rows, info, out_root: Path) -> dict:
    df = to_frame(mlb_facts(rows, info))
    dates = pd.Series({retro_game_id(r): pd.Timestamp(r[0]) for r in rows if len(r) > 16})
    df, edges = bucketize(df, dates, MLB_TRAIN_END)
    seasons = pd.Series({k: v.year for k, v in dates.items()})
    return write_league(df, seasons, "mlb", out_root, edges)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="download cache dir")
    ap.add_argument("--out", default="/mnt/project-files/data-b/")
    ap.add_argument("--leagues", default="nfl,mlb")
    a = ap.parse_args(argv)
    raw, out = Path(a.raw), Path(a.out)
    manifest = {"worker": "B1", "generated_utc": iso(pd.Timestamp.now(tz="UTC")), "leagues": {}}
    if "nfl" in a.leagues:
        manifest["leagues"]["nfl"] = land_nfl(load_nfl(raw, range(1999, 2027)), out)
    if "mlb" in a.leagues:
        rows, info = load_mlb(raw, range(2000, 2026))
        manifest["leagues"]["mlb"] = land_mlb(rows, info, out)
    keys = ("facts", "new", "derived", "games")
    manifest["totals"] = {k: sum(v["total"][k] for v in manifest["leagues"].values()) for k in keys}
    (out / "manifest-b1.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({lg: v["total"] for lg, v in manifest["leagues"].items()} | {"totals": manifest["totals"]},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


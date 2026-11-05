"""NBA.com stats play-by-play for the 2015-16 SportVU games (sealneaward/nba-movement-data data/events/<gid>.csv).

Serves: tokenizer v2 per-play tracking context (EXP-1000): these plays carry NBA.com's 10-digit game ids, the same
ids as the SportVU tracking shard, so lane 1 can join ball x/y, speed and spacing to each play by game_id + clock.
Owner: Data Planner A.

    python scripts/pbp/nba_stats_events.py OUT_DIR --tracking OUT_DIR/nba_sportvu/2016.parquet

Game list and home/away come from the landed SportVU shard. One row per event (EVENTNUM order). SCORE is
"AWAY - HOME" in the stats API. WCTIMESTRING is a local wall clock to the minute only, so wall_ts stays null (no
invented time); clock_s is PCTIMESTRING. No description text is kept (fixed EVENTMSGTYPE vocab only).
"""
from __future__ import annotations

import io
import time
from pathlib import Path

try:
    from pbp import schema
except ImportError:
    try:
        from scripts.pbp import schema
    except ImportError:  # run as a script
        import schema  # type: ignore

URL = "https://raw.githubusercontent.com/sealneaward/nba-movement-data/master/data/events/{gid}.csv"
LICENSE = ("NBA.com stats play-by-play (NBA/STATS LLC content via a mirror with no licence); plan B personal use, "
           "private training only, never re-upload; credit NBA")
SOURCE = {"id": "nba_stats_events", "leagues": {"nba": "pro"}, "license": LICENSE, "tag": schema.PERSONAL,
          "est_events_per_game": 460}
KIND = {1: "made_shot", 2: "missed_shot", 3: "free_throw", 4: "rebound", 5: "turnover", 6: "foul", 7: "violation",
        8: "substitution", 9: "timeout", 10: "jump_ball", 11: "ejection", 12: "start_period", 13: "end_period",
        18: "instant_replay"}
UA = "redzone-personal-research/1.0"


def _clock(s) -> float | None:
    try:
        m, sec = str(s).split(":")
        return int(m) * 60 + float(sec)
    except ValueError:
        return None


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def convert_game(df, gid: str, night: str, home: str, away: str, season: int = 2016, shots=None,
                 source: str = "nba_stats_events") -> list[dict]:
    rows, hs, as_, poss = [], 0, 0, ""
    df = df.sort_values("EVENTNUM", kind="stable")
    for i, r in enumerate(df.itertuples(index=False)):
        t = _int(r.EVENTMSGTYPE) or 0
        kind = KIND.get(t, f"type_{t}")
        team = str(r.PLAYER1_TEAM_ABBREVIATION) if isinstance(r.PLAYER1_TEAM_ABBREVIATION, str) else ""
        side = "home" if team == home else "away" if team == away else ""
        sc = r.SCORE if isinstance(r.SCORE, str) and "-" in r.SCORE else None
        h0, a0 = hs, as_
        if sc:
            a, h = (int(x) for x in sc.split("-"))
            hs, as_ = h, a
        other = {"home": "away", "away": "home"}.get(side, "")
        made_last_ft = t == 3 and sc is not None
        if t in (1, 5) or made_last_ft:
            poss = other
        elif t == 4 and side:
            poss = side
        stop = {9: "timeout", 18: "review", 8: "none"}.get(t, "none")
        if t == 13:
            stop = "halftime" if _int(r.PERIOD) == 2 else "end_period"
        elif t in (6, 7):
            stop = "whistle"
        xy = (shots or {}).get(_int(r.EVENTNUM))
        rows.append(schema.row(
            source=source, sport="basketball", league="nba", level="pro", season=season, game_id=gid,
            x=xy[0] if xy else None, y=xy[1] if xy else None,
            night=night, seq=i, period=_int(r.PERIOD), clock_s=_clock(r.PCTIMESTRING), home=home, away=away,
            home_score=hs, away_score=as_, side=side, possession=poss, kind=kind,
            sub_kind=str(_int(r.EVENTMSGACTIONTYPE) or 0), actor_id=str(_int(r.PLAYER1_ID) or ""),
            actor2_id=str(_int(r.PLAYER2_ID) or ""), value=float((hs - h0) + (as_ - a0)), stop=stop,
            license=LICENSE, tag=schema.PERSONAL))
    return rows


SEASON_URL = "https://raw.githubusercontent.com/shufinskiy/nba_data/main/datasets/{kind}_{po}{y}.tar.xz"
SEASON_LICENSE = ("NBA.com stats play-by-play 1996-2025 (NBA.com ToS: private, non-commercial use) via shufinskiy/nba_data "
                  "(Apache-2.0 packaging); plan B personal use, never redistributed; credit NBA.com")


def _read_tar_csv(blob: bytes):
    import lzma
    import tarfile

    import pandas as pd
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(blob)), mode="r:") as tar:
        m = next(x for x in tar.getmembers() if x.name.endswith(".csv"))
        return pd.read_csv(tar.extractfile(m), low_memory=False)


def shot_xy(sd) -> dict:
    """shotdetail rows -> {gid10: {eventnum: (x, y)}}: LOC_X -250..250 (tenths of ft across), LOC_Y -50..890 (from
    the basket toward half court) normalised to 0..1 (x across the floor, y along it, one half court)."""
    out: dict = {}
    for r in sd[["GAME_ID", "GAME_EVENT_ID", "LOC_X", "LOC_Y"]].itertuples(index=False):
        x = min(1.0, max(0.0, (float(r.LOC_X) + 250) / 500))
        y = min(1.0, max(0.0, (float(r.LOC_Y) + 50) / 940))
        out.setdefault(f"{int(r.GAME_ID):010d}", {})[int(r.GAME_EVENT_ID)] = (round(x, 4), round(y, 4))
    return out


def pull_season(year: int, out_dir, playoffs: bool = False, session=None) -> dict | None:
    """One NBA season (`year` = start year, 1996..2025; shard season = year + 1) from shufinskiy/nba_data:
    nbastats (stats.nba.com playbyplayv2) + shotdetail (game date, home/away, shot x/y). Shard
    out_dir/nba_stats_pbp/<season>[_po].parquet. Nights on or after EVAL_FIRST_NIGHT["nba"] are never written."""
    import json

    import requests
    out = Path(out_dir) / "nba_stats_pbp"
    shard = out / f"{year + 1}{'_po' if playoffs else ''}.parquet"
    if shard.exists() and Path(str(shard) + ".json").exists():
        return json.loads(Path(str(shard) + ".json").read_text())
    s = session or requests.Session()
    s.headers.update({"User-Agent": UA})
    po = "po_" if playoffs else ""
    blobs = {}
    for kind in ("nbastats", "shotdetail"):
        r = s.get(SEASON_URL.format(kind=kind, po=po, y=year), timeout=600)
        if r.status_code != 200:
            if kind == "nbastats":
                return None
            blobs[kind] = None
            continue
        blobs[kind] = r.content
    pbp = _read_tar_csv(blobs["nbastats"])
    sd = _read_tar_csv(blobs["shotdetail"]) if blobs.get("shotdetail") else None
    meta_g = {}
    if sd is not None:
        for g in sd[["GAME_ID", "GAME_DATE", "HTM", "VTM"]].drop_duplicates("GAME_ID").itertuples(index=False):
            meta_g[f"{int(g.GAME_ID):010d}"] = (str(int(g.GAME_DATE)), str(g.HTM), str(g.VTM))
    xy = shot_xy(sd) if sd is not None else {}
    rows, dropped, undated, held = [], 0, 0, 0
    cut = schema.EVAL_FIRST_NIGHT["nba"]
    for gid_raw, g in pbp.groupby("GAME_ID", sort=True):
        gid = f"{int(gid_raw):010d}"
        if gid not in meta_g:
            undated += 1                    # no shot rows -> no date/home: never guess a night
            continue
        night, home, away = meta_g[gid]
        if night >= cut:
            held += 1
            continue
        shots = xy.get(gid)
        rs = convert_game(g, gid, night, home, away, season=year + 1, shots=shots, source="nba_stats_pbp")
        for r in rs:
            r["license"] = SEASON_LICENSE
        if schema.validate(rs):
            dropped += 1
            continue
        rows += rs
    if not rows:
        return None
    return schema.write_shard(rows, shard, {
        "source": "nba_stats_pbp", "league": "nba", "season": year + 1,
        "license": SEASON_LICENSE, "tag": schema.PERSONAL, "data_plan": "B", "dropped_games": dropped,
        "undated_games": undated, "held_out_games": held, "playoffs": playoffs,
        "credit": "Play-by-play (c) NBA.com via shufinskiy/nba_data"})


def pull_all(out_dir, tracking, session=None, rps: float = 2.0, sleep=time.sleep) -> dict:
    import pandas as pd
    import requests
    out = Path(out_dir)
    shard = out / "nba_stats_events" / "2016.parquet"
    if shard.exists() and Path(str(shard) + ".json").exists():
        import json
        return json.loads(Path(str(shard) + ".json").read_text())
    games = (pd.read_parquet(tracking, columns=["game_id", "night", "home", "away"])
             .drop_duplicates("game_id").sort_values("game_id"))
    s = session or requests.Session()
    s.headers.update({"User-Agent": UA})
    rows, dropped, missing, last = [], 0, 0, 0.0
    for g in games.itertuples(index=False):
        sleep(max(0.0, 1.0 / min(rps, 2.0) - (time.time() - last)))
        last = time.time()
        r = s.get(URL.format(gid=g.game_id), timeout=60)
        if r.status_code != 200:
            missing += 1
            continue
        rs = convert_game(pd.read_csv(io.StringIO(r.text)), g.game_id, g.night, g.home, g.away)
        if schema.validate(rs):
            dropped += 1
            continue
        rows += rs
    meta = {"source": "nba_stats_events", "league": "nba", "season": 2016, "license": LICENSE,
            "tag": schema.PERSONAL, "data_plan": "B", "dropped_games": dropped, "missing_games": missing,
            "join": "game_id matches nba_sportvu/2016.parquet; align by period + clock_s"}
    return schema.write_shard(rows, shard, meta)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--tracking", required=True)
    a = ap.parse_args()
    m = pull_all(a.out_dir, a.tracking)
    print({k: m.get(k) for k in ("games", "events", "v1_tokens", "dropped_games", "missing_games")})

"""StatsBomb American football open data (#TB12DB) -> play table (one row per event) + low-frequency tracking summary.

Serves: North Star "% of hindsight-oracle value achieved" via world-model pretraining data (NFL event streams with
~6k hand-coded events/game, plus event-anchored 22-player freeze frames). Owner: Data Planner A.

Source: github statsbomb/amf-open-data (branch main, raw.githubusercontent.com; the GitHub API and the doc/ folder
listing are not reachable from the box, so the layout below was read from README.md, the example notebooks and the
files themselves, measured 2026-11-05):
  data/games.json                                        124 Tom Brady games, NFL seasons 2016-2022 (game_id, date,
                                                         home/away team_id + nfl_team_id)
  data/plays/tb12_plays_dataset_<Y>_<Y+1>.csv            one row per play, 172 columns (2017-2022; 2016 absent)
  data/events/tb12_events_dataset_<Y>_<Y+1>.csv          one row per event, 78 columns (2017-2022; 2016 absent)
  data/lft/tb12_lft_dataset_<Y>_<Y+1>.parquet            low-frequency tracking (2021, 2022 only; per-game
                                                         <GAME_ID>.csv files named in the README 404 for the ids tried)
  data/tracking/*.json.gz + S3 season zips               high-frequency tracking: OUT OF SCOPE (too heavy)
Licence: StatsBomb Public Data User Agreement (research and genuine interest; credit StatsBomb + logo if published).

(a) EVENTS -> out_dir/statsbomb_amf/nfl/<season>.parquet, one row per StatsBomb event in event_game_index order
(Pathing, Engagement Start/End, Snap, Pass, Catch, Tackle, Play End, Timeout, Quarter End ...). The parent play is
implied by seq order (each play runs Play Start .. Play End; no play id column is added to the schema).
  kind      = the event's type-tag set (StatsBomb gives a set, e.g. {"From Snap",Catch}), each tag slugged, sorted and
              joined with "+" -> "catch+from_snap" (90 fixed tags; no free text)
  sub_kind  = event_success -> "success" | "fail" | ""
  side      = the event's team; possession = the play's offense (non-play events carry the last offense forward)
  actor_id  = player_id; actor2_id = receiver_player_id, else opponent_player_id
  x, y      = event_x/event_y (fallback event_ball_x/y). StatsBomb already normalises every event of a play to the
              OFFENSE attacking left to right (defensive events too): x yards -10..110 (-10 = back of the offense's own
              end zone, 0 own goal line, 100 opponent goal line, 110 back of its end zone), y 0..53.33 yards across.
              Stored as x = (x + 10) / 120, y = y / 53.33, clipped to 0..1.
  value     = points on the event (TD 6, FG 3, PAT 1, 2-pt 2). A negative event_points (defensive TD: pick-six, fumble
              return) is signed from the offense's view, but the event's team is already the scoring defense:
              value = |points| and the event team's score moves (checked against plays.csv scores and real finals).
  clock_s   = game_clock / 1000 (ms left in the quarter; null where StatsBomb has none, e.g. Quarter Start);
  period    = quarter (5 = overtime). wall_ts null: event_video_timestamp is seconds into the (edited) per-quarter
              video, not a wall clock, and not real elapsed time either, so elapsed_s is null too.
  stop      = Timeout -> timeout; Two Minute Warning -> two_minute_warning; Quarter End -> halftime (Q2) /
              end_period; Review/Challenge -> review; Injury Stoppage -> injury; Play End -> score_break if the play
              scored, else whistle; every other event "none".
(b) LFT -> out_dir/statsbomb_amf_lft/nfl/<season>.parquet. The lft file is NOT a fixed-rate feed: it is one 22-player
(+ ball when known) freeze frame per event (~35 frames per play over ~6 s of video). Per play the frames are bucketed
into 1 s of event_video_timestamp (within one play the video runs in real time) and each bucket becomes one row:
kind "track_lft", sub_kind phase "pre_snap" | "live" | "post_play" (vs the play's Snap/kick and Play End events),
x, y = ball (or the ball carrier when the ball row is missing), actor_id = ball carrier, value = mean offensive
player speed (yd/s, net displacement from the previous bucket's frame), side/possession = offense, scores = score
after the frame's event, clock_s = game clock of the frame's event, stop "whistle" on the play's last row.
Sidecar "rows_are" marks these as tracking summaries so the ledger never counts them as pbp events.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
from pathlib import Path

try:
    from pbp import schema  # scripts/ on sys.path (tests, the Kaggle notebook)
except ImportError:
    try:
        from scripts.pbp import schema
    except ImportError:  # run as a script: scripts/pbp is on sys.path
        import schema  # type: ignore

BASE = "https://raw.githubusercontent.com/statsbomb/amf-open-data/main/data/"
LICENSE = ("StatsBomb Public Data User Agreement (amf-open-data): research and genuine interest; credit StatsBomb + "
           "logo if published; plan B personal use")
CREDIT = "Data: StatsBomb American Football Open Data (https://github.com/statsbomb/amf-open-data)"
TERMS_ID = "statsbomb_amf"
SEASONS = tuple(range(2016, 2023))      # games.json spans 2016-2022; 2016 has tracking only (no plays/events)
LEAGUE, SHARD_DIR, LFT_DIR = "nfl", "statsbomb_amf", "statsbomb_amf_lft"
# Measured 2026-11-05 on 2021: 19 games, 117,620 events (~6.2k/game), 91k lft frames -> ~7 one-second rows/play.
SOURCE = {"id": "statsbomb_amf", "leagues": "nfl", "license": LICENSE, "tag": schema.PERSONAL, "data_plan": "B",
          "terms_id": TERMS_ID, "est_events_per_game": 6200, "games": "106 with events (2017-2022 Tom Brady games)"}
EVENT_COLS = ("game_id", "gsis_old_game_id", "game_quarter", "event_game_index", "play_uuid", "game_clock", "team_id",
              "event_types", "player_id", "receiver_player_id", "opponent_player_id", "event_x", "event_y",
              "event_ball_x", "event_ball_y", "event_success", "event_points", "event_uuid", "event_video_timestamp")
PLAY_COLS = ("game_id", "gsis_old_game_id", "season", "play_uuid", "play_game_index", "play_offense_team_id",
             "play_defense_team_id", "play_offense_team_short_name", "play_defense_team_short_name",
             "play_offense_is_home_team")
LFT_COLS = ("event_uuid", "team_id", "track_player_index", "track_ball_location", "player_id", "track_x", "track_y")
FIELD_X, FIELD_Y = 120.0, 160.0 / 3


def slug(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def tags(s) -> list[str]:
    """'{"From Snap",Catch}' -> ['catch', 'from_snap'] (sorted slugs)."""
    s = str(s or "").strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1]
    if not s:
        return []
    return sorted({slug(t) for t in next(csv.reader([s])) if slug(t)})


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _id(v) -> str:
    f = _num(v)
    return "" if f is None else str(int(f))


def _bool(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return True if s == "true" else False if s == "false" else None


def _norm(x, y):
    x, y = _num(x), _num(y)
    if x is None or y is None:
        return None, None
    return min(max((x + 10.0) / FIELD_X, 0.0), 1.0), min(max(y / FIELD_Y, 0.0), 1.0)


def season_of(label) -> int:
    """'2021/2022' -> 2021 (NFL season year)."""
    return int(re.findall(r"\d{4}", str(label))[0])


def _stop(tg: list[str], period, play_scored: bool) -> str:
    t = set(tg)
    if "timeout" in t:
        return "timeout"
    if "two_minute_warning" in t:
        return "two_minute_warning"
    if "quarter_end" in t:
        return "halftime" if period == 2 else "end_period"
    if t & {"review", "challenge"}:
        return "review"
    if "injury_stoppage" in t:
        return "injury"
    if "play_end" in t:
        return "score_break" if play_scored else "whistle"
    return "none"


def game_info(games: list[dict], plays) -> dict:
    """game_id(str) -> {home_id, away_id, home, away, night}; games.json first, plays.csv as fallback."""
    info = {}
    for g in games or []:
        info[str(g["game_id"])] = {"home_id": str(g["home_team"]["team_id"]), "away_id": str(g["away_team"]["team_id"]),
                                   "home": g["home_team"].get("nfl_team_id") or slug(g["home_team"]["name"]),
                                   "away": g["away_team"].get("nfl_team_id") or slug(g["away_team"]["name"]),
                                   "night": str(g["game_date"]).replace("-", "")[:8]}
    for r in plays.drop_duplicates("game_id").itertuples(index=False):
        gid = str(r.game_id)
        if gid in info:
            continue
        home = _bool(r.play_offense_is_home_team)
        o = (_id(r.play_offense_team_id), str(r.play_offense_team_short_name))
        d = (_id(r.play_defense_team_id), str(r.play_defense_team_short_name))
        (hid, hab), (aid, aab) = (o, d) if home else (d, o)
        info[gid] = {"home_id": hid, "away_id": aid, "home": hab, "away": aab,
                     "night": str(int(r.gsis_old_game_id))[:8]}      # gsis_old_game_id = YYYYMMDDNN (ET date)
    return info


def convert_game(gid: str, info: dict, season: int, events, offense: dict) -> list[dict]:
    """One game's events DataFrame -> play-table rows. offense: play_uuid -> offense team_id (str)."""
    side_of = {info["home_id"]: "home", info["away_id"]: "away"}
    base = dict(source="statsbomb_amf", sport="football", league=LEAGUE, level="pro", season=season, game_id=gid,
                night=info["night"], home=info["home"], away=info["away"], license=LICENSE, tag=schema.PERSONAL)
    evs = events.sort_values("event_game_index", kind="stable")
    scored = set(evs.loc[evs["event_points"].fillna(0) != 0, "play_uuid"].dropna())
    score = {"home": 0, "away": 0}
    poss, rows = "", []
    for ev in evs.itertuples(index=False):
        tg = tags(ev.event_types)
        side = side_of.get(_id(ev.team_id), "")
        play = ev.play_uuid if isinstance(ev.play_uuid, str) else ""
        if play and play in offense:
            poss = side_of.get(offense[play], poss)
        pts = int(_num(ev.event_points) or 0)
        if pts and side:
            score[side] += abs(pts)
        x, y = _norm(ev.event_x, ev.event_y)
        if x is None:
            x, y = _norm(ev.event_ball_x, ev.event_ball_y)
        ok = _bool(ev.event_success)
        clock = _num(ev.game_clock)
        period = int(ev.game_quarter)
        rows.append(schema.row(**base, seq=len(rows), period=period, clock_s=None if clock is None else clock / 1000.0,
                               elapsed_s=None, wall_ts=None, side=side, possession=poss, kind="+".join(tg) or "unknown",
                               sub_kind="success" if ok else "fail" if ok is False else "",
                               actor_id=_id(ev.player_id),
                               actor2_id=_id(ev.receiver_player_id) or _id(ev.opponent_player_id),
                               x=x, y=y, value=float(abs(pts)), home_score=score["home"], away_score=score["away"],
                               stop=_stop(tg, period, play in scored)))
    return rows


def convert_season(plays, events, games: list[dict]) -> tuple[dict, int]:
    """(game_id -> rows, season). plays/events are the season's DataFrames."""
    season = season_of(plays["season"].dropna().iloc[0])
    info = game_info(games, plays)
    offense = {u: _id(t) for u, t in zip(plays["play_uuid"], plays["play_offense_team_id"])}
    out = {}
    for gid, evs in events.groupby("game_id", sort=True):
        gid = str(gid)
        if gid in info:
            out[gid] = convert_game(gid, info[gid], season, evs, offense)
    return out, season


def _speed(prev: dict, cur: dict, dt: float):
    if prev is None or dt < 0.2:
        return None
    v = [math.hypot(cur[p][0] - prev[p][0], cur[p][1] - prev[p][1]) / dt for p in cur if p in prev]
    return round(sum(v) / len(v), 2) if v else None


def convert_lft_game(gid: str, info: dict, season: int, events, lft, offense: dict, event_rows: list[dict]) -> list[dict]:
    """One game's lft frames -> 1 Hz per-play tracking summary rows (kind track_lft)."""
    side_of = {info["home_id"]: "home", info["away_id"]: "away"}
    base = dict(source="statsbomb_amf", sport="football", league=LEAGUE, level="pro", season=season, game_id=gid,
                night=info["night"], home=info["home"], away=info["away"], license=LICENSE, tag=schema.PERSONAL)
    evs = events.sort_values("event_game_index", kind="stable").reset_index(drop=True)
    score_after = {u: (r["home_score"], r["away_score"]) for u, r in zip(evs["event_uuid"], event_rows)}
    meta, snap, end = {}, {}, {}
    for ev in evs.itertuples(index=False):
        play = ev.play_uuid if isinstance(ev.play_uuid, str) else ""
        ts = _num(ev.event_video_timestamp)
        meta[ev.event_uuid] = (play, ts, int(ev.game_quarter), _num(ev.game_clock), int(ev.event_game_index))
        if not play or ts is None:
            continue
        tg = set(tags(ev.event_types))
        if tg & {"snap", "kick_off", "free_kick"} and play not in snap:
            snap[play] = ts
        if "play_end" in tg and play not in end:
            end[play] = ts
    frames = {}
    for fr in lft.itertuples(index=False):
        frames.setdefault(fr.event_uuid, []).append(fr)
    plays = {}
    for u, ents in frames.items():
        m = meta.get(u)
        if m is None or not m[0] or m[1] is None:
            continue
        plays.setdefault(m[0], []).append((m[4], m[1], u, ents))
    rows = []
    for play, fl in sorted(plays.items(), key=lambda kv: min(f[0] for f in kv[1])):
        fl.sort()
        off = offense.get(play, "")
        oside = side_of.get(off, "")
        t0, buckets = fl[0][1], {}
        for f in fl:
            buckets.setdefault(int(math.floor(f[1] - t0)), f)       # first frame of each video second
        prev, prev_ts, out = None, None, []
        for _, (_, ts, u, ents) in sorted(buckets.items()):
            _, _, period, clock, _ = meta[u]
            ball = next((e for e in ents if int(e.track_player_index) == -1), None)
            carrier = next((e for e in ents if int(e.track_player_index) >= 0 and _bool(e.track_ball_location)), None)
            pos = ball or carrier
            x, y = (None, None) if pos is None else _norm(pos.track_x, pos.track_y)
            cur = {_id(e.player_id): (float(e.track_x), float(e.track_y)) for e in ents
                   if _id(e.team_id) == off and _id(e.player_id) and _num(e.track_x) is not None
                   and _num(e.track_y) is not None}
            speed = _speed(prev, cur, ts - prev_ts) if prev_ts is not None else None
            prev, prev_ts = cur, ts
            phase = ("post_play" if play in end and ts >= end[play] else
                     "pre_snap" if play in snap and ts < snap[play] else "live")
            hs, as_ = score_after.get(u, (None, None))
            out.append(dict(period=period, clock_s=None if clock is None else clock / 1000.0, side=oside,
                            possession=oside, kind="track_lft", sub_kind=phase,
                            actor_id="" if carrier is None else _id(carrier.player_id), x=x, y=y, value=speed,
                            home_score=hs, away_score=as_, stop="none"))
        out[-1]["stop"] = "whistle"
        for r in out:
            rows.append(schema.row(**base, seq=len(rows), elapsed_s=None, wall_ts=None, actor2_id="", **r))
    return rows


class _Fetcher:
    def __init__(self, raw_dir: Path, rps: float, session=None):
        if session is None:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = "redzone-personal-research/1.0"
        self.s, self.raw, self.gap, self.last = session, raw_dir, 1.0 / rps if rps else 0.0, 0.0

    def get(self, rel: str):
        """Cached bytes, or None on 404."""
        p = self.raw / rel
        if p.exists():
            return p.read_bytes()
        wait = self.last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        r = self.s.get(BASE + rel, timeout=300)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
        return r.content


def _csv(data: bytes, cols):
    import pandas as pd
    head = pd.read_csv(io.BytesIO(data), nrows=0).columns
    return pd.read_csv(io.BytesIO(data), usecols=[c for c in cols if c in head], low_memory=False)


def _paths(season: int) -> dict:
    y = f"{season}_{season + 1}"
    return {"plays": f"plays/tb12_plays_dataset_{y}.csv", "events": f"events/tb12_events_dataset_{y}.csv",
            "lft": f"lft/tb12_lft_dataset_{y}.parquet"}


def pull_all(out_dir, rps: float = 4, session=None, keep_raw: bool = False, seasons=SEASONS) -> list[dict]:
    """games.json + per-season plays/events/lft -> one events shard (+ one lft shard when lft exists) per season.
    Raw cached under out_dir/statsbomb_amf/_raw and deleted after the shards unless keep_raw."""
    import pandas as pd
    out = Path(out_dir)
    root = out / SHARD_DIR
    f = _Fetcher(root / "_raw", rps, session)
    games = json.loads(f.get("games.json") or b"[]")
    metas = []
    for season in seasons:
        shard, lshard = root / LEAGUE / f"{season}.parquet", out / LFT_DIR / LEAGUE / f"{season}.parquet"
        side = Path(str(shard) + ".json")
        if side.exists():
            m = json.loads(side.read_text())
            lside = Path(str(lshard) + ".json")
            if m.get("lft") == "absent" or lside.exists():
                metas.append(m)
                if lside.exists():
                    metas.append(json.loads(lside.read_text()))
                continue
        rel = _paths(season)
        pdata = f.get(rel["plays"])
        edata = f.get(rel["events"]) if pdata is not None else None
        if pdata is None or edata is None:
            continue                     # 2016: tracking only, no plays/events in the repo
        plays, events = _csv(pdata, PLAY_COLS), _csv(edata, EVENT_COLS)
        del pdata, edata
        games_rows, season_label = convert_season(plays, events, games)
        rows, dropped, kept = [], 0, []
        for gid in sorted(games_rows, key=lambda g: (games_rows[g][0]["night"] if games_rows[g] else "", g)):
            g = games_rows[gid]
            if schema.validate(g):
                dropped += 1
                continue
            kept.append(gid)
            rows += g
        n_games = events["game_id"].nunique()
        dropped += n_games - len(games_rows)
        ldata = f.get(rel["lft"])
        lft_state = "absent" if ldata is None else "present"
        if not rows:
            continue
        common = {"source": "statsbomb_amf", "league": LEAGUE, "season": season_label, "license": LICENSE,
                  "tag": schema.PERSONAL, "data_plan": "B", "credit": CREDIT, "terms_id": TERMS_ID}
        meta = schema.write_shard(rows, shard, {**common, "dropped_games": dropped, "lft": lft_state,
                                                "rows_are": "pbp events (StatsBomb event stream)"})
        metas.append(meta)
        if ldata is not None:
            lft = pd.read_parquet(io.BytesIO(ldata), columns=list(LFT_COLS))
            del ldata
            offense = {u: _id(t) for u, t in zip(plays["play_uuid"], plays["play_offense_team_id"])}
            info = game_info(games, plays)
            lgame = events.drop_duplicates("event_uuid")[["event_uuid", "game_id"]]
            lft = lft.merge(lgame, on="event_uuid", how="inner")
            lrows, ldropped = [], 0
            for gid in kept:
                gl = lft[lft["game_id"].astype(str) == gid]
                if not len(gl):
                    continue
                ge = events[events["game_id"].astype(str) == gid]
                lr = convert_lft_game(gid, info[gid], season_label, ge, gl, offense, games_rows[gid])
                if not lr or schema.validate(lr):
                    ldropped += 1
                    continue
                lrows += lr
            if lrows:
                metas.append(schema.write_shard(lrows, lshard, {
                    **common, "dropped_games": ldropped,
                    "rows_are": "low-frequency tracking summaries (not pbp events)",
                    "note": "1 Hz per-play summaries of StatsBomb lft event-anchored freeze frames"}))
        if not keep_raw:
            for r in rel.values():
                (f.raw / r).unlink(missing_ok=True)
    if not keep_raw:
        (f.raw / "games.json").unlink(missing_ok=True)
    return metas


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--rps", type=float, default=4)
    ap.add_argument("--keep-raw", action="store_true")
    a = ap.parse_args()
    for m in pull_all(a.out_dir, a.rps, keep_raw=a.keep_raw):
        print(json.dumps({k: m.get(k) for k in ("source", "season", "events", "games", "dropped_games", "rows_are")}))

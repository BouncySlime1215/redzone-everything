"""NBA SportVU 2015-16 tracking dump -> per-second tracking summary in the play table (+ a small side table).

Serves: North Star "% of hindsight-oracle value achieved" via world-model pretraining data (real live/dead-ball
timing at 1 Hz with real wall clock, ~3k rows/game, for ~630 NBA games Oct 2015 - Jan 2016). Owner: Data Planner A.

Source: the leaked/public SportVU game logs mirrored on GitHub (linouk23/NBA-Player-Movements,
data/2016.NBA.Raw.SportVU.Game.Logs/MM.DD.YYYY.VIS.at.HOME.7z, one {gameid}.json per archive; plain git blobs,
not LFS, measured 2026-11-05: ~6 MB .7z -> ~100 MB JSON). The github API is not reachable, so file names are
built from the full 2015-16 schedule in sealneaward/nba-movement-data/data/shots/shots.csv (GAME_ID, GAME_DATE,
HTM, VTM for all 1230 games) and every candidate up to CUTOFF is probed; a 404 means the game is not in the dump.

Scout rule: we store DERIVED NUMBERS, never raw frames. Moments (25 Hz, duplicated across overlapping events) are
deduped by (period, unix_ms), bucketed by wall second (unix_ms // 1000), and each bucket becomes one row:
  kind "track_live" if the game clock ran >= half the bucket's span, else "track_dead"; sub_kind = shot-clock
  bucket at the bucket's middle moment; x, y = ball (x/94, y/50); value = ball ground speed ft/s (net xy
  displacement first -> last moment of the second, so dribble bounce cancels); actor_id = nearest player to the
  ball within 4 ft at the middle moment, side/possession = that player's team; stop "none" on live rows,
  "whistle" on the first dead row after a live row, "" on later dead rows (the stoppage continues).
home_score/away_score are null (not in tracking): lane 1 joins pbp by game_id + period + clock_s.
Side table out_dir/sportvu_extra/nba/2016.parquet keyed by (game_id, seq): mean player speed and spacing (mean
pairwise distance of the five) per team, ball height z, raw shot clock, and wall_ms (int64 unix ms: the play
table stores wall_ts as float32, which rounds unix seconds to ~128 s, so join exact wall time from here). Its sidecar counts 0 events/tokens so the
ledger never double counts. Raw .7z and JSON are deleted after each game; per-game parts live in a raw/ folder
(removed by the Kaggle crew after the shard is built) so the pull is resumable.
"""
from __future__ import annotations

import csv
import io
import json
import math
import shutil
import time
from pathlib import Path

try:
    from pbp import schema  # scripts/ on sys.path (tests, the Kaggle notebook)
except ImportError:
    try:
        from scripts.pbp import schema
    except ImportError:  # run as a script: scripts/pbp is on sys.path
        import schema  # type: ignore

RAW = "https://raw.githubusercontent.com/"
MEDIA = "https://media.githubusercontent.com/media/"
REPO, BRANCH, DIR = "linouk23/NBA-Player-Movements", "master", "data/2016.NBA.Raw.SportVU.Game.Logs/"
SCHEDULE = "sealneaward/nba-movement-data/master/data/shots/shots.csv"
CUTOFF = "20160131"          # the dump ends 2016-01-23; probe a week past it
LICENSE = ("SportVU 2015-16 (NBA/STATS LLC content, mirror has no licence); plan B personal use, private training "
           "only, never re-upload; credit NBA / STATS LLC")
CREDIT = "Tracking data: NBA / STATS LLC SportVU (2015-16 public game logs)"
SOURCE = {"id": "sportvu", "leagues": "nba", "license": LICENSE, "tag": schema.PERSONAL, "data_plan": "B",
          "est_rows_per_game": 3200, "games": "~631 (Oct 2015 - Jan 2016)"}
SEASON, LEAGUE, SHARD_DIR, EXTRA_DIR = 2016, "nba", "nba_sportvu", "sportvu_extra"
HANDLER_FT = 4.0
EXTRA_COLS = ("wall_ms", "home_speed", "away_speed", "home_spacing", "away_spacing", "ball_z", "shot_clock_s")


def sc_bucket(sc) -> str:
    if sc is None or (isinstance(sc, float) and math.isnan(sc)):
        return "off"
    return "sc0-6" if sc < 6 else "sc6-12" if sc < 12 else "sc12-18" if sc < 18 else "sc18-24"


def dedupe(events: list[dict]) -> list[tuple]:
    """Unique moments sorted by wall time: (period, unix_ms, game_clock, shot_clock, entities)."""
    seen = {}
    for ev in events:
        for m in ev.get("moments") or []:
            key = (m[0], m[1])
            if key not in seen:
                seen[key] = (m[0], m[1], m[2], m[3], m[5])
    return sorted(seen.values(), key=lambda m: (m[1], m[0]))


def _handler(ents):
    """(teamid, playerid, dist) of the player nearest the ball, or None if no ball/players."""
    if not ents or ents[0][0] != -1:
        return None
    bx, by = ents[0][2], ents[0][3]
    best = None
    for e in ents[1:]:
        d = math.hypot(e[2] - bx, e[3] - by)
        if best is None or d < best[2]:
            best = (e[0], e[1], d)
    return best


def _spacing(ents, team) -> float | None:
    pts = [(e[2], e[3]) for e in ents[1:] if e[0] == team]
    if len(pts) < 2:
        return None
    ds = [math.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(pts) for b in pts[i + 1:]]
    return sum(ds) / len(ds)


def _speeds(first, last, dt, team) -> float | None:
    if dt <= 0:
        return None
    a = {e[1]: (e[2], e[3]) for e in first if e[0] == team}
    v = [math.hypot(e[2] - a[e[1]][0], e[3] - a[e[1]][1]) / dt for e in last if e[0] == team and e[1] in a]
    return sum(v) / len(v) if v else None


def convert_game(game: dict) -> tuple[list[dict], list[dict]]:
    """One SportVU game JSON -> (play-table rows, side-table rows), one row per wall second with moments."""
    evs = game.get("events") or []
    ev0 = next((e for e in evs if e.get("home") and e.get("visitor")), None)
    if ev0 is None:
        return [], []
    hid, vid = ev0["home"]["teamid"], ev0["visitor"]["teamid"]
    side_of = {hid: "home", vid: "away"}
    base = dict(source="sportvu", sport="basketball", league=LEAGUE, level="pro", season=SEASON,
                game_id=str(game["gameid"]), night=str(game["gamedate"]).replace("-", "")[:8],
                home=ev0["home"]["abbreviation"], away=ev0["visitor"]["abbreviation"], license=LICENSE,
                tag=schema.PERSONAL)
    moments = dedupe(evs)
    buckets, cur, key = [], [], None
    for m in moments:
        k = (m[0], m[1] // 1000)
        if k != key and cur:
            buckets.append(cur)
            cur = []
        key = k
        cur.append(m)
    if cur:
        buckets.append(cur)
    rows, extra, prev_live = [], [], False
    for b in buckets:
        first, last, mid = b[0], b[-1], b[len(b) // 2]
        dt = (last[1] - first[1]) / 1000.0
        g0, g1 = first[2], last[2]
        live = g0 is not None and g1 is not None and dt >= 0.2 and (g0 - g1) >= 0.5 * dt
        if live:
            stop = "none"
        else:
            stop = "whistle" if prev_live and rows and rows[-1]["period"] == first[0] else ""
        prev_live = live
        ents = mid[4] or []
        ball = ents[0] if ents and ents[0][0] == -1 else None
        h = _handler(ents)
        side, actor = ("", "") if h is None or h[2] > HANDLER_FT else (side_of.get(h[0], ""), h[1])
        speed = None
        fb, lb = (first[4] or [None])[0], (last[4] or [None])[0]
        if dt > 0 and fb and lb and fb[0] == -1 and lb[0] == -1:
            speed = round(math.hypot(lb[2] - fb[2], lb[3] - fb[3]) / dt, 1)
        seq = len(rows)
        rows.append(schema.row(**base, seq=seq, period=first[0], clock_s=mid[2], wall_ts=first[1] / 1000.0,
                               side=side, possession=side, kind="track_live" if live else "track_dead",
                               sub_kind=sc_bucket(mid[3]), actor_id=actor, actor2_id="",
                               x=None if ball is None else min(max(ball[2] / 94.0, 0.0), 1.0),
                               y=None if ball is None else min(max(ball[3] / 50.0, 0.0), 1.0),
                               value=speed, stop=stop))
        extra.append({"game_id": base["game_id"], "seq": seq, "wall_ms": first[1],
                      "home_speed": _speeds(first[4] or [], last[4] or [], dt, hid),
                      "away_speed": _speeds(first[4] or [], last[4] or [], dt, vid),
                      "home_spacing": _spacing(ents, hid), "away_spacing": _spacing(ents, vid),
                      "ball_z": None if ball is None else ball[4], "shot_clock_s": mid[3]})
    return rows, extra


def extra_frame(extra: list[dict]):
    import pandas as pd
    df = pd.DataFrame(extra, columns=["game_id", "seq", *EXTRA_COLS])
    df["seq"] = df["seq"].astype("int32")
    df["wall_ms"] = df["wall_ms"].astype("int64")
    for c in EXTRA_COLS[1:]:
        df[c] = pd.to_numeric(df[c]).astype("float32")
    return df


def schedule(csv_bytes: bytes, cutoff: str = CUTOFF) -> list[dict]:
    """shots.csv -> sorted unique games [{game_id, date, home, away, file}] on or before cutoff."""
    games = {}
    for r in csv.DictReader(io.StringIO(csv_bytes.decode("utf-8", "replace"))):
        gid, d = r.get("GAME_ID", ""), r.get("GAME_DATE", "")
        if not gid or gid in games or not d or d > cutoff:
            continue
        games[gid] = {"game_id": gid.zfill(10), "date": d, "home": r["HTM"], "away": r["VTM"],
                      "file": f"{d[4:6]}.{d[6:8]}.{d[:4]}.{r['VTM']}.at.{r['HTM']}.7z"}
    return sorted(games.values(), key=lambda g: (g["date"], g["game_id"]))


class _Fetcher:
    def __init__(self, rps: float, session=None):
        if session is None:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = "redzone-personal-research/1.0"
        self.s, self.gap, self.last = session, 1.0 / rps if rps else 0.0, 0.0

    def get(self, url: str):
        """bytes, or None on 404."""
        wait = self.last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        r = self.s.get(url, timeout=300)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content


def _blob(f: _Fetcher, path: str):
    """A repo file; follows a git-LFS pointer to media.githubusercontent.com if the mirror ever switches to LFS."""
    data = f.get(f"{RAW}{REPO}/{BRANCH}/{path}")
    if data is not None and len(data) < 1024 and data.startswith(b"version https://git-lfs"):
        data = f.get(f"{MEDIA}{REPO}/{BRANCH}/{path}")
    return data


def _load_7z(data: bytes, work: Path) -> dict:
    import py7zr
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        with py7zr.SevenZipFile(io.BytesIO(data)) as z:
            z.extractall(work)
        js = sorted(work.rglob("*.json"))
        if not js:
            raise ValueError("no json in archive")
        with open(js[0], "rb") as fh:
            return json.load(fh)
    finally:
        shutil.rmtree(work, ignore_errors=True)       # never keep raw frames


def _concat(parts: list[Path], dst: Path) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp")
    writer, n = None, 0
    try:
        for p in parts:
            t = pq.read_table(p)
            if writer is None:
                writer = pq.ParquetWriter(tmp, t.schema, compression="zstd")
            writer.write_table(t.cast(writer.schema) if t.schema != writer.schema else t)
            n += t.num_rows
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        writer = pq.ParquetWriter(tmp, pa.schema([]))
        writer.close()
    tmp.replace(dst)
    return n


def pull_all(out_dir, rps: float = 2, max_games=None, session=None) -> list[dict]:
    """schedule -> probe/download each .7z -> convert -> per-game parts -> one shard + side table.
    Resumable via raw/status.json; returns [shard meta, extra meta] (empty list if nothing converted)."""
    import py7zr  # noqa: F401 - fail fast (one job error) instead of dropping every game; pip install py7zr
    out = Path(out_dir)
    shard, xshard = out / SHARD_DIR / f"{SEASON}.parquet", out / EXTRA_DIR / LEAGUE / f"{SEASON}.parquet"
    side = Path(str(shard) + ".json")
    if side.exists() and Path(str(xshard) + ".json").exists():
        meta = json.loads(side.read_text())
        if meta.get("complete"):
            return [meta, json.loads(Path(str(xshard) + ".json").read_text())]
    raw = out / SHARD_DIR / "raw"
    parts = raw / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    f = _Fetcher(rps, session)
    gpath, spath = raw / "games.json", raw / "status.json"
    if gpath.exists():
        games = json.loads(gpath.read_text())
    else:
        data = f.get(RAW + SCHEDULE)
        if data is None:
            raise RuntimeError("schedule csv not found")
        games = schedule(data)
        gpath.write_text(json.dumps(games))
    status = json.loads(spath.read_text()) if spath.exists() else {}
    done_now = 0
    for g in games:
        if g["file"] in status:
            continue
        if max_games is not None and done_now >= max_games:
            break
        t0 = time.time()
        data = _blob(f, DIR + g["file"])
        if data is None:
            status[g["file"]] = "missing"
        else:
            try:
                rows, extra = convert_game(_load_7z(data, raw / "work"))
                del data
                errs = schema.validate(rows)
                if errs:
                    status[g["file"]] = "dropped: " + errs[0][:120]
                else:
                    gid = rows[0]["game_id"]
                    schema.to_frame(rows).to_parquet(parts / f"{gid}.parquet", compression="zstd", index=False)
                    extra_frame(extra).to_parquet(parts / f"{gid}.extra.parquet", compression="zstd", index=False)
                    status[g["file"]] = f"done {gid} {len(rows)} {time.time() - t0:.1f}s"
            except Exception as e:  # noqa: BLE001 - a corrupt archive drops one game, not the pull
                status[g["file"]] = f"dropped: {e!r}"[:200]
            done_now += 1
        spath.write_text(json.dumps(status, indent=0))
    main = sorted(p for p in parts.glob("*.parquet") if not p.name.endswith(".extra.parquet"))
    if not main:
        return []
    complete = all(g["file"] in status for g in games)
    vals = list(status.values())
    n = _concat(main, shard)
    meta = {"source": "sportvu", "league": LEAGUE, "season": SEASON, "license": LICENSE, "tag": schema.PERSONAL,
            "data_plan": "B", "credit": CREDIT, "games": len(main), "events": n,
            "dropped_games": sum(v.startswith("dropped") for v in vals),
            "missing_files": sum(v == "missing" for v in vals), "candidates": len(games), "complete": complete,
            "rows_are": "1 Hz wall-clock tracking summaries (not pbp events)",
            "v1_tokens": n * schema.V1_TOKENS_PER_EVENT + 4 * len(main), "bytes": shard.stat().st_size,
            "schema": "pbp-play-table-1"}
    side.write_text(json.dumps(meta, indent=1, sort_keys=True))
    xn = _concat([p.with_suffix("").with_suffix(".extra.parquet") for p in main], xshard)
    xmeta = {"source": "sportvu_extra", "league": LEAGUE, "season": SEASON, "license": LICENSE,
             "tag": schema.PERSONAL, "data_plan": "B", "credit": CREDIT, "games": 0, "events": 0, "v1_tokens": 0,
             "rows": xn, "keys": ["game_id", "seq"], "columns": list(EXTRA_COLS),
             "bytes": xshard.stat().st_size, "schema": "sportvu-extra-1", "complete": complete,
             "note": "side table of nba_sportvu/2016.parquet; counts 0 events so the ledger never double counts"}
    Path(str(xshard) + ".json").write_text(json.dumps(xmeta, indent=1, sort_keys=True))
    return [meta, xmeta]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--rps", type=float, default=2)
    ap.add_argument("--max-games", type=int)
    a = ap.parse_args()
    for m in pull_all(a.out_dir, a.rps, a.max_games):
        print(json.dumps({k: m[k] for k in ("source", "events", "games", "bytes", "complete") if k in m}))

#!/usr/bin/env python3
"""Media metadata workers (Data Division, type C): which plays got clipped, and how much people watched.

NEVER downloads video, audio or images. It reads public JSON metadata only: clip ids, headlines, tags,
publish times, durations, view counts. Runs on a GitHub cloud runner or Kaggle/Colab CPU, never the Mac.
Stdlib only. Polite: an honest User-Agent, one request per --gap seconds (default 1.0), no cookies, no
logins, and a 401/403/429 is a STOP for that source (a bot block is never a puzzle to solve).

    python scripts/media_pull.py espn --leagues nba,nfl --start 2024-10-22 --end 2025-06-22 --out data/media
    python scripts/media_pull.py mlb --start 2024-03-28 --end 2024-09-29 --out data/media
    python scripts/media_pull.py nhl --start 2024-10-04 --end 2025-04-17 --out data/media
    python scripts/media_pull.py nba --start 2024-10-22 --end 2024-11-05 --out data/media --max-games 50
    YOUTUBE_API_KEY=... python scripts/media_pull.py yt --out data/media          # snapshot (append)
    python scripts/media_pull.py ledger --out data/media                          # counts -> ledger.json

Output: <out>/<source>/<YYYY-MM>.jsonl.gz, one row per clip:
    {src, league, game_id, event_id, clip_id, published, event_time, duration_s, kind, tags, title_kw,
     views, likes, comments, snap_utc, chapters, tokens}
`tokens` is the clip's media token list (field:bucket strings, see MEDIA_FIELDS). Lane 1 owns vocab v2 ids
and maps these strings to ids; this script never assigns ids. Every row carries the personal-use tag.

Licence lines (plan B, personal use only, never for commercial release; docs/datasources/media-metadata.md):
    mlb  MLB Stats API (statsapi.mlb.com): MLBAM copyright notice, non-commercial individual use only. B.
    nhl  NHL api-web (api-web.nhle.com): NHL.com terms restrict scraping; B accepts for personal use. B.
    nba  NBA stats / cdn.nba.com public JSON: NBA.com terms, personal non-commercial use. B.
    yt   YouTube Data API v3 with Nick's own key: YouTube API Services ToS + Developer Policies (refresh or
         delete stored stats within 30 days; no sale; personal use). yellow-B.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

UA = "redzone-media/0.1 (personal research; metadata only, no video; 1 request per second)"
TAG = "personal-use, not for commercial release"
LICENSE = {
    "espn": "ESPN public site API (game summary videos), personal non-commercial use; plan B personal use",
    "mlb": "MLB Stats API, MLBAM non-commercial individual use; plan B personal use",
    "nhl": "NHL api-web, NHL.com terms; plan B personal use",
    "nba": "NBA.com public JSON, personal non-commercial use; plan B personal use",
    "yt": "YouTube Data API v3 (Nick's key), API Services ToS; stats refreshed <=30 days; yellow-B personal use",
}
# Media token fields. NEW = facts we don't hold anywhere else (an editor chose to clip this play; people
# watched it this much). DERIVED = computed from things we already hold (league, linkage). Ledger counts
# them apart, like tokenizer v2's context fields.
MEDIA_FIELDS_NEW = ("clipped", "kind", "dur", "pub_lag", "views", "vel", "rank", "chap")
MEDIA_FIELDS_DERIVED = ("lg", "linked")


class Blocked(Exception):
    pass


class Http:
    def __init__(self, gap: float = 1.0):
        self.gap, self.last, self.calls = gap, 0.0, 0

    def get(self, url: str, timeout: int = 30) -> dict | None:
        wait = self.gap - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        self.calls += 1
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):
                raise Blocked(f"{e.code} on {urllib.parse.urlparse(url).netloc}") from e
            return None                       # 404 etc.: no content for this one
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None


# ---------------------------------------------------------------- buckets (log scale, fixed vocab)
def _log2b(x: float | None, cap: int = 24) -> str:
    if x is None or x < 0:
        return "na"
    return str(min(cap, int(math.log2(x + 1))))


def _dur_b(s: float | None) -> str:
    if s is None:
        return "na"
    for lim, b in ((15, "lt15"), (30, "lt30"), (60, "lt60"), (120, "lt2m"), (300, "lt5m"), (900, "lt15m")):
        if s < lim:
            return b
    return "long"


def _lag_b(sec: float | None) -> str:
    if sec is None:
        return "na"
    for lim, b in ((120, "lt2m"), (600, "lt10m"), (1800, "lt30m"), (3600, "lt1h"), (6 * 3600, "lt6h"),
                   (86400, "lt1d")):
        if sec < lim:
            return b
    return "later"


KIND_WORDS = (("walk-off", "walkoff"), ("walkoff", "walkoff"), ("grand slam", "grand_slam"),
              ("home run", "hr"), ("homer", "hr"), ("strikeout", "k"), ("double play", "dp"),
              ("catch", "catch"), ("goal", "goal"), ("save", "save"), ("hit", "hit"), ("dunk", "dunk"),
              ("block", "block"), ("three", "three"), ("3-pointer", "three"), ("buzzer", "buzzer"),
              ("touchdown", "td"), ("interception", "int"), ("sack", "sack"), ("fumble", "fumble"),
              ("field goal", "fg"), ("recap", "recap"), ("highlights", "game_recap"),
              ("condensed", "condensed"), ("fight", "fight"), ("ejected", "ejection"))


def kind_of(text: str) -> str:
    t = (text or "").lower()
    for w, k in KIND_WORDS:
        if w in t:
            return k
    return "other"


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _hms(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    parts = [p for p in str(s).split(":") if p.strip().isdigit()]
    if not parts:
        return None
    v = 0.0
    for p in parts:
        v = v * 60 + int(p)
    return v


def tokens(row: dict) -> list[str]:
    t = [f"lg:{row['league']}", f"linked:{'y' if row.get('event_id') not in (None, '') else 'n'}",
         "clipped:1", f"kind:{row.get('kind') or 'other'}", f"dur:{_dur_b(row.get('duration_s'))}"]
    pe, ev = _ts(row.get("published")), _ts(row.get("event_time"))
    t.append(f"pub_lag:{_lag_b(pe - ev if pe and ev else None)}")
    if row.get("views") is not None:
        t.append(f"views:{_log2b(row['views'])}")
    if row.get("vel") is not None:
        t.append(f"vel:{_log2b(row['vel'])}")
    if row.get("rank") is not None:
        t.append(f"rank:{row['rank']}")
    if row.get("chapters"):
        t.append(f"chap:{min(len(row['chapters']), 12)}")
    return t


def _days(a: str, b: str):
    d, e = date.fromisoformat(a), date.fromisoformat(b)
    while d <= e:
        yield d
        d += timedelta(days=1)


class Sink:
    """Append rows to <out>/<src>/<YYYY-MM>.jsonl.gz, skipping clip ids already written this run."""

    def __init__(self, out: Path, src: str):
        self.dir = out / src
        self.dir.mkdir(parents=True, exist_ok=True)
        self.seen: set[str] = set()
        for f in self.dir.glob("*.jsonl.gz"):           # incremental: never write a row twice across runs
            try:
                with gzip.open(f, "rt") as fh:
                    for line in fh:
                        r = json.loads(line)
                        self.seen.add(f"{r['clip_id']}|{r.get('snap_utc') or ''}")
            except (OSError, ValueError, EOFError):
                pass
        self.n = 0
        self.fh: dict[str, gzip.GzipFile] = {}

    def add(self, row: dict, month: str) -> None:
        key = f"{row['clip_id']}|{row.get('snap_utc') or ''}"
        if key in self.seen:
            return
        self.seen.add(key)
        row["tag"] = TAG
        row["license"] = LICENSE[row["src"]]
        row["tokens"] = tokens(row)
        if month not in self.fh:
            self.fh[month] = gzip.open(self.dir / f"{month}.jsonl.gz", "at")
        self.fh[month].write(json.dumps(row, separators=(",", ":")) + "\n")
        self.n += 1

    def close(self) -> None:
        for f in self.fh.values():
            f.close()


# ---------------------------------------------------------------- C1: MLB Stats API game content
MLB = "https://statsapi.mlb.com/api/v1"


def pull_mlb(http: Http, sink: Sink, start: str, end: str, max_games: int) -> None:
    sched = http.get(f"{MLB}/schedule?sportId=1&startDate={start}&endDate={end}&gameType=R,F,D,L,W")
    games = [(d["date"], g) for d in (sched or {}).get("dates", []) for g in d.get("games", [])
             if (g.get("status") or {}).get("abstractGameState") == "Final"]
    for i, (day, g) in enumerate(games[: max_games or None]):
        pk = g["gamePk"]
        c = http.get(f"{MLB}/game/{pk}/content") or {}
        items = ((c.get("highlights") or {}).get("highlights") or {}).get("items") or []
        for it in items:
            kw = it.get("keywordsAll") or []
            tags = sorted({f"{k.get('type')}:{k.get('value')}" for k in kw if k.get("type") in
                           ("taxonomy", "player_id", "team_id", "game_pk", "subject")})
            play = it.get("guid") or next((k.get("value") for k in kw if k.get("type") in
                                           ("sv_id", "playid", "play_id")), None)
            text = " ".join([it.get("headline") or "", it.get("blurb") or "",
                             " ".join(t for t in tags if t.startswith("taxonomy:"))])
            sink.add(dict(src="mlb", league="mlb", game_id=str(pk), event_id=play, clip_id=str(it.get("id")),
                          published=it.get("date"), event_time=None, duration_s=_hms(it.get("duration")),
                          kind=kind_of(text), tags=tags, title_kw=_kw(it.get("headline"))), day[:7])
        if i % 50 == 0:
            print(f"mlb {day} game {i + 1}/{len(games)} rows {sink.n} calls {http.calls}", flush=True)


# ---------------------------------------------------------------- C2: NHL api-web gamecenter
NHL = "https://api-web.nhle.com/v1"


def pull_nhl(http: Http, sink: Sink, start: str, end: str, max_games: int) -> None:
    n = 0
    for d in _days(start, end):
        day = d.isoformat()
        sc = http.get(f"{NHL}/score/{day}") or {}
        for g in sc.get("games", []):
            if g.get("gameState") not in ("OFF", "FINAL"):
                continue
            gid = str(g["id"])
            land = http.get(f"{NHL}/gamecenter/{gid}/landing") or {}
            for per in ((land.get("summary") or {}).get("scoring") or []):
                for goal in per.get("goals") or []:
                    clip = goal.get("highlightClip") or goal.get("highlightClipSharingUrl")
                    if not clip:
                        continue
                    who = (goal.get("name") or {}).get("default") or ""
                    sink.add(dict(src="nhl", league="nhl", game_id=gid, event_id=str(goal.get("eventId") or ""),
                                  clip_id=str(goal.get("highlightClip") or clip), published=None, event_time=None,
                                  duration_s=None, kind="goal",
                                  tags=[f"period:{(per.get('periodDescriptor') or {}).get('number')}",
                                        f"t:{goal.get('timeInPeriod')}", f"strength:{goal.get('strength')}",
                                        f"player_id:{goal.get('playerId')}", f"shot:{goal.get('shotType')}",
                                        f"goal_mod:{goal.get('goalModifier')}"],
                                  title_kw=_kw(who)), day[:7])
            rr = http.get(f"{NHL}/gamecenter/{gid}/right-rail") or {}
            for k, v in (rr.get("gameVideo") or {}).items():
                if v:
                    sink.add(dict(src="nhl", league="nhl", game_id=gid, event_id=None, clip_id=f"{k}:{v}",
                                  published=None, event_time=None, duration_s=None,
                                  kind="condensed" if "condensed" in k.lower() else "game_recap",
                                  tags=[f"video:{k}"], title_kw=[]), day[:7])
            n += 1
            if max_games and n >= max_games:
                return
        print(f"nhl {day} games {n} rows {sink.n} calls {http.calls}", flush=True)


# ---------------------------------------------------------------- C3: NBA clipped events
NBA_CDN = "https://cdn.nba.com/static/json"
NBA_STATS = "https://stats.nba.com/stats"


def pull_nba(http: Http, sink: Sink, start: str, end: str, max_games: int) -> None:
    """Public schedule + live pbp from cdn.nba.com; the video flag/asset from stats.nba.com only if it
    answers an honest UA. If stats.nba.com blocks, we stop the stats half (no browser spoofing)."""
    sch = http.get(f"{NBA_CDN}/staticData/scheduleLeagueV2.json") or {}
    a, b = start.replace("-", ""), end.replace("-", "")
    gids = []
    for gd in (sch.get("leagueSchedule") or {}).get("gameDates", []):
        for g in gd.get("games", []):
            dt = (g.get("gameDateEst") or "")[:10].replace("-", "")
            if a <= dt <= b and g.get("gameStatus") == 3:
                gids.append((dt, g["gameId"]))
    stats_ok = True
    for i, (dt, gid) in enumerate(gids[: max_games or None]):
        if not stats_ok:
            break
        try:
            pbp = http.get(f"{NBA_STATS}/playbyplayv3?GameID={gid}&StartPeriod=0&EndPeriod=0", timeout=20)
        except Blocked as e:
            print(f"nba stats blocked ({e}); stopping the stats half, no bypass", flush=True)
            stats_ok = False
            break
        if pbp is None:
            print("nba stats no answer to an honest UA; stopping the stats half (no bypass)", flush=True)
            stats_ok = False
            break
        for a_ in ((pbp.get("game") or {}).get("actions") or []):
            if not a_.get("videoAvailable"):
                continue
            ev = str(a_.get("actionNumber"))
            sink.add(dict(src="nba", league="nba", game_id=gid, event_id=ev, clip_id=f"{gid}:{ev}",
                          published=None, event_time=None, duration_s=None,
                          kind=kind_of(f"{a_.get('actionType')} {a_.get('subType')} {a_.get('description')}"),
                          tags=[f"period:{a_.get('period')}", f"clock:{a_.get('clock')}",
                                f"type:{a_.get('actionType')}", f"player_id:{a_.get('personId')}"],
                          title_kw=[]), f"{dt[:4]}-{dt[4:6]}")
        print(f"nba {dt} game {i + 1}/{len(gids)} rows {sink.n}", flush=True)


# ---------------------------------------------------------------- C7: ESPN game-summary videos (NBA, NFL first)
ESPN = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_PATH = {"nba": "basketball/nba", "nfl": "football/nfl", "cbb": "basketball/mens-college-basketball",
             "cfb": "football/college-football"}


def pull_espn(http: Http, sink: Sink, start: str, end: str, max_games: int, leagues: list[str]) -> None:
    """Per game: the clips ESPN attached to the game summary (headline, duration, publish time, linked
    athletes/plays where present). Metadata only; video URLs are never fetched or stored."""
    n = 0
    for d in _days(start, end):
        ymd = d.strftime("%Y%m%d")
        for lg in leagues:
            sb = http.get(f"{ESPN}/{ESPN_PATH[lg]}/scoreboard?dates={ymd}" +
                          ("&groups=50&limit=400" if lg == "cbb" else "&groups=80" if lg == "cfb" else "")) or {}
            for ev in sb.get("events", []):
                st = ((ev.get("status") or {}).get("type") or {})
                if not st.get("completed"):
                    continue
                gid = str(ev.get("id"))
                summ = http.get(f"{ESPN}/{ESPN_PATH[lg]}/summary?event={gid}") or {}
                start_t = ev.get("date")
                for v in summ.get("videos") or []:
                    kws = [k for k in (v.get("keywords") or []) if isinstance(k, str)]
                    ath = [f"athlete:{c.get('athleteId') or c.get('id')}" for c in (v.get("categories") or [])
                           if (c.get("type") == "athlete")]
                    play = next((str(c.get("playId") or c.get("id")) for c in (v.get("categories") or [])
                                 if c.get("type") in ("play", "event") and (c.get("playId") or c.get("id"))), None)
                    pub = v.get("originalPublishDate") or v.get("lastModified")
                    text = f"{v.get('headline') or ''} {v.get('description') or ''} {' '.join(kws)}"
                    sink.add(dict(src="espn", league=lg, game_id=gid, event_id=play, clip_id=f"espn:{v.get('id')}",
                                  published=pub, event_time=start_t, duration_s=v.get("duration"),
                                  kind=kind_of(text), tags=ath + [f"kw:{k.lower()}" for k in kws[:8]],
                                  title_kw=_kw(v.get("headline"))), f"{ymd[:4]}-{ymd[4:6]}")
                n += 1
                if max_games and n >= max_games:
                    return
        print(f"espn {ymd} games {n} rows {sink.n} calls {http.calls}", flush=True)


# ---------------------------------------------------------------- C4: YouTube official channels
YT = "https://www.googleapis.com/youtube/v3"
YT_HANDLES = {"nfl": "@NFL", "nba": "@NBA", "nhl": "@NHL", "mlb": "@MLB", "cbb": "@MarchMadness",
              "cfb": "@ESPNCFB"}
_CHAP = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(.+)$", re.M)


def _iso_dur(s: str | None) -> float | None:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    return None if not m else sum(int(v or 0) * k for v, k in zip(m.groups(), (3600, 60, 1)))


def pull_yt(http: Http, sink: Sink, out: Path, pages: int) -> None:
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        print("yt: no YOUTUBE_API_KEY; skipping (Nick adds it as a GitHub Actions secret)", flush=True)
        return
    snap = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    prev_f = out / "yt" / "last_views.json"
    prev = json.loads(prev_f.read_text()) if prev_f.exists() else {}
    now_views: dict[str, list] = {}
    for lg, handle in YT_HANDLES.items():
        ch = http.get(f"{YT}/channels?part=contentDetails&forHandle={urllib.parse.quote(handle)}&key={key}") or {}
        if not ch.get("items"):
            print(f"yt {handle}: not found", flush=True)
            continue
        uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        tok, vids = "", []
        for _ in range(pages):
            pl = http.get(f"{YT}/playlistItems?part=contentDetails&maxResults=50&playlistId={uploads}"
                          f"&key={key}" + (f"&pageToken={tok}" if tok else "")) or {}
            vids += [x["contentDetails"]["videoId"] for x in pl.get("items", [])]
            tok = pl.get("nextPageToken") or ""
            if not tok:
                break
        rows = []
        for j in range(0, len(vids), 50):
            vv = http.get(f"{YT}/videos?part=snippet,contentDetails,statistics&id={','.join(vids[j:j + 50])}"
                          f"&key={key}") or {}
            for v in vv.get("items", []):
                sn, st = v.get("snippet") or {}, v.get("statistics") or {}
                views = int(st.get("viewCount", 0))
                p = prev.get(v["id"])
                vel = None
                if p:
                    hrs = max((_ts(snap.replace("Z", ":00Z")) - _ts(p[1])) / 3600, 1e-3)
                    vel = max(0.0, (views - p[0]) / hrs)          # views per hour since last snapshot
                now_views[v["id"]] = [views, snap.replace("Z", ":00Z")]
                chaps = [{"t": _hms(m.group(1)), "label_kw": _kw(m.group(2))}
                         for m in _CHAP.finditer(sn.get("description") or "")]
                rows.append(dict(src="yt", league=lg, game_id=None, event_id=None, clip_id=v["id"],
                                 published=sn.get("publishedAt"), event_time=None,
                                 duration_s=_iso_dur((v.get("contentDetails") or {}).get("duration")),
                                 kind=kind_of(sn.get("title")), tags=[f"channel:{handle}"],
                                 title_kw=_kw(sn.get("title")), views=views,
                                 likes=int(st["likeCount"]) if "likeCount" in st else None,
                                 comments=int(st["commentCount"]) if "commentCount" in st else None,
                                 vel=vel, snap_utc=snap, chapters=chaps if len(chaps) >= 2 else []))
        # Daily Top-10 per channel by velocity (falls back to views on the first snapshot).
        ranked = sorted(rows, key=lambda r: -(r["vel"] if r["vel"] is not None else r["views"] / 1e6))
        for k, r in enumerate(ranked):
            r["rank"] = k + 1 if k < 10 else None
            sink.add(r, snap[:7])
        print(f"yt {handle}: {len(rows)} videos, calls {http.calls}", flush=True)
    prev.update(now_views)
    prev_f.write_text(json.dumps(prev))


_STOP = {"the", "a", "an", "of", "and", "in", "on", "vs", "at", "to", "for", "with", "his", "her", "is"}


def _kw(s: str | None) -> list[str]:
    """Title keywords as a short fixed-form list (no free text goes to the tokenizer; lane 1 maps
    names to player/team ids)."""
    return [w for w in re.findall(r"[a-z0-9']+", (s or "").lower()) if w not in _STOP][:12]


# ---------------------------------------------------------------- ledger
def ledger(out: Path) -> dict:
    tot: dict[str, dict] = {}
    for f in sorted(out.glob("*/*.jsonl.gz")):
        src = f.parent.name
        s = tot.setdefault(src, dict(clips=0, linked=0, tokens_new=0, tokens_derived=0, bytes=0,
                                     license=LICENSE.get(src, "?"), tag=TAG))
        s["bytes"] += f.stat().st_size
        with gzip.open(f, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                s["clips"] += 1
                s["linked"] += r.get("event_id") not in (None, "")
                for t in r.get("tokens", []):
                    if t.split(":", 1)[0] in MEDIA_FIELDS_NEW:
                        s["tokens_new"] += 1
                    else:
                        s["tokens_derived"] += 1
    led = dict(updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), sources=tot,
               total_new=sum(v["tokens_new"] for v in tot.values()),
               total_derived=sum(v["tokens_derived"] for v in tot.values()))
    (out / "ledger.json").write_text(json.dumps(led, indent=1) + "\n")
    return led


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", choices=["espn", "mlb", "nhl", "nba", "yt", "ledger"])
    ap.add_argument("--leagues", default="nba,nfl", help="espn: comma list of nba,nfl,cbb,cfb")
    ap.add_argument("--start", default=(date.today() - timedelta(days=2)).isoformat())
    ap.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat())
    ap.add_argument("--out", type=Path, default=Path("data/media"))
    ap.add_argument("--max-games", type=int, default=0)
    ap.add_argument("--pages", type=int, default=4, help="yt: 50-video pages per channel per snapshot")
    ap.add_argument("--gap", type=float, default=1.0, help="seconds between requests")
    a = ap.parse_args(argv)
    if a.source == "ledger":
        print(json.dumps(ledger(a.out), indent=1))
        return 0
    http, sink = Http(a.gap), Sink(a.out, a.source)
    try:
        if a.source == "mlb":
            pull_mlb(http, sink, a.start, a.end, a.max_games)
        elif a.source == "nhl":
            pull_nhl(http, sink, a.start, a.end, a.max_games)
        elif a.source == "espn":
            pull_espn(http, sink, a.start, a.end, a.max_games, a.leagues.split(","))
        elif a.source == "nba":
            pull_nba(http, sink, a.start, a.end, a.max_games)
        else:
            pull_yt(http, sink, a.out, a.pages)
    except Blocked as e:
        print(f"{a.source}: STOP, {e} (no retry, no bypass)", flush=True)
    finally:
        sink.close()
    print(f"{a.source}: {sink.n} rows, {http.calls} calls", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

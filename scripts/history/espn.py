"""ESPN public JSON -> one compact, validated record per finished game, fetched politely and resumably.

Why: B-01 (tracks/B-foundation-model.md B0): ESPN is the only source with play-by-play for every league Nick watches, and
the planned 130M-event corpus needs about 66,000 more games. Everything here is built so a kill at any moment loses
nothing and a rerun repeats nothing.

Flow per league and season (newest season first):
  calendar  one scoreboard request names the season's units: days (nba, cbb, wcbb, nhl, mlb) or season-type weeks (nfl, cfb)
  slate     one scoreboard request per unit lists its games; only final games ("post") are kept, fenced nights are skipped
  summary   one request per game; the record keeps the plays (small, pruned) and drops everything else
The record never keeps `winprobability`, odds, boxscore or other final-state fields (tracks/B-foundation-model.md risk 3:
hindsight in ESPN summaries). Plays keep only ids, types, clocks, scores, teams and athlete ids (names, headshots, links dropped).

Done states in the ledger: ok (plays stored), no_plays (summary exists, zero plays: older seasons), not_found (HTTP 404).
A transient failure is not recorded, so the next run retries that game. Compatible with scripts/noggin_tokens.games_of
once packed (pack.py): id, plays, matchup, date.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable

from .leagues import EVAL_FIRST_NIGHT, LEAGUES, LOCKED_WINDOW, League, fenced, night_of, season_fully_fenced
from .licences import PERSONAL_TAG, SOURCES
from .net import Blocked, FetchError, PoliteClient, check_robots
from .store import ID_RE, Store, read_game, write_game

BASE = "https://site.api.espn.com/apis/site/v2/sports"
SLATE_LIMIT = 1000           # events asked for per scoreboard; a full page means the slate may be cut off (an error)
EMPTY_RUN = 25               # a season whose first this-many finished games all lack plays (and none has any) is abandoned: old seasons with no play-by-play
BREAKER = 8                  # consecutive failed requests before the run stops
RECENT_DAYS = 3              # units this close to today are not cached and their unfinished games are not final yet
DROP_KEYS = frozenset({"headshot", "headshots", "links", "logo", "logos", "displayName", "shortName", "fullName", "flag", "video",
                       "videos", "image", "images", "href", "alt", "uid", "guid", "$ref", "ref", "lastName", "firstName", "jersey"})
BAD_SHAPE = (KeyError, TypeError, ValueError, AttributeError)   # a response that is not shaped as expected is an error for that item, not a crash
WEEK_FALLBACK = {"nfl": {1: 4, 2: 18, 3: 5}, "cfb": {1: 1, 2: 16, 3: 3}}


# ---- shape helpers ------------------------------------------------------------

def _get(o: Any, *path: str) -> Any:
    for k in path:
        if not isinstance(o, dict):
            return None
        o = o.get(k)
    return o


def prune(v: Any, depth: int = 0) -> Any:
    """Keep scalars; keep dicts minus bulky or name-like keys; keep the first 12 list items. Bounded depth."""
    if isinstance(v, dict):
        if depth > 4:
            return None
        return {k: prune(x, depth + 1) for k, x in v.items() if k not in DROP_KEYS}
    if isinstance(v, list):
        return [prune(x, depth + 1) for x in v[:12]]
    return v


def flatten_plays(lg: League, summary: dict) -> list[dict]:
    """Basketball, hockey, baseball: summary.plays. Football: the plays of every drive (previous then current), by sequence."""
    if lg.code in ("nfl", "cfb"):
        plays: list[dict] = []
        for dr in _get(summary, "drives", "previous") or []:
            plays.extend(dr.get("plays") or [] if isinstance(dr, dict) else [])
        cur = _get(summary, "drives", "current")
        if isinstance(cur, dict):
            plays.extend(cur.get("plays") or [])
        plays = [p for p in plays if isinstance(p, dict)]
        plays.sort(key=lambda p: int(str(p.get("sequenceNumber") or 0)) if str(p.get("sequenceNumber") or "0").isdigit() else 0)
        return plays
    p = summary.get("plays")
    return [x for x in p if isinstance(x, dict)] if isinstance(p, list) else []


def play_ok(p: dict) -> bool:
    """A valid event: an id or sequence number, and a type or text."""
    return (p.get("id") is not None or p.get("sequenceNumber") is not None) and bool(p.get("type") or p.get("text"))


def game_record(lg: League, season: int, stub: dict, summary: dict, fetched: str) -> dict:
    seen: set[str] = set()
    kept, bad, dup = [], 0, 0
    for p in flatten_plays(lg, summary):
        if not play_ok(p):
            bad += 1
            continue
        key = str(p.get("id") if p.get("id") is not None else p.get("sequenceNumber"))
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        kept.append(prune(p))
    n = len(kept)
    comp = (_get(summary, "header", "competitions") or [{}])[0]
    sides: dict[str, dict] = {}
    for c in comp.get("competitors") or []:
        sides[c.get("homeAway")] = {"id": str(_get(c, "team", "id") or ""), "abbr": _get(c, "team", "abbreviation") or "",
                                    "score": c.get("score")}
    home, away = sides.get("home") or stub["home"], sides.get("away") or stub["away"]
    night = stub["night"]
    return {
        "v": 1, "source": "espn", "league": lg.code, "season": season, "id": stub["id"], "start": stub["start"], "night": night,
        "date": f"{night[:4]}-{night[4:6]}-{night[6:8]}", "season_type": stub.get("season_type"), "week": stub.get("week"),
        "matchup": f"{away.get('abbr') or '?'} @ {home.get('abbr') or '?'}", "home": home, "away": away,
        "wallclock_available": bool(summary.get("wallclockAvailable")), "n_plays": n, "dropped_invalid": bad, "dropped_duplicate": dup,
        "wall_frac": round(sum(1 for p in kept if p.get("wallclock")) / n, 4) if n else 0.0,
        "clock_frac": round(sum(1 for p in kept if _get(p, "clock", "displayValue") not in (None, "")) / n, 4) if n else 0.0,
        "plays": kept, "fetched": fetched, "tag": PERSONAL_TAG,
    }


# ---- calendar, slate -----------------------------------------------------------

def probe_params(lg: League, season: int) -> dict:
    """Scoreboard parameters whose response also names the season's calendar."""
    if lg.kind == "week":
        p: dict[str, Any] = {"dates": str(season), "seasontype": 2, "week": 1}
    else:
        p = {"dates": f"{season}{lg.probe[0]:02d}{lg.probe[1]:02d}"}
    if lg.groups:
        p["groups"] = lg.groups
    return p


def _ymd(iso: str) -> str:
    return str(iso)[:10].replace("-", "")


def parse_calendar(lg: League, season: int, data: dict) -> dict:
    """{'units': [...], 'source': 'espn'|'fallback', 'season_year': int|None, 'note': str}. A unit is
    {'kind': 'd', 'date': 'YYYYMMDD', 'key': 'd..'} or {'kind': 'w', 'type': 2, 'week': 5, 'key': 'w2-05', 'start', 'end'}."""
    league_obj = (data.get("leagues") or [{}])[0] if isinstance(data.get("leagues"), list) else {}
    cal = league_obj.get("calendar")
    year = _get(league_obj, "season", "year")
    note = ""
    if year is not None and int(year) != season:
        return {"units": [], "source": "espn", "season_year": int(year), "note": f"ESPN returned season {year} for a request of {season}: no such season"}
    units: list[dict] = []
    if lg.kind == "day":
        days: list[str] = []
        if isinstance(cal, list) and cal and all(isinstance(x, str) for x in cal) and league_obj.get("calendarIsWhitelist"):
            days = sorted({_ymd(x) for x in cal})
        else:
            a, b = league_obj.get("calendarStartDate"), league_obj.get("calendarEndDate")
            try:
                d0, d1 = dt.date.fromisoformat(str(a)[:10]), dt.date.fromisoformat(str(b)[:10])
                if d1 < d0:
                    raise ValueError("calendar end before start")
            except ValueError:
                (m0, d0m), (m1, d1m) = lg.fallback
                y0 = season - 1 if lg.label == "end" else season
                d0, d1 = dt.date(y0, m0, d0m), dt.date(season if lg.label == "end" else season + (1 if m1 < m0 else 0), m1, d1m)
                note = "no calendar from ESPN: default window"
            days = [(d0 + dt.timedelta(n)).strftime("%Y%m%d") for n in range((d1 - d0).days + 1)]
        units = [{"kind": "d", "date": d, "key": f"d{d}", "start": d, "end": d} for d in days]
    else:
        pairs: list[tuple[int, int, str, str]] = []
        if isinstance(cal, list):
            for t in cal:
                if isinstance(t, dict) and str(t.get("value", "")).isdigit():
                    for e in t.get("entries") or []:
                        if isinstance(e, dict) and str(e.get("value", "")).isdigit():
                            pairs.append((int(t["value"]), int(e["value"]), _ymd(e.get("startDate") or t.get("startDate") or ""),
                                          _ymd(e.get("endDate") or t.get("endDate") or "")))
        if not pairs:
            note = "no calendar from ESPN: default weeks"
            pairs = [(ty, w, "", "") for ty, n in WEEK_FALLBACK[lg.code].items() for w in range(1, n + 1)]
        units = [{"kind": "w", "type": ty, "week": w, "key": f"w{ty}-{w:02d}", "start": s, "end": e} for ty, w, s, e in pairs]
    return {"units": units, "source": "espn" if not note else "fallback", "season_year": int(year) if year is not None else None, "note": note}


def unit_fenced(lg: League, unit: dict) -> bool:
    """A whole unit that is entirely on or after the first evaluation night, or inside the locked window, is not requested."""
    s, e = unit.get("start") or "", unit.get("end") or ""
    if not s or not e or len(s) != 8 or len(e) != 8:
        return False
    return s >= EVAL_FIRST_NIGHT[lg.code] or (s >= LOCKED_WINDOW[0] and e <= LOCKED_WINDOW[1])


def unit_params(lg: League, season: int, unit: dict) -> dict:
    p: dict[str, Any] = {"dates": unit["date"]} if unit["kind"] == "d" else {"dates": str(season), "seasontype": unit["type"], "week": unit["week"]}
    if lg.groups:
        p["groups"] = lg.groups
    p["limit"] = SLATE_LIMIT
    return p


def parse_stub(e: dict) -> dict | None:
    gid = str(e.get("id") or "")
    if not ID_RE.match(gid):
        return None
    comp = (e.get("competitions") or [{}])[0] if isinstance(e.get("competitions"), list) else {}
    sides: dict[str, dict] = {}
    for c in comp.get("competitors") or []:
        sides[c.get("homeAway")] = {"id": str(_get(c, "team", "id") or ""), "abbr": _get(c, "team", "abbreviation") or "", "score": c.get("score")}
    start = str(e.get("date") or "")
    return {"id": gid, "start": start, "night": night_of(start), "state": _get(e, "status", "type", "state") or "",
            "season_year": _get(e, "season", "year"), "season_type": _get(e, "season", "type"), "week": _get(e, "week", "number"),
            "name": e.get("shortName") or e.get("name") or "", "home": sides.get("home") or {}, "away": sides.get("away") or {}}


# ---- the fetcher --------------------------------------------------------------

@dataclass
class Limits:
    max_games: int | None = None
    max_requests: int | None = None
    deadline: float | None = None     # on the same clock as `clock`


@dataclass
class Run:
    client: PoliteClient
    store: Store
    limits: Limits = field(default_factory=Limits)
    log: Callable[[str], None] = print
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)
    clock: Callable[[], float] = None  # type: ignore[assignment]
    stop_flag: Callable[[], bool] = lambda: False
    types: tuple[int, ...] = (2, 3)       # ESPN season types kept: 2 regular season, 3 postseason (1 = preseason is off by default)
    summary: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        import time
        self.clock = self.clock or time.monotonic
        self.t0 = self.clock()
        self.fail_streak = 0
        self.summary = {"games_ok": 0, "games_no_plays": 0, "games_not_found": 0, "events": 0, "skipped_done": 0, "skipped_fenced": 0,
                        "skipped_not_final": 0, "skipped_recent": 0, "skipped_type": 0, "adopted": 0, "units": 0, "errors": [], "stop_reason": "complete", "leagues": {}}

    # -- control
    def _stop_reason(self) -> str | None:
        lim = self.limits
        if self.stop_flag():
            return "signal"
        if lim.max_games is not None and self.summary["games_ok"] + self.summary["games_no_plays"] + self.summary["games_not_found"] >= lim.max_games:
            return "max_games"
        if lim.max_requests is not None and self.client.stats.requests >= lim.max_requests:
            return "max_requests"
        if lim.deadline is not None and self.clock() >= lim.deadline:
            return "deadline"
        return None

    def _fail(self, what: str, err: Exception) -> None:
        self.fail_streak += 1
        self.summary["errors"].append(f"{what}: {err}")
        self.log(f"ERROR {what}: {err}")
        if self.fail_streak >= BREAKER:
            raise _Stop("breaker")

    # -- steps
    def calendar(self, lg: League, season: int) -> dict:
        rel = f"calendars/espn_{lg.code}_{season}.json"
        cached = self.store.read_json(rel)
        if cached and cached.get("units") is not None:
            return cached
        r = self.client.get_json(f"{BASE}/{lg.path}/scoreboard", probe_params(lg, season))
        self.fail_streak = 0
        cal = parse_calendar(lg, season, r.data)
        cal["fetched"] = self.now().isoformat(timespec="seconds")
        self.store.write_json(rel, cal)
        return cal

    def slate(self, lg: League, season: int, unit: dict) -> dict:
        rel = f"slates/espn/{lg.code}/{season}/{unit['key']}.json"
        cached = self.store.read_json(rel)
        if cached and isinstance(cached.get("games"), list):
            return cached
        r = self.client.get_json(f"{BASE}/{lg.path}/scoreboard", unit_params(lg, season, unit))
        self.fail_streak = 0
        events = r.data.get("events")
        if not isinstance(events, list):
            raise FetchError(f"scoreboard {unit['key']} has no events list")
        if len(events) >= SLATE_LIMIT:
            raise FetchError(f"scoreboard {unit['key']} returned {len(events)} events = the limit; it may be cut off")
        stubs = [s for s in (parse_stub(e) for e in events if isinstance(e, dict)) if s]
        obj = {"unit": unit, "events": len(events), "malformed": len(events) - len(stubs), "games": stubs,
               "fetched": self.now().isoformat(timespec="seconds")}
        newest = max((s["start"] for s in stubs), default="")
        recent = self.now() - dt.timedelta(days=RECENT_DAYS)
        if not newest or newest[:10] < recent.strftime("%Y-%m-%d"):
            self.store.write_json(rel, obj)
        return obj

    def game(self, lg: League, season: int, stub: dict, ledger: dict[str, dict]) -> None:
        gid = stub["id"]
        path = self.store.game_path(lg.code, season, gid)
        base = {"id": gid, "league": lg.code, "season": season, "night": stub["night"]}
        rec = None
        if path.exists():       # a kill between the rename and the ledger append: adopt the file (a damaged one is refetched)
            try:
                rec = read_game(path)
            except (OSError, ValueError, EOFError):
                path.unlink(missing_ok=True)
        if rec is not None:
            e = {**base, "status": "ok", "plays": len(rec["plays"]), "wall": rec.get("wall_frac"), "clock": rec.get("clock_frac"),
                 "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "t": self.now().isoformat(timespec="seconds"), "adopted": True}
            self.store.append_ledger("espn", lg.code, e)
            ledger[gid] = e
            self.summary["adopted"] += 1
            return
        r = self.client.get_json(f"{BASE}/{lg.path}/summary", {"event": gid}, allow_404=True)
        self.fail_streak = 0
        t = self.now().isoformat(timespec="seconds")
        if r.status == 404:
            e = {**base, "status": "not_found", "t": t}
        else:
            hid = str(_get(r.data, "header", "id") or "")
            if hid != gid:
                raise FetchError(f"summary for event {gid} carries header id {hid!r}; not stored")
            rec = game_record(lg, season, stub, r.data, t)
            if rec["n_plays"] == 0:
                e = {**base, "status": "no_plays", "t": t, "wallclock_available": rec["wallclock_available"]}
            else:
                nbytes, sha = write_game(path, rec)
                e = {**base, "status": "ok", "plays": rec["n_plays"], "wall": rec["wall_frac"], "clock": rec["clock_frac"], "bytes": nbytes,
                     "sha256": sha, "t": t}
                self.summary["events"] += rec["n_plays"]
        self.store.append_ledger("espn", lg.code, e)
        ledger[gid] = e
        self.summary["games_" + e["status"]] += 1
        lstat = self.summary["leagues"][lg.code]
        lstat["games_" + e["status"]] = lstat.get("games_" + e["status"], 0) + 1
        lstat["events"] += int(e.get("plays") or 0)

    # -- the loop
    def run(self, leagues: list[str], seasons: list[int]) -> dict:
        try:
            for code in leagues:
                lg = LEAGUES[code]
                ledger = self.store.read_ledger("espn", code)
                self.summary["leagues"].setdefault(code, {"events": 0})
                for season in seasons:
                    self._season(lg, season, ledger)
        except _Stop as s:
            self.summary["stop_reason"] = s.reason
        except Blocked as b:
            self.summary["stop_reason"] = "blocked"
            self.summary["errors"].append(str(b))
            self.log(f"BLOCKED: {b}")
        st = self.client.stats
        self.summary.update(requests=st.requests, retries=st.retries, bytes=st.bytes, by_status=dict(st.by_status),
                            seconds=round(self.clock() - self.t0, 1), ca_bundle=self.client.tls)
        return self.summary

    def _check(self) -> None:
        why = self._stop_reason()
        if why:
            raise _Stop(why)

    @staticmethod
    def _season_counts(ledger: dict[str, dict], season: int) -> tuple[int, int]:
        """(games with plays, games without plays or 404) already finished for this season."""
        ok = sum(1 for e in ledger.values() if e.get("season") == season and e.get("status") == "ok")
        empty = sum(1 for e in ledger.values() if e.get("season") == season and e.get("status") in ("no_plays", "not_found"))
        return ok, empty

    def _season(self, lg: League, season: int, ledger: dict[str, dict]) -> None:
        self._check()
        if season_fully_fenced(lg.code, season):
            self.log(f"{lg.code} {season}: entirely on or after the first evaluation night, skipped")
            return
        try:
            cal = self.calendar(lg, season)
        except Blocked:
            raise
        except (FetchError, *BAD_SHAPE) as e:
            self._fail(f"{lg.code} {season} calendar", e)
            return
        if not cal["units"]:
            self.log(f"{lg.code} {season}: no units ({cal.get('note') or 'empty calendar'})")
            return
        ok_n, empty_n = self._season_counts(ledger, season)
        if ok_n == 0 and empty_n >= EMPTY_RUN:
            self.log(f"{lg.code} {season}: {empty_n} finished games and none has plays; skipped (delete its ledger lines to retry)")
            self.summary["seasons_empty"] = self.summary.get("seasons_empty", 0) + 1
            return
        today = self.now().strftime("%Y%m%d")
        before = self.summary["games_ok"]
        for unit in cal["units"]:
            self._check()
            if unit_fenced(lg, unit):
                self.summary["skipped_fenced"] += 1
                continue
            if unit.get("start") and unit["start"] >= (self.now() - dt.timedelta(days=1)).strftime("%Y%m%d"):
                self.summary["skipped_recent"] += 1
                continue
            try:
                sl = self.slate(lg, season, unit)
            except Blocked:
                raise
            except (FetchError, *BAD_SHAPE) as e:
                self._fail(f"{lg.code} {season} {unit['key']}", e)
                continue
            self.summary["units"] += 1
            for stub in sl["games"]:
                if stub["state"] != "post":
                    self.summary["skipped_not_final"] += 1
                    continue
                if fenced(lg.code, stub["night"]):
                    self.summary["skipped_fenced"] += 1
                    continue
                if stub["night"] >= today:
                    self.summary["skipped_recent"] += 1
                    continue
                if stub.get("season_type") is not None and stub["season_type"] not in self.types:
                    self.summary["skipped_type"] += 1
                    continue
                done = ledger.get(stub["id"])
                if done and done.get("status") in ("ok", "no_plays", "not_found"):
                    self.summary["skipped_done"] += 1
                    continue
                self._check()
                try:
                    self.game(lg, season, stub, ledger)
                except Blocked:
                    raise
                except (FetchError, *BAD_SHAPE) as e:
                    self._fail(f"{lg.code} {season} game {stub['id']}", e)
                    continue
                if ledger[stub["id"]]["status"] == "ok":
                    ok_n += 1
                else:
                    empty_n += 1
                if ok_n == 0 and empty_n >= EMPTY_RUN:
                    self.log(f"{lg.code} {season}: the first {empty_n} games have no plays; abandoning the season")
                    self.summary["seasons_empty"] = self.summary.get("seasons_empty", 0) + 1
                    return
                n = self.summary["games_ok"] + self.summary["games_no_plays"] + self.summary["games_not_found"]
                if n % 25 == 0:
                    el = max(1.0, self.clock() - self.t0)
                    self.log(f"[{lg.code} {season}] {n} games this run, {self.summary['events']:,} events, {self.client.stats.requests / el:.2f} req/s, {int(el)} s")
        self.log(f"{lg.code} {season}: done ({self.summary['games_ok'] - before} new games)")


class _Stop(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def start_checks(client: PoliteClient, source: str = "espn") -> dict:
    """The robots verdict for ESPN's API host, recorded in the manifest. Raises SystemExit on a deny, or on an unavailable
    robots.txt when the source registry does not accept that."""
    res = check_robots(client, BASE, "/apis/site/v2/sports/basketball/nba/summary")
    src = SOURCES[source]
    if res["verdict"] == "deny":
        raise SystemExit(f"robots.txt of {res['host']} disallows the path (or says ai-train=no): not fetching. {res}")
    if res["verdict"] == "unavailable" and not src.robots_ok_when_unavailable:
        raise SystemExit(f"robots.txt of {res['host']} is unavailable ({res.get('note')}) and the registry does not accept that for {source}")
    return res

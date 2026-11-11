"""ESPN's free site API: the backbone for all four leagues.

Why: a free public feed covers every league the app needs and its fields can be pinned against saved responses in tests/fixtures (docstring).

    scoreboard: site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard
    summary:    .../summary?event={id}   (play-by-play with wallclock, win probability)

Field names verified against saved responses in tests/fixtures (Nov 2026).
Every lookup goes through _get(), so a missing field is None, never a crash.

The same summary parser serves live games and the backtester: `parse_summary(..., upto=i)`
rebuilds the game exactly as it stood after play i.
"""
from __future__ import annotations

import re
from datetime import datetime

from redzone.models import PERIOD_S, REGULATION, GameState, League, Phase, Play, Situation, Stoppage, Team
from redzone.scoring import postseason

BASE = "https://site.api.espn.com/apis/site/v2/sports"
PATH = {League.NFL: "football/nfl", League.NBA: "basketball/nba",
        League.NHL: "hockey/nhl", League.MLB: "baseball/mlb",
        League.CBB: "basketball/mens-college-basketball",
        League.WCBB: "basketball/womens-college-basketball",
        League.CFB: "football/college-football"}
OT_S = {League.NFL: 600, League.NBA: 300, League.NHL: 300, League.MLB: 0,
        League.CBB: 300, League.WCBB: 300, League.CFB: 0}  # college football OT is untimed
# ESPN's image-path name for the college leagues (headshots).
IMG = {League.CBB: "mens-college-basketball", League.WCBB: "womens-college-basketball",
       League.CFB: "college-football"}
TOP_N = 25  # college: follow ranked teams down to this poll rank


def scoreboard_urls(league: League, groups: list[str] | None = None, date: str | None = None) -> list[str]:
    """Scoreboards to poll for a league. Pro leagues: the one board. College: ESPN's default board
    (the ranked/featured games, never the whole of Division I) plus one board per conference you
    follow (ESPN `groups`, e.g. ACC = 2 in basketball, 1 in football) so your unranked team is there."""
    base = scoreboard_url(league, date)
    if not league.college:
        return [base]
    sep = "&" if "?" in base else "?"
    return [base] + [f"{base}{sep}groups={g}" for g in groups or []]


def keep_college(g: GameState, mine: set[str], top_n: int = TOP_N) -> bool:
    """College boards are big: keep your teams' games and games with a ranked team, nothing else."""
    if g.involves(mine):
        return True
    return any(t.rank is not None and t.rank <= top_n for t in (g.home, g.away))


def scoreboard_url(league: League, date: str | None = None) -> str:
    u = f"{BASE}/{PATH[league]}/scoreboard"
    return u + (f"?dates={date}" if date else "")


def summary_url(league: League, event_id: str) -> str:
    return f"{BASE}/{PATH[league]}/summary?event={event_id}"


def logo_url(league: League, abbr: str, team_id: str = "") -> str:
    if league.college:  # college logos are filed by ESPN team id (UNC = 153)
        return f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id or abbr.lower()}.png"
    return f"https://a.espncdn.com/i/teamlogos/{league.value}/500/{abbr.lower()}.png"


# --- safe access -------------------------------------------------------------

def _get(o, *path, default=None):
    for p in path:
        if isinstance(o, dict):
            o = o.get(p)
        elif isinstance(o, list) and isinstance(p, int):
            o = o[p] if -len(o) <= p < len(o) else None
        else:
            return default
        if o is None:
            return default
    return o


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _play_score(p: dict) -> tuple[int, int] | None:
    """(home, away) after this play, as the play-by-play logs it."""
    hs, as_ = _int(p.get("homeScore")), _int(p.get("awayScore"))
    return None if hs is None or as_ is None else (hs, as_)


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _clock_s(display: str | None) -> float | None:
    if not display or ":" not in display:
        try:
            return float(display) if display else None
        except ValueError:
            return None
    try:
        m, s = display.split(":", 1)
        return int(m) * 60 + float(s)
    except ValueError:
        return None


def _record(comp: dict) -> tuple[int | None, int | None, int | None]:
    recs = _get(comp, "records") or _get(comp, "record") or []
    summary = None
    for r in recs if isinstance(recs, list) else []:
        if r.get("type") in ("total", None) or r.get("name") == "overall":
            summary = r.get("summary")
            break
    if not summary:
        return None, None, None
    parts = [_int(x) for x in summary.split("-")]
    parts += [None] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


def _team(league: League, comp: dict) -> Team:
    t = comp.get("team") or {}
    abbr = t.get("abbreviation") or "?"
    w, l, ties = _record(comp)
    logo = t.get("logo") or _get(t, "logos", 0, "href") or logo_url(league, abbr, str(t.get("id") or ""))
    dark = t.get("logoDark") or next((x.get("href") for x in t.get("logos") or []
                                      if "dark" in (x.get("rel") or [])), "") or logo.replace("/500/", "/500-dark/")
    return Team(abbr=abbr, name=t.get("shortDisplayName") or t.get("name") or t.get("displayName") or abbr,
                score=_int(comp.get("score")), id=str(t.get("id") or ""), logo=logo,
                color=t.get("color") or "", wins=w, losses=l, ties=ties, logo_dark=dark,
                alt_color=t.get("alternateColor") or "", rank=_rank(comp))


def _rank(comp: dict) -> int | None:
    """College poll rank; ESPN sends 99 for unranked."""
    r = _int(_get(comp, "curatedRank", "current"))
    return r if r and 0 < r <= 25 else None


def _phase(state: str | None) -> Phase:
    return {"pre": Phase.PRE, "in": Phase.LIVE, "post": Phase.POST}.get(state or "", Phase.UNKNOWN)


def _broadcasts(comp: dict) -> tuple[list[str], bool]:
    names, national = [], False
    for b in comp.get("broadcasts") or []:
        for n in b.get("names") or []:
            if n not in names:
                names.append(n)
        if b.get("market") == "national":
            national = True
    for gb in comp.get("geoBroadcasts") or []:
        n = _get(gb, "media", "shortName")
        if n and n not in names:
            names.append(n)
        if _get(gb, "market", "type") == "National":
            national = True
    return names, national


# --- pictures: headshots, leaders, venues ----------------------------------------

def headshot_url(league: League, athlete_id) -> str:
    return f"https://a.espncdn.com/i/headshots/{IMG.get(league, league.value)}/players/full/{athlete_id}.png"


def venue_image(league: League, venue_id) -> str:
    if league.college:
        return ""  # no stock venue photos for college; the summary's own image is used when sent
    return f"https://a.espncdn.com/i/venues/{league.value}/day/{venue_id}.jpg" if venue_id else ""


def _leader(league: League, cat: dict, entry: dict, team_abbr: str) -> dict | None:
    a = entry.get("athlete") or {}
    if not a.get("id"):
        return None
    hs = a.get("headshot")
    hs = hs.get("href") if isinstance(hs, dict) else hs
    return {"label": cat.get("shortDisplayName") or cat.get("abbreviation") or cat.get("displayName") or "",
            "name": a.get("displayName") or a.get("fullName") or "", "short": a.get("shortName") or "",
            "headshot": hs or headshot_url(league, a["id"]), "stat": entry.get("displayValue") or "",
            "team": team_abbr, "id": str(a["id"])}


def _leaders_from(league: League, per_team: list[tuple[str, list]], limit: int = 3) -> list[dict]:
    """Top performers: each team's first category leader, then second categories, up to `limit`."""
    out: list[dict] = []
    for rank in range(3):
        for abbr, cats in per_team:
            cat = cats[rank] if isinstance(cats, list) and rank < len(cats) else None
            entry = _get(cat, "leaders", 0) if cat else None
            if entry:
                ld = _leader(league, cat, entry, abbr)
                if ld and all(x["id"] != ld["id"] for x in out):
                    out.append(ld)
            if len(out) >= limit:
                return out
    return out


def _venue(league: League, v: dict | None) -> dict:
    if not v:
        return {}
    img = _get(v, "images", 0, "href") or venue_image(league, v.get("id"))
    return {"id": str(v.get("id") or ""), "name": v.get("fullName") or "",
            "city": ", ".join(x for x in (_get(v, "address", "city"), _get(v, "address", "state")) if x),
            "image": img, "indoor": v.get("indoor")}


# --- scoreboard ----------------------------------------------------------------

def parse_scoreboard(league: League, data: dict) -> list[GameState]:
    out = []
    for ev in _get(data, "events") or []:
        try:
            g = _parse_event(league, ev)
        except Exception:  # one weird game never sinks the rest
            continue
        if g:
            out.append(g)
    return out


def _parse_event(league: League, ev: dict) -> GameState | None:
    comp = _get(ev, "competitions", 0) or {}
    comps = comp.get("competitors") or []
    home = next((c for c in comps if c.get("homeAway") == "home"), None)
    away = next((c for c in comps if c.get("homeAway") == "away"), None)
    if not home or not away or not ev.get("id"):
        return None
    status = comp.get("status") or ev.get("status") or {}
    g = GameState(league=league, game_id=str(ev["id"]), home=_team(league, home), away=_team(league, away))
    g.phase = _phase(_get(status, "type", "state"))
    g.period = _int(status.get("period")) or None
    disp = status.get("displayClock")
    g.clock_s = _clock_s(disp) if g.phase == Phase.LIVE and league != League.MLB else None
    g.status_text = _get(status, "type", "shortDetail") or _get(status, "type", "detail") or ""
    g.start_time = _ts(ev.get("date"))
    g.venue = _get(comp, "venue", "fullName") or ""
    g.venue_info = _venue(league, comp.get("venue"))
    g.leaders = _leaders_from(league, [(g.home.abbr, home.get("leaders") or []),
                                       (g.away.abbr, away.get("leaders") or [])])
    g.broadcasts, g.national_tv = _broadcasts(comp)
    odds = _get(comp, "odds", 0) or {}
    g.spread = _float(odds.get("spread"))
    if odds.get("details"):
        g.odds_text = odds["details"] + (f" · O/U {odds['overUnder']}" if odds.get("overUnder") else "")
    try:   # postseason / ranked stakes: data only, scored behind score.postseason.enabled
        g.stakes = postseason.parse(league, ev, g.home.id, g.away.id, g.home.rank, g.away.rank)
    except Exception:
        g.stakes = None
    detail = (_get(status, "type", "detail") or "").lower()
    brk = _status_break(league, g.period, detail)
    if brk is not None:
        g.stoppage = brk
    elif g.phase == Phase.LIVE:
        g.stoppage = Stoppage.UNKNOWN

    sit = comp.get("situation") or {}
    if league.base == League.NFL and sit:
        poss_id = str(sit.get("possession") or "")
        g.situation.possession = (g.home.abbr if poss_id == g.home.id else
                                  g.away.abbr if poss_id == g.away.id else None)
        g.situation.down = _int(sit.get("down")) if (_int(sit.get("down")) or 0) > 0 else None
        g.situation.distance = _int(sit.get("distance"))
        ytg = _int(sit.get("yardsToEndzone"))
        g.situation.yards_to_goal = ytg if ytg and ytg > 0 else None
        if g.situation.distance is not None and g.situation.yards_to_goal is not None:
            g.situation.goal_to_go = g.situation.distance >= g.situation.yards_to_goal
        g.situation.timeouts_home = _int(sit.get("homeTimeouts"))
        g.situation.timeouts_away = _int(sit.get("awayTimeouts"))
        lp = sit.get("lastPlay") or {}
        if lp.get("id"):
            g.last_play = Play(str(lp["id"]), lp.get("text") or "", _kind(league, lp), bool(lp.get("scoringPlay")))
        wp = _float(_get(sit, "lastPlay", "probability", "homeWinPercentage"))
        if wp is not None:
            g.home_win_prob = wp
    if league == League.MLB and sit:
        g.situation.outs = _int(sit.get("outs"))
        g.situation.runners = (bool(sit.get("onFirst")), bool(sit.get("onSecond")), bool(sit.get("onThird")))
        if g.status_text.lower().startswith("bot"):
            g.half = "bottom"
        elif g.status_text.lower().startswith("top"):
            g.half = "top"
    return g


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- summary / play-by-play ---------------------------------------------------

def flatten_plays(league: League, data: dict) -> list[dict]:
    if league.base == League.NFL:
        plays = []
        for dr in _get(data, "drives", "previous") or []:
            plays.extend(dr.get("plays") or [])
        cur = _get(data, "drives", "current")
        if cur:
            seen = {p.get("id") for p in plays}
            plays.extend(p for p in cur.get("plays") or [] if p.get("id") not in seen)
        plays.sort(key=lambda p: _int(p.get("sequenceNumber")) or 0)
        return plays
    return list(_get(data, "plays") or [])


_SCORE_KINDS = ("touchdown", "field goal good", "safety")


def _kind(league: League, p: dict) -> str:
    t = (_get(p, "type", "text") or "").lower()
    if league == League.MLB and (_get(p, "alternativeType", "type") == "lineup-change"
                                or (_get(p, "alternativeType", "text") or "").lower() == "lineup change"):
        # ESPN wraps between-innings fielding substitutions in "Play Result".
        # They are bookkeeping, not a pitch that can be cut off mid-flight.
        return "lineup_change"
    if league.college and league.base == League.NBA and "freethrow" in t.replace(" ", ""):
        m = re.search(r"(\d) of (\d)", p.get("text") or "")  # NBA-style kind, so dead-ball rules match
        return f"free_throw_-_{m.group(1)}_of_{m.group(2)}" if m else "free_throw"
    if league.base == League.NBA and "team rebound" in (p.get("text") or "").lower():
        return "team_rebound"  # ball out of bounds / between free throws: a dead ball, not live play
    if league.base == League.NFL:
        if "touchdown" in t:
            return "td"
        if "field goal good" in t:
            return "fg"
        if "interception" in t or "fumble recovery (opponent)" in t or p.get("isTurnover"):
            return "turnover"
        if "sack" in t:
            return "sack"
    if league == League.NHL and t == "goal":
        return "goal"
    if league == League.MLB and "home run" in t:
        return "hr"
    if league.base == League.NBA and p.get("scoringPlay") and (_int(p.get("scoreValue")) or 0) == 3:
        return "three"
    return t.replace(" ", "_")[:30]


# [v3 autopsy, Nov 6] ESPN's NHL penalty rows: 2025-26 summaries type every one "Penalty" (no Stoppage row before it in
# ~9 of 10), 2026-27 types the infraction ("Tripping", "Cross checking", "High sticking", "Delaying Game - Puck over
# Glass", "Too Many Men on the Ice"...). Every one is a whistle.
_NHL_PENALTIES = ("penalty", "hooking", "holding", "tripping", "roughing", "interference", "slashing", "high sticking",
                  "fighting", "unsportsmanlike conduct", "cross checking", "delay of game", "delaying game",
                  "too many men", "boarding", "elbowing", "kneeing", "charging", "misconduct", "instigator",
                  "embellishment", "spearing", "butt ending", "clipping", "abuse of official", "head butting",
                  "checking from behind", "illegal check", "game misconduct", "match penalty", "goalkeeper interference",
                  "holding the stick", "closing hand on puck", "throwing equipment", "face off violation", "delay game",
                  "removing opponent", "broken stick", "leaving crease", "aggressor", "throwing object",
                  "thowing object")


def _nhl_penalty(t: str) -> bool:
    t = re.sub(r"[-_]+", " ", t)
    if t.startswith("penalty shot"):   # integrator, Nov 6: a penalty shot is live play and a big moment, never a whistle
        return False
    return any(t == k or t.startswith(k + " ") for k in _NHL_PENALTIES) or "penalty" in t


def stoppage_for(league: League, p: dict) -> Stoppage:
    """What the game is doing right AFTER this play."""
    t = (_get(p, "type", "text") or "").lower()
    text = (p.get("text") or "").lower()
    if league.base == League.NFL:
        if t in ("timeout", "official timeout"):
            return Stoppage.TIMEOUT
        if t.startswith("two-minute warning"):
            return Stoppage.TWO_MINUTE_WARNING
        if t == "end of half":
            return Stoppage.HALFTIME
        if t == "end period" and league.college and (_int(_get(p, "period", "number")) or 0) == 2:
            return Stoppage.HALFTIME  # college logs halftime as "End Period: End of 2nd quarter."
        if t in ("end period", "end of regulation"):
            return Stoppage.END_PERIOD
        if p.get("scoringPlay") or any(k in t for k in _SCORE_KINDS):
            return Stoppage.SCORE_BREAK
        if any(k in text for k in ("challenged", "review", "replay official", "reversed", "upheld")):
            return Stoppage.REVIEW
        if "injur" in text:
            return Stoppage.INJURY
        return Stoppage.NONE
    if league.base == League.NBA:
        if "timeout" in t:
            return Stoppage.TIMEOUT
        if t in ("end period", "end game", "end of game"):
            per = _int(_get(p, "period", "number")) or 0
            half = REGULATION[league] // 2  # men's college plays halves: halftime ends period 1
            return Stoppage.HALFTIME if per == half and t == "end period" else Stoppage.END_PERIOD
        if "challenge" in t or "review" in t:
            return Stoppage.REVIEW
        if "team rebound" in (p.get("text") or "").lower():
            return Stoppage.WHISTLE
        if "free throw" in t or "freethrow" in t:
            # The ball is dead between free throws; after the LAST one play resumes
            # (made: quick inbound; missed: live rebound), so that one ends the whistle.
            # College types are bare ("MadeFreeThrow"): the "1 of 2" is in the play text.
            ft = t if re.search(r"\d of \d", t) else f"{t} {text}"
            return Stoppage.NONE if _last_free_throw(ft) else Stoppage.WHISTLE
        if ("foul" in t or "violation" in t or "out of bounds" in t or "substitution" in t
                or "kicked ball" in t or "delay" in t or "technical" in t or "flagrant" in t):
            return Stoppage.WHISTLE
        return Stoppage.NONE
    if league == League.NHL:
        if t == "stoppage":
            if "tv timeout" in text:
                return Stoppage.TIMEOUT
            if "challenge" in text or "review" in text:
                return Stoppage.REVIEW
            return Stoppage.WHISTLE
        if t in ("period end", "end of game", "game end"):
            per = _int(_get(p, "period", "number")) or 0
            return Stoppage.INTERMISSION if t == "period end" and per in (1, 2) else Stoppage.END_PERIOD
        if t == "goal":
            return Stoppage.SCORE_BREAK
        if _nhl_penalty(t):
            return Stoppage.WHISTLE
        return Stoppage.NONE
    if league == League.MLB:
        if "end inning" in t:
            return Stoppage.END_PERIOD
        if "pitching change" in t or "pitcher change" in text:
            return Stoppage.PITCHING_CHANGE
        if "end batter" in t:
            return Stoppage.WHISTLE  # between plate appearances
        return Stoppage.NONE
    return Stoppage.NONE


NHL_PLAYOFF_OT_S = PERIOD_S[League.NHL]   # a playoff OT is a full 20:00 period, played until someone scores (no shootout)


def _nhl_playoffs(data: dict) -> bool:
    """ESPN's summary marks the season type: 3 = postseason (2 = regular season)."""
    return _int(_get(data, "header", "season", "type")) == 3


def _play_clock(league: League, p: dict, playoff: bool = False) -> dict:
    """[cut timing] Clock (seconds LEFT in the period), period and raw type of one entry."""
    per = _int(_get(p, "period", "number"))
    clock = _clock_s(_get(p, "clock", "displayValue"))
    if league == League.NHL and clock is not None:
        length = (NHL_PLAYOFF_OT_S if playoff else OT_S[league]) if (per or 0) >= 4 else PERIOD_S[league]
        clock = max(0.0, length - clock)  # ESPN NHL play clocks count up
    return {"clock": clock if league != League.MLB else None, "period": per,
            "type_text": _get(p, "type", "text") or ""}


def _last_free_throw(t: str) -> bool:
    """'free throw - 2 of 2' is the last one; technicals/flagrants keep the ball dead (inbound after)."""
    if "technical" in t or "flagrant" in t:
        return False
    m = re.search(r"(\d) of (\d)", t)
    return bool(m) and m.group(1) == m.group(2)


def parse_summary(league: League, data: dict, upto: int | None = None,
                  plays: list[dict] | None = None) -> GameState | None:
    """Game state from a summary. `upto` = index of the last play to include (backtesting)."""
    comp = _get(data, "header", "competitions", 0) or {}
    comps = comp.get("competitors") or []
    home = next((c for c in comps if c.get("homeAway") == "home"), None)
    away = next((c for c in comps if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    g = GameState(league=league, game_id=str(comp.get("id") or _get(data, "header", "id") or ""),
                  home=_team(league, home), away=_team(league, away))
    ids = {g.home.id: g.home.abbr, g.away.id: g.away.abbr}
    g.broadcasts, g.national_tv = _broadcasts(comp)
    pc = _get(data, "pickcenter", 0) or {}
    g.spread = _float(pc.get("spread"))
    if pc.get("details"):
        g.odds_text = pc["details"] + (f" · O/U {pc['overUnder']}" if pc.get("overUnder") else "")
    g.start_time = _ts(comp.get("date"))
    plays = plays if plays is not None else flatten_plays(league, data)
    status = comp.get("status") or {}
    live_phase = _phase(_get(status, "type", "state"))

    if upto is None:
        g.phase = live_phase
        g.status_text = _get(status, "type", "shortDetail") or ""
        cut = plays
    else:
        cut = plays[: upto + 1]
        last_t = (_get(cut[-1], "type", "text") or "").lower() if cut else ""
        # Baseball often has no "end of game" entry (e.g. home team leads after the top of the 9th),
        # so the last play of a game the header says is over also counts as the end.
        over = last_t in ("end of game", "end game") or (
            upto >= len(plays) - 1 and _get(status, "type", "state") == "post")
        g.phase = Phase.POST if over else Phase.LIVE if cut else Phase.PRE
    if not cut:
        if upto is None and live_phase == Phase.LIVE:
            g.period = _int(status.get("period"))
        return g

    p = cut[-1]
    g.period = _int(_get(p, "period", "number"))
    g.home.score = _int(p.get("homeScore"))
    g.away.score = _int(p.get("awayScore"))
    if upto is not None:
        # [grader v47, F14] The summary's records are as of capture, so for a finished game they already hold
        # this game's result (a leak into Scorer._stakes). We can't tell which captures include it, so a
        # backtest knows no record: stakes read neutral rather than hindsight.
        for t in (g.home, g.away):
            t.wins = t.losses = t.ties = None
    playoff = _nhl_playoffs(data)   # [grader v47, F7] playoff OT is 20:00, not 5:00, and has no shootout
    clock = _clock_s(_get(p, "clock", "displayValue"))
    if league == League.NHL and clock is not None:
        length = (NHL_PLAYOFF_OT_S if playoff else OT_S[league]) if (g.period or 0) >= 4 else PERIOD_S[league]
        clock = max(0.0, length - clock)  # ESPN NHL play clocks count up
    g.clock_s = clock if league != League.MLB else None
    g.stoppage = stoppage_for(league, p)
    if league == League.NHL and (g.period or 0) >= 5 and not playoff:
        g.period_type = "SO"
    elif league.base in (League.NHL, League.NBA, League.NFL) and (g.period or 0) > REGULATION[league]:
        g.period_type = "OT"

    seen = _ts(p.get("wallclock"))
    g.last_play = Play(str(p.get("id") or len(cut)), p.get("text") or _get(p, "type", "text") or "",
                       _kind(league, p), bool(p.get("scoringPlay")), seen, seen, "espn", score=_play_score(p),
                       **_play_clock(league, p, playoff))
    g.recent_plays = [Play(str(x.get("id")), x.get("text") or "", _kind(league, x), bool(x.get("scoringPlay")),
                           _ts(x.get("wallclock")), score=_play_score(x), **_play_clock(league, x, playoff))
                      for x in cut[-8:]]

    if league.base == League.NFL:
        _nfl_situation(g, p, ids)
    elif league == League.MLB:
        _mlb_situation(g, p, cut)
    elif league == League.NHL:
        _nhl_situation(g, cut, ids)

    wp_by_play = {w.get("playId"): w.get("homeWinPercentage") for w in data.get("winprobability") or []}
    for x in reversed(cut[-20:]):
        v = _float(wp_by_play.get(x.get("id")))
        if v is not None:
            g.home_win_prob = v
            break
    if upto is None:
        _live_overrides(g, data, comp, ids)
    g.venue_info = _venue(league, _get(data, "gameInfo", "venue"))
    g.venue = g.venue_info.get("name", "")
    if upto is None:
        # The summary's leader board is as of now; replayed at play `upto` it is the FINAL board
        # (a leak: research 05 §3.1). A replay has no leaders until it reaches the end of the game.
        by_team = {str(_get(x, "team", "id") or ""): x.get("leaders") or [] for x in data.get("leaders") or []}
        g.leaders = _leaders_from(league, [(g.home.abbr, by_team.get(g.home.id, [])),
                                           (g.away.abbr, by_team.get(g.away.id, []))])
    g.spotlight = spotlight(league, data, cut, ids)
    return g


def athletes(league: League, data: dict) -> dict[str, dict]:
    """Every player in the box score: id -> {name, short, headshot, team}."""
    out = {}
    for side in _get(data, "boxscore", "players") or []:
        abbr = _get(side, "team", "abbreviation") or ""
        for stat in side.get("statistics") or []:
            for a in stat.get("athletes") or []:
                ath = a.get("athlete") or {}
                if ath.get("id") and ath["id"] not in out:
                    hs = ath.get("headshot")
                    out[str(ath["id"])] = {"name": ath.get("displayName") or "", "short": ath.get("shortName") or "",
                                           "headshot": (hs.get("href") if isinstance(hs, dict) else hs)
                                           or headshot_url(league, ath["id"]), "team": abbr}
    return out


def play_athlete(league: League, p: dict, roster: dict[str, dict]) -> dict | None:
    """Who made this play? Uses participants when the feed has them, else names in the text."""
    for part in p.get("participants") or []:
        aid = str(_get(part, "athlete", "id") or "")
        if aid in roster:
            return roster[aid]
    text = p.get("text") or ""
    if not text or not roster:
        return None
    squashed = text.replace(" ", "")
    hits = []
    for a in roster.values():
        key = (a.get("short") or "").replace(" ", "") or _pbp_name(a.get("name") or "")
        if len(key) > 3:
            i = squashed.find(key)
            if i >= 0:
                hits.append((i, a))
    if not hits:
        return None
    hits.sort(key=lambda x: x[0])
    up = squashed.upper()
    if "INTERCEPTEDBY" in up:
        cut = up.index("INTERCEPTEDBY")
        after = [a for i, a in hits if i > cut]
        return after[0] if after else hits[-1][1]
    if "TOUCHDOWN" in up:
        cut = up.index("TOUCHDOWN")
        before = [a for i, a in hits if i < cut]
        return before[-1] if before else hits[0][1]
    return hits[0][1]


_SUFFIXES = {"jr.", "jr", "sr.", "sr", "ii", "iii", "iv", "v"}


def _pbp_name(full: str) -> str:
    """'Bo Nix' -> 'B.Nix', 'Marvin Harrison Jr.' -> 'M.Harrison' (how NFL play text names players)."""
    parts = [x for x in full.split() if x.lower() not in _SUFFIXES]
    if len(parts) < 2:
        return ""
    return f"{parts[0][0]}.{''.join(parts[1:])}"


def spotlight(league: League, data: dict, cut: list[dict], ids: dict) -> dict | None:
    """The player behind the latest big play (score or turnover) in the last 15 plays."""
    roster = athletes(league, data)
    for p in reversed(cut[-15:]):
        t = (_get(p, "type", "text") or "").lower()
        big = p.get("scoringPlay") or "interception" in t or "fumble recovery (opponent)" in t or t == "goal" \
            or "home run" in t
        if league.base == League.NBA:
            big = bool(p.get("scoringPlay")) and (_int(p.get("scoreValue")) or 0) >= 3
        if not big:
            continue
        a = play_athlete(league, p, roster)
        if a:
            return {**a, "text": (p.get("text") or "")[:140], "play_id": str(p.get("id") or "")}
    return None


def _nfl_situation(g: GameState, p: dict, ids: dict) -> None:
    end = p.get("end") or {}
    team = ids.get(str(_get(end, "team", "id") or ""))
    down = _int(end.get("down"))
    ytg = _int(end.get("yardsToEndzone"))
    s = g.situation
    if team and down and down > 0 and ytg and ytg > 0:
        s.possession, s.down, s.distance, s.yards_to_goal = team, down, _int(end.get("distance")), ytg
        s.goal_to_go = s.distance is not None and s.distance >= ytg
    elif team:
        s.possession = team


# ESPN NHL event types that are not penalties (everything else with a team is an infraction).
_NHL_EVENT_TYPES = {"502", "503", "505", "506", "507", "508", "516", "518", "519", "520", "521", "522",
                    "1401", "1402"}
# Infractions that don't put anyone on the power play (offsetting by nature, or a penalty shot).
_NHL_NO_PP = ("misconduct", "fighting", "instigator", "ps-")
NHL_PP_SCAN = 60   # plays to look back: a minor is 2:00 of game time, well under 60 logged events


def nhl_power_play(cut: list[dict], ids: dict, home: str, away: str) -> tuple[str | None, str | None]:
    """[nhl pp] (team on the power play, strength) after the last play in `cut`, from ESPN's own
    play-by-play (the NHL feed's situationCode isn't always there, and backtests never have it).
    `ids` maps ESPN team id -> abbreviation.

    Each ESPN play carries the strength of the team that made it ("power-play" / "short-handed" /
    "even-strength"), so the newest such label says who is up a man. The label lags the call:
    the penalty itself and the whistle after it are still "even-strength", and only the faceoff
    that restarts play says "power-play". So a penalty logged after the newest label starts the
    power play right away for the other team; one against each team at once cancels out."""
    pp = None
    pen: dict[str, int] = {}   # team -> penalties called on them since the newest strength label
    for x in cut[-NHL_PP_SCAN:]:
        team = ids.get(str(_get(x, "team", "id") or ""))
        typ = str(_get(x, "type", "id") or "")
        if team and typ and typ not in _NHL_EVENT_TYPES:
            if not any(k in (_get(x, "type", "text") or "").lower() for k in _NHL_NO_PP):
                pen[team] = pen.get(team, 0) + 1
            continue
        strength = _get(x, "strength", "abbreviation")
        if team and strength:
            other = away if team == home else home
            pp = team if strength == "power-play" else other if strength == "short-handed" else None
            if typ == "505" and strength == "power-play":
                pp = None   # a power-play goal ends a minor
            pen = {}
    net = pen.get(away, 0) - pen.get(home, 0)   # > 0: the away team is down a man since the last label
    strength = "5v4"
    if net:
        up = home if net > 0 else away
        if pp is None:
            pp, strength = up, "5v3" if abs(net) >= 2 else "5v4"
        elif pp == up:
            strength = "5v3"   # the short-handed team took another one
        else:
            pp = None          # the team on the power play took one: 4-on-4
    return (pp, strength) if pp else (None, None)


def _nhl_situation(g: GameState, cut: list[dict], ids: dict) -> None:
    pp, strength = nhl_power_play(cut, ids, g.home.abbr, g.away.abbr)
    if pp:
        g.situation.power_play, g.situation.strength = pp, strength


def _mlb_situation(g: GameState, p: dict, cut: list[dict]) -> None:
    half = (_get(p, "period", "type") or "").lower()
    g.half = "bottom" if half.startswith("bot") else "top" if half.startswith("top") else None
    s = g.situation
    s.outs = _int(p.get("outs"))
    roles = {x.get("type") for x in p.get("participants") or []}
    s.runners = ("onFirst" in roles, "onSecond" in roles, "onThird" in roles)
    if (_get(p, "type", "text") or "").lower() == "end inning":
        s.runners, s.outs = (False, False, False), 0


def _live_overrides(g: GameState, data: dict, comp: dict, ids: dict) -> None:
    """Live summaries carry a richer `situation` and current possession; prefer them."""
    sit = data.get("situation") or {}
    s = g.situation
    if g.league.base == League.NFL and sit:
        down = _int(sit.get("down"))
        ytg = _int(sit.get("yardsToEndzone") or (100 - _int(sit.get("yardLine")) if _int(sit.get("yardLine")) else None))
        if down and down > 0:
            s.down, s.distance = down, _int(sit.get("distance"))
            if ytg and ytg > 0:
                s.yards_to_goal = ytg
            s.goal_to_go = s.distance is not None and s.yards_to_goal is not None and s.distance >= s.yards_to_goal
        s.timeouts_home = _int(sit.get("homeTimeouts"))
        s.timeouts_away = _int(sit.get("awayTimeouts"))
    for c in comp.get("competitors") or []:
        if c.get("possession") is True:
            s.possession = ids.get(str(_get(c, "team", "id") or ""), s.possession)
    status = comp.get("status") or {}
    clock = _clock_s(status.get("displayClock"))
    if clock is not None and g.league != League.MLB and g.phase == Phase.LIVE:
        g.clock_s = clock
    detail = (_get(status, "type", "detail") or "").lower()
    brk = _status_break(g.league, g.period, detail, allow_bare_end=False)
    # Never downgrade a long break the play-by-play already proved (halftime, NHL intermission).
    if brk is not None and g.stoppage not in (Stoppage.HALFTIME, Stoppage.INTERMISSION):
        g.stoppage = brk


def _status_break(league: League, period: int | None, detail: str,
                  allow_bare_end: bool = True) -> Stoppage | None:
    """The break a status detail ('Halftime', 'End of 1st', '2nd Intermission') describes.
    NHL ends of the 1st and 2nd are the ~18-minute intermissions, not a short period break."""
    if "halftime" in detail:
        return Stoppage.HALFTIME
    if "end of" in detail or "intermission" in detail or (allow_bare_end and detail.startswith("end ")):
        if league == League.NHL and (period or 0) in (1, 2):
            return Stoppage.INTERMISSION
        return Stoppage.END_PERIOD
    return None

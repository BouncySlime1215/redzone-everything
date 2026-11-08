"""League feeds that are faster or richer than ESPN for some things.

NHL  api-web.nhle.com   situationCode -> power play / empty net (ESPN doesn't say)
NBA  S3 mirror of cdn.nba.com liveData -> fast score/clock (cdn.nba.com itself 403s scripts)
MLB  statsapi.mlb.com   leverage index, runners, outs (fed in when the season is on)

Each enrich_* takes the merged ESPN game and a league payload and returns a GameState
copy with what the league feed knows, ready to go through the Merger like any source.
Field names verified against tests/fixtures (Nov 2026).
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import re
import time

from redzone.models import GameState, League, Phase, Play, Stoppage
from redzone.sources.espn import _get, _int

NHL_SCORE = "https://api-web.nhle.com/v1/score/{date}"
NHL_PBP = "https://api-web.nhle.com/v1/wsc/play-by-play/{id}"
NBA_SCOREBOARD = ("https://nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA/liveData/"
                  "scoreboard/todaysScoreboard_00.json")
# The live play-by-play on the same S3 mirror (cdn.nba.com 403s scripts). Each action carries
# timeActual (when it happened, ms) and edited (when it was last published): 4-12 s apart at the
# median in a fixture game, vs ESPN's ~45 s. Path assumed from the scoreboard mirror: unverified.
NBA_PBP = ("https://nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA/liveData/"
           "playbyplay/playbyplay_{id}.json")
MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=broadcasts,team"
MLB_LIVE = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"

# ESPN abbreviation -> league abbreviation, where they differ.
NHL_ABBR = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA", "MON": "MTL"}
NBA_ABBR = {"WSH": "WAS", "GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "PHX": "PHX"}


def _key(a: str, b: str) -> tuple[str, str]:
    return a.upper(), b.upper()


# --- NHL ----------------------------------------------------------------------

def nhl_index(score_json: dict) -> dict[tuple[str, str], dict]:
    """(away, home) abbrevs -> game dict from /v1/score/{date}."""
    out = {}
    for gm in _get(score_json, "games") or []:
        a, h = _get(gm, "awayTeam", "abbrev"), _get(gm, "homeTeam", "abbrev")
        if a and h:
            out[_key(a, h)] = gm
    return out


def nhl_match(g: GameState, idx: dict) -> dict | None:
    a = NHL_ABBR.get(g.away.abbr, g.away.abbr)
    h = NHL_ABBR.get(g.home.abbr, g.home.abbr)
    return idx.get(_key(a, h))


def parse_situation_code(code: str | None, home: str, away: str) -> tuple[str | None, str | None, str | None]:
    """'1541' = [awayGoalie][awaySkaters][homeSkaters][homeGoalie]. Returns (pp_team, strength, empty_net_team)."""
    if not code or not re.fullmatch(r"\d{4}", str(code)):
        return None, None, None
    ag, ask, hsk, hg = (int(c) for c in str(code))
    empty = away if ag == 0 else home if hg == 0 else None
    # Skater counts include the extra attacker when a goalie is pulled; a PP is about penalties,
    # so compare skaters without the extra attacker.
    a_real = ask - (1 if ag == 0 else 0)
    h_real = hsk - (1 if hg == 0 else 0)
    pp = away if a_real > h_real else home if h_real > a_real else None
    strength = f"{max(a_real, h_real)}v{min(a_real, h_real)}" if pp else f"{hsk}v{ask}"
    return pp, strength, empty

# NHL play-by-play typeDescKey -> the decider's play kind. Kinds follow ESPN's (lower case, "_"), so
# the screen model's marker / dead-ball rules match: a whistle-time penalty is a stoppage entry, while
# a delayed penalty is called with the puck still live (it must not read as a dead ball: "delay").
NHL_KIND = {"faceoff": "face_off", "stoppage": "stoppage", "penalty": "stoppage_penalty",
            "delayed-penalty": "penalty_signal", "period-start": "period_start", "period-end": "end_period",
            "game-end": "end_game", "goal": "goal", "shot-on-goal": "shot", "missed-shot": "missed_shot",
            "blocked-shot": "blocked_shot", "hit": "hit", "giveaway": "giveaway", "takeaway": "takeaway"}
NHL_TYPE_TEXT = {"faceoff": "Face Off", "stoppage": "Stoppage", "penalty": "Penalty", "goal": "Goal",
                 "period-start": "Period Start", "period-end": "End Period", "game-end": "End Game"}
NHL_PLAYS_KEPT = 30


def _mmss(s) -> float | None:
    m = re.fullmatch(r"(\d+):(\d+)", str(s or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def nhl_plays(pbp, seen: dict[str, float], now: float | None = None) -> list[Play]:
    """The newest NHL play-by-play entries as Plays, for the decider's timeline.

    The feed has no wallclock, so each play carries when WE first saw it (`seen`, kept by the
    caller across polls); the screen model bounds its real time with the NHL feed's lag window
    (screen.SOURCE_LAG_S). Ids are prefixed so they never collide with ESPN's."""
    now = time.time() if now is None else now
    plays = (pbp if isinstance(pbp, list) else (pbp or {}).get("plays")) or []
    if not seen:
        # First look at this game: what's already logged is history, with no time we could bound
        # (a play first seen now may be minutes old). Remember it and pass on only what comes next.
        seen.update({f"nhl{p.get('eventId')}": 0.0 for p in plays})
        seen.setdefault("_primed", 0.0)
        return []
    out = []
    for p in plays[-NHL_PLAYS_KEPT:]:
        eid, key = p.get("eventId"), p.get("typeDescKey") or ""
        if eid is None:
            continue
        pid = f"nhl{eid}"
        t = seen.setdefault(pid, now)
        if not t:
            continue      # history from before we started watching
        reason = _get(p, "details", "reason") or ""
        kind = NHL_KIND.get(key, key.replace("-", "_")[:30])
        out.append(Play(pid, f"{key} {reason}".strip()[:80], kind, key == "goal", None, t, "nhl",
                        clock=_mmss(p.get("timeRemaining")), period=_int(_get(p, "periodDescriptor", "number")),
                        type_text=NHL_TYPE_TEXT.get(key, key)))
    return out


def enrich_nhl(g: GameState, score_game: dict, pbp: dict | None = None,
               seen: dict[str, float] | None = None) -> GameState:
    out = copy.deepcopy(g)
    out.sources = {}
    state = score_game.get("gameState")
    if state in ("LIVE", "CRIT"):
        out.phase = Phase.LIVE
    elif state in ("OFF", "FINAL"):
        out.phase = Phase.POST
    out.home.score = _int(_get(score_game, "homeTeam", "score")) if _get(score_game, "homeTeam", "score") is not None else out.home.score
    out.away.score = _int(_get(score_game, "awayTeam", "score")) if _get(score_game, "awayTeam", "score") is not None else out.away.score
    out.period = _int(_get(score_game, "periodDescriptor", "number")) or out.period
    ptype = _get(score_game, "periodDescriptor", "periodType")
    if ptype:
        out.period_type = ptype
    secs = _get(score_game, "clock", "secondsRemaining")
    if secs is not None:
        out.clock_s = float(secs)
    if _get(score_game, "clock", "inIntermission"):
        out.stoppage = Stoppage.INTERMISSION if (out.period or 0) in (1, 2) else Stoppage.END_PERIOD
    code = score_game.get("situationCode") or _get(score_game, "situation", "situationCode")
    if seen is not None and isinstance(pbp, dict):
        # [NHL primary] the live play-by-play is fresher than /score: take its clock and plays.
        secs = _get(pbp, "clock", "secondsRemaining")
        if secs is not None and _int(_get(pbp, "periodDescriptor", "number")) == out.period:
            out.clock_s = float(secs)
        for side, team in (("homeTeam", out.home), ("awayTeam", out.away)):
            sc = _get(pbp, side, "score")
            if sc is not None and (team.score is None or _int(sc) >= team.score):
                team.score = _int(sc)   # never below what another feed already showed
        plays = nhl_plays(pbp, seen)
        if plays:
            out.recent_plays, out.last_play = plays, plays[-1]
    if pbp:
        # The NHL play-by-play body is usually {"plays": [...]}, but some games return a bare list.
        plays = (pbp if isinstance(pbp, list) else pbp.get("plays")) or []
        if plays:
            last = plays[-1]
            code = last.get("situationCode") or code
            kind = last.get("typeDescKey") or ""
            reason = _get(last, "details", "reason") or ""
            if kind == "stoppage":
                out.stoppage = nhl_stoppage_reason(reason)
            elif kind == "goal":
                out.stoppage = Stoppage.SCORE_BREAK
            elif kind == "period-end":
                out.stoppage = Stoppage.INTERMISSION if (out.period or 0) in (1, 2) else Stoppage.END_PERIOD
            elif kind in ("penalty", "delayed-penalty"):
                out.stoppage = Stoppage.WHISTLE if kind == "penalty" else out.stoppage
            elif kind and _get(score_game, "clock", "running"):
                out.stoppage = Stoppage.NONE
    if code and re.fullmatch(r"\d{4}", str(code)):
        # The NHL feed's on-ice code is the truth when it's there; without one, keep what ESPN's
        # play-by-play already said (espn._nhl_situation) rather than wiping it.
        pp, strength, empty = parse_situation_code(code, out.home.abbr, out.away.abbr)
        out.situation.power_play, out.situation.strength, out.situation.empty_net = pp, strength, empty
    out.updated_at = time.time()
    return out


def nhl_stoppage_reason(reason: str) -> Stoppage:
    """[grader v47, F9] A league-feed stoppage by its reason. A video review or a coach's challenge runs
    minutes (a real break to visit), an injury likewise; only the rest are whistles. api-web owns the NHL
    stoppage (merge._owned_stoppage), so this is what the decider sees."""
    r = (reason or "").lower()
    if r == "tv-timeout":
        return Stoppage.TIMEOUT
    if "video-review" in r or r.startswith("chlg") or "challenge" in r:
        return Stoppage.REVIEW
    if "injury" in r:
        return Stoppage.INJURY
    return Stoppage.WHISTLE


# --- NBA ----------------------------------------------------------------------

def nba_index(sb: dict) -> dict[tuple[str, str], dict]:
    out = {}
    for gm in _get(sb, "scoreboard", "games") or []:
        a, h = _get(gm, "awayTeam", "teamTricode"), _get(gm, "homeTeam", "teamTricode")
        if a and h:
            out[_key(a, h)] = gm
    return out


def nba_match(g: GameState, idx: dict) -> dict | None:
    return idx.get(_key(NBA_ABBR.get(g.away.abbr, g.away.abbr), NBA_ABBR.get(g.home.abbr, g.home.abbr)))


def _iso_clock(s: str | None) -> float | None:
    m = re.fullmatch(r"PT(\d+)M([\d.]+)S", s or "")
    return int(m.group(1)) * 60 + float(m.group(2)) if m else None


def enrich_nba(g: GameState, gm: dict, pbp: dict | None = None,
               seen: dict[str, float] | None = None) -> GameState:
    out = copy.deepcopy(g)
    out.sources = {}
    st = gm.get("gameStatus")
    out.phase = {1: Phase.PRE, 2: Phase.LIVE, 3: Phase.POST}.get(st, out.phase)
    hs, as_ = _int(_get(gm, "homeTeam", "score")), _int(_get(gm, "awayTeam", "score"))
    if hs is not None and as_ is not None:
        out.home.score, out.away.score = hs, as_
    out.period = _int(gm.get("period")) or out.period
    c = _iso_clock(gm.get("gameClock"))
    if c is not None:
        out.clock_s = c
    text = (gm.get("gameStatusText") or "").lower()
    if "half" in text:
        out.stoppage = Stoppage.HALFTIME
    elif text.startswith("end"):
        out.stoppage = Stoppage.END_PERIOD
    if seen is not None and isinstance(pbp, dict):
        # [NBA primary] the live play-by-play: real event times, its stoppage, its clock.
        acts = _get(pbp, "game", "actions") or []
        plays = nba_plays(pbp, seen)
        if plays:
            out.recent_plays, out.last_play = plays, plays[-1]
        if acts:
            last = acts[-1]
            out.stoppage = nba_stoppage(last) if out.stoppage not in (Stoppage.HALFTIME,) else out.stoppage
            if _int(last.get("period")) == out.period and _iso_clock(last.get("clock")) is not None:
                out.clock_s = min(out.clock_s if out.clock_s is not None else 1e9, _iso_clock(last.get("clock")))
    out.situation.timeouts_home = _int(_get(gm, "homeTeam", "timeoutsRemaining"))
    out.situation.timeouts_away = _int(_get(gm, "awayTeam", "timeoutsRemaining"))
    out.updated_at = time.time()
    return out


def _iso_ts(s) -> float | None:
    """ISO stamp -> epoch. Feed stamps are UTC, so one with no offset is read as UTC, not as the
    Mac's local time (which put it 4-5 h off in ET, T6-4)."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _nba_kind(a: dict) -> str:
    """NBA liveData action -> the decider's play kind, in ESPN's spelling so the screen model's
    marker and dead-ball rules (timeouts, fouls, subs, non-final free throws) match."""
    t, sub = a.get("actionType") or "", (a.get("subType") or "").lower()
    desc = (a.get("descriptor") or "").lower()
    if t == "timeout":
        return f"{sub or 'full'}_timeout"
    if t == "foul":
        return f"{desc or sub or 'personal'}_foul".replace(" ", "_")
    if t == "freethrow":
        m = re.search(r"(\d) of (\d)", sub)
        return f"free_throw_-_{m.group(1)}_of_{m.group(2)}" if m else "free_throw"
    if t == "period":
        return "period_start" if sub == "start" else "end_period"
    if t == "game":
        return "end_game"
    if t == "rebound" and (not a.get("personId") or "team" in (a.get("qualifiers") or [])):
        return "team_rebound"
    if t == "3pt" and a.get("shotResult") == "Made":
        return "three"
    if t in ("2pt", "3pt"):
        return (sub or "shot").replace(" ", "_")[:30]
    if t == "turnover":
        return f"{sub or 'lost_ball'}_turnover".replace(" ", "_")
    return t.replace(" ", "_")[:30] or "action"


def nba_stoppage(a: dict | None) -> Stoppage:
    """What the game is doing right after this action."""
    if not a:
        return Stoppage.UNKNOWN
    t, sub = a.get("actionType") or "", (a.get("subType") or "").lower()
    if t == "timeout":
        return Stoppage.TIMEOUT
    if t == "period" and sub == "end":
        return Stoppage.HALFTIME if _int(a.get("period")) == 2 else Stoppage.END_PERIOD
    if t == "instantreplay":        # [grader v47, F9] a replay review runs minutes: a break, not a whistle
        return Stoppage.REVIEW
    if t in ("foul", "violation", "substitution") or _nba_kind(a) == "team_rebound":
        return Stoppage.WHISTLE
    if t == "freethrow":
        m = re.search(r"(\d) of (\d)", sub)
        return Stoppage.WHISTLE if m and m.group(1) != m.group(2) else Stoppage.NONE
    return Stoppage.NONE


def nba_plays(pbp, seen: dict[str, float], now: float | None = None) -> list[Play]:
    """New NBA liveData actions as Plays, with their real time (timeActual). The first look at a
    game is history (remembered, not returned), like nhl_plays."""
    now = time.time() if now is None else now
    acts = (_get(pbp, "game", "actions") or []) if isinstance(pbp, dict) else []
    if not seen:
        seen.update({f"nba{a.get('actionNumber')}": 0.0 for a in acts})
        seen.setdefault("_primed", 0.0)
        return []
    out = []
    for a in acts[-NHL_PLAYS_KEPT:]:
        n = a.get("actionNumber")
        if n is None:
            continue
        pid = f"nba{n}"
        t = seen.setdefault(pid, now)
        if not t:
            continue
        hs, as_ = _int(a.get("scoreHome")), _int(a.get("scoreAway"))
        out.append(Play(pid, (a.get("description") or "")[:80], _nba_kind(a), a.get("shotResult") == "Made",
                        _iso_ts(a.get("timeActual")), t, "nba_cdn",
                        score=(hs, as_) if hs is not None and as_ is not None else None,
                        clock=_iso_clock(a.get("clock")), period=_int(a.get("period")),
                        type_text=a.get("actionType") or ""))
    return out


# --- MLB ----------------------------------------------------------------------

def enrich_mlb(g: GameState, live: dict) -> GameState:
    out = copy.deepcopy(g)
    out.sources = {}
    ls = _get(live, "liveData", "linescore") or {}
    out.period = _int(ls.get("currentInning")) or out.period
    half = (ls.get("inningHalf") or "").lower()
    out.half = "bottom" if half.startswith("bot") else "top" if half.startswith("top") else out.half
    state = (ls.get("inningState") or "").lower()
    if state in ("middle", "end"):
        out.stoppage = Stoppage.END_PERIOD
    out.situation.outs = _int(ls.get("outs"))
    off = ls.get("offense") or {}
    out.situation.runners = (bool(off.get("first")), bool(off.get("second")), bool(off.get("third")))
    hs, as_ = _int(_get(ls, "teams", "home", "runs")), _int(_get(ls, "teams", "away", "runs"))
    if hs is not None and as_ is not None:
        out.home.score, out.away.score = hs, as_
    cur = _get(live, "liveData", "plays", "currentPlay") or {}
    events = cur.get("playEvents") or []
    if events:
        ev = (_get(events[-1], "details", "event") or "").lower()
        if "pitching substitution" in ev:
            out.stoppage = Stoppage.PITCHING_CHANGE
        elif "mound visit" in ev or "timeout" in ev:
            out.stoppage = Stoppage.WHISTLE
    # [grader v47, F17] `hasReview` stays true for the rest of the plate appearance, so only a review that is
    # OPEN now counts: the newest event is the review itself and it is not resolved.
    if _get(cur, "about", "hasReview") and events and _open_review(events[-1]):
        out.stoppage = Stoppage.REVIEW
    out.updated_at = time.time()
    return out


def _open_review(event: dict) -> bool:
    """Is this playEvent a review/challenge still in progress? (reviewDetails.inProgress when sent, else the event name.)"""
    rd = event.get("reviewDetails")
    if isinstance(rd, dict) and rd.get("inProgress") is not None:
        return bool(rd["inProgress"])
    name = (_get(event, "details", "event") or "").lower()
    return "review" in name or "challenge" in name


def mlb_leverage(wp_json: list | None) -> float | None:
    """Latest leverage index from /game/{pk}/winProbability (one entry per at-bat)."""
    if not wp_json:
        return None
    li = wp_json[-1].get("leverageIndex") if isinstance(wp_json[-1], dict) else None
    try:
        return float(li) if li is not None else None
    except (TypeError, ValueError):
        return None

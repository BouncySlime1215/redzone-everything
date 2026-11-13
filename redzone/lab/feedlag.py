"""Feed-arrival log: which rows a feed publishes and when WE first saw each one (metadata only, no network).

The show assumes the feed is 8 s behind live; measured, ESPN's summary is about 36 s and the NBA league feed about
11 s. Replay numbers depend on the real arrival lag, so a low-priority sidecar (scripts/feedlag_logger.py) polls the
day's games and appends one JSONL row per feed row the first time it appears:

    {"ev":"row","t":<epoch we first saw it>,"prev":<epoch we sent the poll before that one>,"src":"espn_summary",
     "lg":"nba","game":"<espn event id>","row":"<row id>","kind":"foul","typ":"<feed type>","stop":"whistle",
     "stamp":<epoch of the row's own wallclock>,"per":3,"clk":412.0,"pub":<epoch the feed says it published, or null>}

The row was published somewhere in (prev, t], so the lag is known to within one poll gap: scripts report both the
upper bound (t - stamp) and the midpoint. No text, no player names, no scores are kept. This module is pure: the
polling loop, the rate limit and the backoff live in scripts/feedlag_logger.py, the numbers in feedlag_report.py.
The show never imports it.

Why: C-02 (v2 WORKPLAN, tracks/C-picture-audio-timing.md C2): every replay assumes a clean 8 s feed because the recorded nights hold no arrival times (L107), so real arrival lags per row kind are logged nightly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from redzone.models import League
from redzone.sources import espn, leagues

SRC_ESPN = "espn_summary"
SRC_CDN = "nba_cdn"
FEED_SOURCES = (SRC_ESPN, SRC_CDN)
LOG_VERSION = 1
FLOOR_INTERVAL = 10.0         # seconds between two requests for one game and source: never lower, whatever the flags say
HARD_RPS = 4.0                # requests per second across everything: never higher, whatever the flags say
BULK_ROWS = 40                # more new rows than this in one answer is a feed that was blank or stale, not arrivals
ROLLOVER_HOURS = 6.0          # a slate (one night of games, one log file) ends at 06:00 US Eastern
EASTERN = ZoneInfo("America/New_York")

# One vocabulary for both feeds, so row kinds line up across sources.
ROW_KINDS = ("shot_made", "shot_miss", "free_throw", "rebound", "turnover", "foul", "timeout", "sub", "period_start",
             "period_end", "game_end", "jumpball", "violation", "review", "steal", "block", "other")


def slate_date(now: float) -> str:
    """The night a moment belongs to, as YYYY-MM-DD (US Eastern, rolling over at 06:00): a 1 AM tip is still last night's."""
    return (datetime.fromtimestamp(now, EASTERN) - timedelta(hours=ROLLOVER_HOURS)).strftime("%Y-%m-%d")


# --- row kinds --------------------------------------------------------------------------------------------

def espn_kind(p: dict) -> str:
    """ESPN summary play -> a ROW_KINDS word, from its type text (ESPN spells the same play many ways)."""
    t = re.sub(r"\s+", " ", str(espn._get(p, "type", "text") or "")).strip().lower()
    if "timeout" in t or "time out" in t:
        return "timeout"
    if t in ("end game", "end of game"):
        return "game_end"
    if t == "end period" or t.startswith("end of"):
        return "period_end"
    if t in ("start period", "start of period", "period start"):
        return "period_start"
    if "jumpball" in t or "jump ball" in t:
        return "jumpball"
    if "free throw" in t or "freethrow" in t:
        return "free_throw"
    if "turnover" in t:
        return "turnover"
    if "foul" in t or "charge" in t:
        return "foul"
    if "substitution" in t:
        return "sub"
    if "rebound" in t:
        return "rebound"
    if "steal" in t:
        return "steal"
    if t.startswith("block"):
        return "block"
    if "challenge" in t or "review" in t:
        return "review"
    if "violation" in t or "kicked ball" in t or "delay" in t:
        return "violation"
    if any(w in t for w in ("shot", "layup", "dunk", "hook", "jumper", "tip")):
        return "shot_made" if p.get("scoringPlay") else "shot_miss"
    return "other"


def cdn_kind(a: dict) -> str:
    """NBA liveData action -> a ROW_KINDS word."""
    t, sub = str(a.get("actionType") or "").lower(), str(a.get("subType") or "").lower()
    if t in ("2pt", "3pt", "heave"):
        return "shot_made" if a.get("shotResult") == "Made" else "shot_miss"
    if t == "period":
        return "period_start" if sub == "start" else "period_end"
    return {"freethrow": "free_throw", "rebound": "rebound", "turnover": "turnover", "foul": "foul",
            "timeout": "timeout", "substitution": "sub", "game": "game_end", "jumpball": "jumpball",
            "violation": "violation", "instantreplay": "review", "steal": "steal", "block": "block"}.get(t, "other")


# --- parsing a payload into rows ----------------------------------------------------------------------------

@dataclass(frozen=True)
class FeedRow:
    row: str                 # the feed's own id for the row
    stamp: float | None      # the row's own wallclock (epoch): when it happened, per the feed
    kind: str                # ROW_KINDS word
    typ: str                 # the feed's own type word (no player names in it)
    stop: str                # what the show's own rule says the game is doing after this row ("none" = live play)
    per: int | None
    clk: float | None        # seconds left in the period
    pub: float | None        # when the feed says it published the row (NBA liveData "edited"), else None


def espn_rows(league: League, summary) -> list[FeedRow]:
    """Every play in an ESPN summary payload, in feed order. A malformed play is skipped, never fatal."""
    if not isinstance(summary, dict):
        return []
    out = []
    for p in espn.flatten_plays(league, summary):
        try:
            rid = str(p.get("id") or "")
            if not rid:
                continue
            out.append(FeedRow(rid, espn._ts(p.get("wallclock")), espn_kind(p),
                               re.sub(r"\s+", " ", str(espn._get(p, "type", "text") or "")).strip()[:40],
                               espn.stoppage_for(league, p).value, espn._int(espn._get(p, "period", "number")),
                               espn._clock_s(espn._get(p, "clock", "displayValue")), None))
        except Exception:      # one odd play must not sink the poll
            continue
    return out


def cdn_rows(pbp) -> list[FeedRow]:
    """Every action in an NBA liveData play-by-play payload, in feed order."""
    acts = (espn._get(pbp, "game", "actions") or []) if isinstance(pbp, dict) else []
    out = []
    for a in acts:
        try:
            n = a.get("actionNumber")
            if n is None:
                continue
            typ = "/".join(x for x in (str(a.get("actionType") or ""), str(a.get("subType") or "")) if x)[:40]
            out.append(FeedRow(str(n), leagues._iso_ts(a.get("timeActual")), cdn_kind(a), typ,
                               leagues.nba_stoppage(a).value, espn._int(a.get("period")),
                               leagues._iso_clock(a.get("clock")), leagues._iso_ts(a.get("edited"))))
        except Exception:
            continue
    return out


# --- noticing what is new -----------------------------------------------------------------------------------

def row_event(src: str, lg: str, game: str, gid: str | None, fr: FeedRow, t: float, prev: float | None) -> dict:
    ev = {"ev": "row", "t": round(t, 3), "prev": None if prev is None else round(prev, 3), "src": src, "lg": lg,
          "game": game, "row": fr.row, "kind": fr.kind, "typ": fr.typ, "stop": fr.stop, "stamp": fr.stamp,
          "per": fr.per, "clk": fr.clk, "pub": fr.pub}
    if gid and gid != game:
        ev["gid"] = gid
    return ev


class ArrivalTracker:
    """One game on one source: which rows were already seen, and when the poll before this one was sent.

    The first successful look at a game that is already under way is history: those rows existed before we
    started, so their arrival is unknown and they are remembered, not logged (`baseline` counts them). A game
    first seen before the tip (`primed=True`) has no history: everything it publishes afterwards is new."""

    def __init__(self, src: str, lg: str, game: str, gid: str | None = None, primed: bool = False,
                 known: set[str] | None = None):
        self.src, self.lg, self.game, self.gid = src, lg, game, gid
        self.primed = primed
        self.seen: set[str] = set(known or ())
        self.prev: float | None = None      # send time of the last poll that came back with a usable answer
        self.baseline = 0
        self.logged = 0
        self.bulk = 0

    def empty_poll(self, sent: float) -> None:
        """A poll that proved nothing was published yet (the feed's file does not exist before the tip)."""
        if self.primed:
            self.prev = sent

    def observe(self, rows: list[FeedRow], sent: float, received: float) -> list[dict]:
        """Rows in this answer that were not there before, as log events. `sent`/`received` bracket the request."""
        new = [r for r in rows if r.row not in self.seen]
        events: list[dict] = []
        if not self.primed:
            self.baseline = len(new)
            self.primed = True
            events.append({"ev": "game", "t": round(received, 3), "src": self.src, "lg": self.lg, "game": self.game,
                           "baseline": self.baseline})
        elif len(new) > BULK_ROWS:
            # A normal 10 s window brings a handful of rows. Dozens at once means the feed was empty, stale or
            # unreachable and caught up: their arrival is unknown, so they are remembered like history.
            self.bulk += len(new)
            events.append({"ev": "bulk", "t": round(received, 3), "src": self.src, "lg": self.lg, "game": self.game,
                           "rows": len(new)})
        else:
            events = [row_event(self.src, self.lg, self.game, self.gid, r, received, self.prev) for r in new]
            self.logged += len(events)
        self.seen.update(r.row for r in new)
        self.prev = sent
        return events


# --- the append-only day files ------------------------------------------------------------------------------

class DayLog:
    """data/feedlag/YYYY-MM-DD.jsonl, append only, one file per slate. Nothing here is ever rewritten."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, slate: str) -> Path:
        return self.root / f"{slate}.jsonl"

    def append(self, slate: str, events: list[dict]) -> None:
        if not events:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        blob = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events)
        with open(self.path(slate), "a", encoding="utf-8") as f:
            f.write(blob)

    def known(self, slate: str) -> dict[tuple[str, str], set[str]]:
        """(source, game) -> row ids already in today's file, so a restart never logs a row twice."""
        out: dict[tuple[str, str], set[str]] = {}
        p = self.path(slate)
        if not p.exists():
            return out
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("ev") == "row":
                out.setdefault((str(e.get("src")), str(e.get("game"))), set()).add(str(e.get("row")))
        return out

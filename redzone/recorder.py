"""Flight recorder: the real-world numbers the simulator needs.

For every play, when it happened (feed wallclock) vs when THIS app first saw it, per source.
That's the true feed lag, which decides how much head start we get on the stream. Also logs
the source race (who reported each score first). Small JSONL files, one per day:

    data/recordings/2026-11-02/plays.jsonl   {"t","league","game","play","type","wall","lag","source","text"}
    data/recordings/2026-11-02/race.jsonl    {"t","league","game","label","first","others"}
                                             label "score A-H" or "stop p<period> <clock s>" (a dead ball)

Finished games' full play-by-play is also saved to data/archive/<league>/ for backtesting.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from redzone.models import GameState
from redzone.sources import espn

ROOT = Path(__file__).resolve().parent.parent / "data"
RACE_SETTLE_S = 90.0   # a race row is written this long after the first report, with every follower's lag


class Recorder:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self._seen: dict[tuple[str, str], set[str]] = {}   # (game, source) -> play ids
        self._nhl_logged: set[tuple[str, str]] = set()
        self._race_done: set[tuple[str, str, float]] = set()
        self._archived: set[str] = set()

    def _day(self) -> Path:
        d = self.root / "recordings" / datetime.now().strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write(self, name: str, rows: list[dict]) -> None:
        if not rows:
            return
        with open(self._day() / name, "a") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")

    def sync_anchor(self, row: dict) -> None:
        """[auto-anchors] One on-screen break edge (matched feed event) or drift alarm per row."""
        self._write("sync_anchors.jsonl", [row])

    def plays(self, g: GameState, plays: list[dict], source: str) -> None:
        now = time.time()
        # Per source, so each feed's lag is measured on its own: the slower feed's copy of a
        # play still gets a row, and the gap between the two rows is what the race is about.
        seen = self._seen.setdefault((g.game_id, source), set())
        first = not seen
        rows = []
        for p in plays:
            pid = str(p.get("id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            if first:
                continue  # plays that existed before we started watching don't tell us the lag
            wall = espn._ts(p.get("wallclock"))
            rows.append({"t": round(now, 2), "league": g.league.value, "game": g.game_id, "play": pid,
                         "type": espn._get(p, "type", "text"), "wall": wall,
                         "lag": round(now - wall, 2) if wall else None, "source": source,
                         "text": (p.get("text") or "")[:80]})
        self._write("plays.jsonl", rows)

    def feed_plays(self, g: GameState, plays) -> None:
        """Plays from a league feed with real event times (NBA liveData timeActual), as plays.jsonl
        rows under their own source: timing_report.py measures their lag next to ESPN's."""
        now = time.time()
        plays = [p for p in plays if (g.game_id, p.id) not in self._nhl_logged]
        self._nhl_logged.update((g.game_id, p.id) for p in plays)   # ids are prefixed per feed: one set
        self._write("plays.jsonl", [{"t": round(p.seen_at or now, 2), "league": g.league.value, "game": g.game_id,
                                     "play": p.id, "type": p.type_text, "wall": p.wallclock,
                                     "lag": round((p.seen_at or now) - p.wallclock, 2) if p.wallclock else None,
                                     "source": p.source, "text": (p.text or "")[:80]} for p in plays])

    def nhl_seen(self, g: GameState, pbp, seen: dict[str, float]) -> None:
        """NHL api-web events with when we first saw them (the feed has no wallclock). Joined to
        ESPN's wallclocked plays by period, clock and type, this gives api-web's real lag, and it
        is what a backtest of sources.nhl_primary replays. The first look is history: not logged."""
        from redzone.sources.leagues import nhl_plays
        rows = [{"t": round(p.seen_at, 2), "league": "nhl", "game": g.game_id, "event": p.id,
                 "type": p.type_text, "kind": p.kind, "period": p.period, "clock": p.clock,
                 "text": p.text} for p in nhl_plays(pbp, seen)]
        new = [r for r in rows if (r["game"], r["event"]) not in self._nhl_logged]
        self._nhl_logged.update((r["game"], r["event"]) for r in new)
        self._write("nhl_seen.jsonl", new)

    def race(self, merger, now: float | None = None) -> None:
        """Write each race event once it has settled (RACE_SETTLE_S), so the slower sources'
        lags are in the row. Written events are tracked by identity, not by list index: the
        merger trims its list at 2000, which used to freeze the index and stop all race rows."""
        now = time.time() if now is None else now
        live = {(e.game_id, e.label, e.first_at) for e in merger.race}
        rows = []
        for e in merger.race:
            key = (e.game_id, e.label, e.first_at)
            if key in self._race_done or e.first_at > now - RACE_SETTLE_S:
                continue
            self._race_done.add(key)
            g = merger.games.get(e.game_id)
            rows.append({"t": round(e.first_at, 2), "league": g.league.value if g else None,
                         "game": e.game_id, "label": e.label, "first": e.first_source,
                         "others": dict(e.others)})
        if len(self._race_done) > 4000:
            self._race_done &= live
        self._write("race.jsonl", rows)

    def archive(self, g: GameState, summary: dict) -> None:
        """Keep a finished game's full play-by-play for future backtests (NBA/NHL/MLB too)."""
        if g.game_id in self._archived:
            return
        self._archived.add(g.game_id)
        d = self.root / "archive" / g.league.value
        d.mkdir(parents=True, exist_ok=True)
        (d / f"summary_{g.game_id}.json").write_text(json.dumps(summary))

    def lag_stats(self, day: str | None = None) -> dict:
        """Median/p90 feed lag per league and source from the plays log."""
        f = (self.root / "recordings" / (day or datetime.now().strftime("%Y-%m-%d")) / "plays.jsonl")
        if not f.exists():
            return {}
        by: dict[str, list[float]] = {}
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("lag") is not None and 0 <= r["lag"] < 600:
                by.setdefault(f"{r['league']}:{r['source']}", []).append(r["lag"])
        out = {}
        for k, v in by.items():
            v.sort()
            out[k] = {"n": len(v), "median": v[len(v) // 2], "p90": v[int(len(v) * 0.9)]}
        return out

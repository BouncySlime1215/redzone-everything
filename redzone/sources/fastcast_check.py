"""[fastcast live check] Is ESPN's push feed faster than polling, and does it say the same thing?

FastCast (fastcast.py) stays OFF in the Merger until this check passes on real live games. While
it is off, `sources.fastcast_check` (default on) runs the push feed in SHADOW: subscribed to the
first few live games, its updates are parsed and logged but never merged, so nothing on screen
can change. For every play both feeds deliver, the check notes when each delivered it and what
each said (score after the play, play type). The verdict, written to data/fastcast_check.json and
shown in /state under health.fastcast_check:

    pass        enough plays paired (MIN_PAIRED over at least MIN_GAMES games), FastCast ahead by
                at least MIN_MEDIAN_LEAD_S at the median, behind polling by more than BEHIND_S on
                at most MAX_BEHIND_PCT of plays, delivered at least MIN_COVERAGE_PCT of the plays
                polling saw, and not one disagreement on a play's score or type
    fail        enough plays and any of those is missed (the reason is listed)
    pending     not enough yet

The check stops shadowing after a verdict or after MAX_SHADOW_S. With a pass, turning FastCast on
is one setting (`sources: {fastcast: true}`); `sources.fastcast_auto_enable` (default off) would
flip it automatically. Offline, the same grading reads a night's recordings/<day>/plays.jsonl:

    python scripts/fastcast_check.py                    # today
    python scripts/fastcast_check.py --day 2026-11-03
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

POLLED = "espn_summary"
PUSHED = "espn_fastcast"
MIN_PAIRED = 150             # plays both feeds delivered, new to both (not in their first batch)
MIN_GAMES = 2
MIN_MEDIAN_LEAD_S = 1.0      # a second of switch lead time is worth having; less isn't worth the risk
BEHIND_S = 2.0               # FastCast later than polling by more than this counts as "behind"
MAX_BEHIND_PCT = 5.0
MIN_COVERAGE_PCT = 98.0      # of the plays polling delivered while both watched the game
MAX_TOPICS = 4               # shadow only this many live games at once (keep the Mac light)
MAX_SHADOW_S = 4 * 3600.0
PAIR_WINDOW_S = 120.0        # a play polling saw, that FastCast hadn't within this long: missed


def _score(p: dict):
    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return num(p.get("homeScore")), num(p.get("awayScore"))


def _type(p: dict) -> str:
    t = p.get("type")
    return str((t.get("text") if isinstance(t, dict) else t) or "")


@dataclass
class _Seen:
    t: float
    score: tuple
    kind: str


@dataclass
class FastcastCheck:
    path: Path | None = None
    # When shadowing began: the first live game either feed reported, NOT when the app started.
    # (Live Nov 4: the app restarted at midnight, sat 4 h with no live games, and the check gave up
    # with "0 plays over 0 games in 4 h" before the night's games, so FastCast never logged a row.)
    started: float | None = None
    seen: dict = field(default_factory=dict)          # (source, game, play) -> _Seen
    primed: set = field(default_factory=set)          # (source, game): first batch already skipped
    last_push: dict = field(default_factory=dict)     # game -> last pushed update (FastCast still watching)
    errors: int = 0                                   # pushed docs we couldn't use
    updates: int = 0
    verdict: dict | None = None

    # ------------------------------------------------------------------ input
    def saw(self, source: str, game: str, plays: list[dict], now: float | None = None) -> None:
        """A feed delivered these plays for this game (all of them; only new ones count)."""
        if self.done or source not in (POLLED, PUSHED):
            return
        now = time.time() if now is None else now
        if self.started is None:
            self.started = now
        key = (source, str(game))
        first = key not in self.primed
        self.primed.add(key)
        if source == PUSHED:
            self.updates += 1
            self.last_push[str(game)] = now
        for p in plays:
            pid = str(p.get("id") or "")
            k = (source, str(game), pid)
            if not pid or k in self.seen:
                continue
            # the first batch only tells us what existed before we started watching: no lag in it
            self.seen[k] = _Seen(float("nan") if first else now, _score(p), _type(p))

    def unusable(self) -> None:
        if not self.done:
            self.errors += 1

    # ------------------------------------------------------------------ grading
    @property
    def done(self) -> bool:
        return self.verdict is not None and self.verdict["result"] in ("pass", "fail", "stopped")

    def games(self) -> set:
        """Games both feeds have watched (only those can be paired)."""
        g = {k[1] for k in self.primed if k[0] == PUSHED}
        return g & {k[1] for k in self.primed if k[0] == POLLED}

    def grade(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        shadowed = now - self.started if self.started is not None else 0.0
        both = self.games()
        leads, missed, disagree, examples = [], 0, 0, []
        for (src, game, pid), s in self.seen.items():
            if src != POLLED or game not in both or s.t != s.t:       # NaN: in polling's first batch
                continue
            f = self.seen.get((PUSHED, game, pid))
            if f is None:
                # missed only if FastCast was still watching this game well after polling had the play
                # (a game dropped from the shadow's few topics doesn't count against it)
                if self.last_push.get(game, float("-inf")) >= s.t + PAIR_WINDOW_S:
                    missed += 1
                continue
            if f.t != f.t:                                            # in FastCast's first batch
                continue
            leads.append(s.t - f.t)
            if (f.score != s.score and None not in f.score + s.score) or (f.kind and s.kind and f.kind != s.kind):
                disagree += 1
                if len(examples) < 5:
                    examples.append({"game": game, "play": pid, "polled": [s.score, s.kind],
                                     "pushed": [f.score, f.kind]})
        leads.sort()
        n = len(leads)
        q = (lambda p: round(leads[min(n - 1, int(p * n))], 2)) if n else (lambda p: None)
        covered = n + missed
        out = {"paired_plays": n, "games": len(both), "missed_by_fastcast": missed,
               "coverage_pct": round(100 * n / covered, 1) if covered else None,
               "lead_median_s": q(0.5), "lead_p10_s": q(0.1), "lead_p90_s": q(0.9),
               "behind_pct": round(100 * sum(1 for x in leads if x < -BEHIND_S) / n, 1) if n else None,
               "disagreements": disagree, "disagreement_examples": examples,
               "unusable_docs": self.errors, "pushed_updates": self.updates,
               "shadow_s": round(shadowed)}
        reasons = []
        if out["lead_median_s"] is not None and out["lead_median_s"] < MIN_MEDIAN_LEAD_S:
            reasons.append(f"median lead {out['lead_median_s']} s < {MIN_MEDIAN_LEAD_S} s")
        if out["behind_pct"] is not None and out["behind_pct"] > MAX_BEHIND_PCT:
            reasons.append(f"behind polling on {out['behind_pct']}% of plays > {MAX_BEHIND_PCT}%")
        if out["coverage_pct"] is not None and out["coverage_pct"] < MIN_COVERAGE_PCT:
            reasons.append(f"delivered {out['coverage_pct']}% of polled plays < {MIN_COVERAGE_PCT}%")
        if disagree:
            reasons.append(f"{disagree} plays disagree with polling")
        enough = n >= MIN_PAIRED and len(both) >= MIN_GAMES
        if disagree:                    # one disagreement is enough to fail: never merge a feed that lies
            out["result"] = "fail"
        elif not enough:
            out["result"] = "stopped" if shadowed > MAX_SHADOW_S else "pending"
            if out["result"] == "stopped":
                reasons.append(f"only {n} plays over {len(both)} games in {MAX_SHADOW_S / 3600:.0f} h")
        else:
            out["result"] = "fail" if reasons else "pass"
        out["reasons"] = reasons
        out["criteria"] = {"min_paired": MIN_PAIRED, "min_games": MIN_GAMES, "min_median_lead_s": MIN_MEDIAN_LEAD_S,
                           "behind_s": BEHIND_S, "max_behind_pct": MAX_BEHIND_PCT,
                           "min_coverage_pct": MIN_COVERAGE_PCT}
        return out

    def tick(self, now: float | None = None) -> dict:
        """Grade, keep the verdict once final, write the file. Returns the current grade."""
        if self.done:
            return self.verdict
        g = self.grade(now)
        self.verdict = g
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(g, indent=1))
            except OSError:
                log.warning("fastcast check: could not write %s", self.path)
        if g["result"] in ("pass", "fail", "stopped"):
            log.warning("fastcast live check: %s (%s)", g["result"].upper(),
                        "; ".join(g["reasons"]) or f"median lead {g['lead_median_s']} s on {g['paired_plays']} plays")
        return g


def grade_log(path: Path, now: float | None = None) -> dict:
    """The same verdict from a night's plays.jsonl (rows from the flight recorder, one per play per
    source; the recorder already leaves out each source's first batch). Rows carry no score, so
    the offline grade checks timing, coverage and the play type, not the score."""
    chk = FastcastCheck()
    rows = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("source") in (POLLED, PUSHED) and r.get("play") and r.get("t") is not None:
            rows.append(r)
    for r in sorted(rows, key=lambda r: r["t"]):
        key = (r["source"], str(r["game"]))
        chk.primed.add(key)
        if r["source"] == PUSHED:
            chk.last_push[str(r["game"])] = float(r["t"])
        k = (r["source"], str(r["game"]), str(r["play"]))
        if k not in chk.seen:
            chk.seen[k] = _Seen(float(r["t"]), (None, None), str(r.get("type") or ""))
    if rows:
        chk.started = rows[0]["t"]
    return chk.grade(now if now is not None else (rows[-1]["t"] + PAIR_WINDOW_S + 1 if rows else time.time()))

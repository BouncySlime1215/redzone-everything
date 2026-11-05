"""[time-shift buffer, SHADOW] Nick, 9:10 AM Nov 5: "the time-shift buffer is now the main architecture".

While timing.behind_live_s is off on screen, a second decider (the "editor") runs every decide tick on
the same games, scores and play-by-play timelines, but believes each picture runs B s further behind
real life (timing.behind_live_s.by_league, capped at max_s): what the show would do with the buffer on.
It never touches a stick or the screen. Every change of its decision is a row in data/buffer_shadow.jsonl
next to what the live decider has on screen, so lane 3 can grade buffered vs live on the same night
(mid-play entries, big plays shown from the start, dead air, switches/h) before RC8 turns it on.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


class BufferedDelays:
    """The live DelayBook seen B s later: get(target).mu += B(target). Writes are ignored (the editor
    holds nothing; the live book is the controller's)."""

    def __init__(self, live, behind_of):
        self.live, self.behind_of = live, behind_of

    def get(self, target):
        d = self.live.get(target)
        # a stream the live controller already holds behind (timing.behind_live_s.enabled per league) has that
        # hold in mu: only the rest of B is added
        b = float(self.behind_of(target) or 0.0) - float(getattr(d, "behind", 0.0) or 0.0)
        return dataclasses.replace(d, mu=d.mu + b) if b > 0 else d

    @property
    def book(self) -> dict:
        return {t: self.get(t) for t in list(self.live.book)}

    def set_behind(self, *a, **k) -> None:
        return None

    def __getattr__(self, name):
        return getattr(self.live, name)


PROFILE = Path(__file__).resolve().parent / "lab" / "feed_lag_profile.json"
P95 = 19                  # index of q95 in the profile's 0..100 step-5 quantiles


def _p95_table() -> dict:
    try:
        prof = json.loads(PROFILE.read_text())
    except (OSError, ValueError):
        return {}
    out = {lg: {"espn_summary": row["q"][P95]} for lg, row in (prof.get("leagues") or {}).items() if len(row.get("q", [])) > P95}
    for src, rows in (prof.get("sources") or {}).items():
        for lg, row in rows.items():
            if len(row.get("q", [])) > P95:
                out.setdefault(lg, {})[src] = row["q"][P95]
    return out


class BufferShadow:
    # [Nick 9:35 AM] buffer = feed-lag p95 + sync error + tune/seek + margin, floor ~10-15 s, cap max_s.
    # Tonight only LOGGED next to the fixed by_league B (planner: fit Friday, ratchet after locked gates).
    FLOOR_S = 12.0
    MARGIN_S = 5.0
    STAY_LEAVE_EVERY_S = 15.0

    def __init__(self, cfg, live_decider, behind_of, log_path: Path | None = None, clock=time.time,
                 games=None):
        from redzone.decider.decider import Decider
        self.cfg, self.live, self.behind_of, self.log_path, self._clock = cfg, live_decider, behind_of, log_path, clock
        self.delays = BufferedDelays(live_decider.delays, behind_of)
        self.editor = Decider(cfg, self.delays, clock=clock)
        self.editor.timelines = live_decider.timelines   # one play-by-play history, read by both
        self._last: tuple | None = None
        self.n_rows = 0
        self.games = games or (lambda gid: None)
        self._p95 = _p95_table()
        self._sl_at = 0.0
        self._sl_key: tuple | None = None

    def tick(self, cands: dict) -> object | None:
        self.editor.entry_cost = getattr(self.live, "entry_cost", None)   # the engine's measured switch costs
        try:
            d = self.editor.decide(cands, None, commit=True)
        except Exception:
            log.exception("buffer shadow decide failed")
            return None
        try:
            self._stay_leave(d, cands, self._clock())
        except Exception:
            log.exception("buffer shadow stay/leave log failed")
        key = (d.action, d.target, d.reason)
        if d.action != "stay" or key != self._last:
            self._last = key
            now = self._clock()
            x = cands.get(d.target or "")
            target = getattr(x, "target", None)
            sc = getattr(x, "scored", None)
            lg = getattr(getattr(sc, "league", None), "value", None)
            b = float(self.behind_of(target) or 0.0) if target else 0.0
            row = {"ts": round(now, 2), "mode": "shadow", "kind": "entry" if d.action == "switch" else "decision",
                   "league": lg, "gid": d.target, "stick": None, "role": None, "behind_s": b,
                   "action": d.action, "target": target, "reason": d.reason, "decision_kind": getattr(d, "kind", ""),
                   "state": getattr(d.state, "value", str(d.state)), "rewind_s": getattr(d, "rewind_s", 0.0),
                   "editor_on": self.editor.current, "live_on": self.live.current}
            if target:
                ld = self.live.delays.get(target)
                row["source"] = getattr(ld, "source", "prior")
                if d.action == "switch":
                    try:
                        ec = self.editor.entry_cost
                        cost = float(ec(target) if ec is not None else self.editor._deadline_cost(target))
                    except Exception:
                        cost = 0.0
                    mu, sd = float(ld.mu), float(ld.current_sigma(now))
                    pic = now + cost
                    row.update({"to": d.target, "picture_at": round(pic, 2), "mu": round(mu, 2), "sd": round(sd, 2),
                                "arena_at": round(pic - b - mu - float(getattr(d, "rewind_s", 0.0) or 0.0), 2)})
                row.update(self.adaptive(target, lg))
            self._write(row)
        return d

    def adaptive(self, target: str | None, league: str | None) -> dict:
        """The adaptive buffer's terms for one target (log only tonight)."""
        lg = league or ""
        src = "nba_cdn" if lg == "nba" and self.cfg.get("sources.nba_primary", False) else \
            "nhl_wsc" if lg == "nhl" and self.cfg.get("sources.nhl_primary", False) else "espn_summary"
        row = self._p95.get(lg) or self._p95.get({"cfb": "nfl", "wnba": "nba", "wcbb": "cbb"}.get(lg, ""), {})
        p95 = row.get(src, row.get("espn_summary"))
        src = src if src in row else "espn_summary"
        sd, tune = None, None
        if target:
            try:
                sd = float(self.live.delays.get(target).current_sigma(self._clock()))
            except Exception:
                sd = None
            ec = getattr(self.live, "entry_cost", None)
            try:
                tune = float(ec(target)) if ec is not None else float(self.live._deadline_cost(target))
            except Exception:
                tune = None
        cap = float(self.cfg.get("timing.behind_live_s.max_s", 90.0))
        parts = [p95, 2 * sd if sd is not None else None, tune]
        total = sum(x for x in parts if x is not None) + self.MARGIN_S if p95 is not None else None
        return {"feed_lag_p95_s": round(p95, 2) if p95 is not None else None, "feed_lag_source": src,
                "sync_err_s": round(2 * sd, 2) if sd is not None else None,
                "tune_s": round(tune, 2) if tune is not None and tune == tune and tune < 1e6 else None,
                "margin_s": self.MARGIN_S, "floor_s": self.FLOOR_S, "cap_s": cap,
                "behind_adaptive_s": round(min(cap, max(self.FLOOR_S, total)), 2) if total is not None else None}

    def _context(self, gid: str | None, cands: dict) -> dict | None:
        x = cands.get(gid or "")
        if x is None:
            return None
        sc = x.scored
        g = self.games(gid)
        sit = dataclasses.asdict(g.situation) if g is not None and dataclasses.is_dataclass(getattr(g, "situation", None)) else {}
        return {"gid": gid, "league": getattr(sc.league, "value", None), "target": x.target,
                "value": round(float(sc.score), 2), "moment": round(float(sc.moment), 2), "stakes": round(float(sc.stakes), 3),
                "personal": round(float(sc.personal), 2), "closeness": round(float(sc.closeness), 3),
                "lead_wp": sc.lead_wp, "home_wp": getattr(g, "home_win_prob", None),
                "watch_value": sc.watch_value, "watch_terms": sc.watch_terms or None, "stakes_line": sc.stakes_line,
                "crunch": sc.crunch, "crunch_now": sc.crunch_now, "crunch_state": sc.crunch_state, "late": sc.late,
                "high_leverage": sc.high_leverage, "garbage": sc.garbage, "mine": sc.mine, "is_break": sc.is_break,
                "lock": sc.lock, "pre_moment": sc.pre_moment, "tags": list(sc.tags or [])[:8], "reason": sc.reason,
                "period": getattr(g, "period", None), "clock_s": getattr(g, "clock_s", None),
                "score": [getattr(getattr(g, "home", None), "score", None), getattr(getattr(g, "away", None), "score", None)]
                if g is not None else None, "spread": getattr(g, "spread", None), "situation": sit or None}

    def _stay_leave(self, d, cands: dict, now: float) -> None:
        """[Nick 9:45 AM] every stay/leave call with the full game context and the predicted value of staying
        vs the best alternative (the decider's own value; Watch Value when lane 1 sets it). Log only."""
        on = self.editor.current
        if not on or on not in cands:
            return
        leave = d.action == "switch" and d.target != on
        alts = [(float(x.scored.score), gid) for gid, x in cands.items() if gid != on and x.target]
        best = max(alts) if alts else None
        key = (on, leave, d.target if leave else None, best[1] if best else None)
        if not leave and key == self._sl_key and now - self._sl_at < self.STAY_LEAVE_EVERY_S:
            return
        self._sl_key, self._sl_at = key, now
        here = self._context(on, cands)
        alt = self._context(best[1], cands) if best else None
        b = float(self.behind_of(here["target"]) or 0.0) if here and here.get("target") else 0.0
        self._write({"ts": round(now, 2), "mode": "shadow", "kind": "stay_leave", "action": "leave" if leave else "stay",
                     "league": here["league"] if here else None, "gid": on, "stick": None, "role": None, "behind_s": b,
                     "source": None, "to": d.target if leave else None, "reason": d.reason,
                     "decision_kind": getattr(d, "kind", ""), "value_stay": here["value"] if here else None,
                     "value_best_alt": alt["value"] if alt else None, "best_alt": best[1] if best else None,
                     "value_gap": round(here["value"] - alt["value"], 2) if here and alt else None,
                     "context": here, "alt_context": alt,
                     **self.adaptive(here.get("target") if here else None, here["league"] if here else None)})

    def _write(self, rec: dict) -> None:
        self.n_rows += 1
        if self.log_path is None:
            return
        try:
            with self.log_path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass

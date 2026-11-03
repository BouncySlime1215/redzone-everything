"""[highlight jump] The catch-up reel in a backtest: which clips existed when, and how reels went.

Clips: the archived ESPN summary's own in-game highlight videos when it has them (real headlines,
real post times: ESPN posts a clip minutes after the play), else one synthetic clip per big play,
posted after the league's median lag (NBA 4.5 min, NHL 5.5, NFL 14, measured on the archive).
A clip counts as seen when its play was on screen live (the engine's own rule, Decider._saw), and
a reel's clips are never shown twice.

Report lines (ReelCard.summary):
  reels                       reels played
  reel_s                      seconds of reel on screen
  reels_in_dead_pct           of those seconds, the share the game we left was in a dead ball on
                              screen (target ~100: a reel only ever replaces dead time)
  reel_commercial_s           ...of which inside a commercial break (ad seconds replaced by a reel)
  reel_home_big_missed        big plays of the game we left that aired while the reel was on (target 0)
  reel_big_missed_anywhere    big plays of any game that aired while the reel was on
  reel_home_late_s            seconds of live play missed at home because the reel ran past the restart
"""
from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as dc_replace

from redzone import reel as R
from redzone.lab import cutgrade, sportsgrade
from redzone.models import League

POST_LAG_S = {League.NFL: 840.0, League.NBA: 270.0, League.NHL: 330.0, League.MLB: 300.0}
REEL_TARGET = "reel:sim"


def _base(league: League) -> League:
    return getattr(league, "base", league)


def synthetic_clips(r, my_teams: set[str]) -> list[R.Clip]:
    """One clip per big play (sportsgrade.is_big), or per highlight-worthy scoring play in the NBA."""
    out, prev = [], None
    lg = _base(r.league)
    lag = POST_LAG_S.get(lg, 300.0)
    mine = any(a in my_teams for a in r.matchup.replace("@", " ").split())
    for w, p in zip(r.wall, r.plays):
        text = str(p.get("text") or "")
        pts = R.headline_points(text)
        big = sportsgrade.is_big(lg, p, prev)
        if lg == League.NBA and p.get("scoringPlay") and pts >= 14:
            big = True
        prev = p
        if not big:
            continue
        pts = max(pts, 24.0)
        out.append(R.Clip(f"{r.gid}:{p.get('id')}", r.gid, lg.value, text[:90], "", w + lag, 20.0, pts, mine,
                          R.FALLBACK_S, w))
    return out


def clips_for(r, my_teams: set[str]) -> tuple[list[R.Clip], bool]:
    """(clips, real?) for one archived game."""
    real = R.clips_from_summary(r.data or {}, _base(r.league).value, r.gid, my_teams) if r.data else []
    if real:
        for c in real:
            if c.play_at is None:
                c.play_at = c.published - POST_LAG_S.get(_base(r.league), 300.0)
        return real, True
    return synthetic_clips(r, my_teams), False


class SimReels:
    """Keeps 'the ready reel' for a backtest tick, like Reeler does live (no files: just the plan)."""

    def __init__(self, replays, cfg, my_teams: set[str]):
        self.cfg = cfg
        self.clips: list[R.Clip] = []
        self.real_games = 0
        for r in replays:
            cs, real = clips_for(r, my_teams)
            self.clips += cs
            self.real_games += real
        self.clips.sort(key=lambda c: c.published)
        self.shown: set[str] = set()
        self.plan: list[R.Clip] = []

    def _c(self, k, default):
        v = self.cfg.get(f"reel.{k}", default)
        return default if v is None else v

    def offer(self, now: float, dec) -> R.ReelOffer | None:
        if not self.cfg.get("reel.play", False):
            return None
        max_age = float(self._c("max_age_s", 1800))

        def seen(c: R.Clip) -> bool:
            if c.id in self.shown:
                return True
            if c.play_at is None:
                return False
            mu = dec.delays.get(dec.target_of(c.game_id) or f"youtubetv:{c.game_id}").mu
            return dec._saw(c.game_id, c.play_at + mu - 3, c.play_at + mu + 6)

        pool = [dc_replace(c) for c in self.clips if c.published <= now and now - c.published <= max_age]
        self.plan = R.plan_reel(pool, now=now, seen=seen, league_weight=self.cfg.get("score.league_weight") or {},
                                min_clips=int(self._c("min_clips", 5)), min_s=float(self._c("min_s", 30)),
                                max_s=float(self._c("max_s", 60)), max_age_s=max_age)
        return R.offer_of(REEL_TARGET, self.plan, now, min_clips=int(self._c("min_clips", 5)),
                          min_s=float(self._c("min_s", 30)), max_age_s=max_age,
                          league_weight=self.cfg.get("score.league_weight") or {})

    def played(self, n: int | None) -> None:
        self.shown.update(c.id for c in (self.plan if n is None else self.plan[:n]))


@dataclass
class ReelCard:
    reels: int = 0
    reel_s: float = 0.0
    dead_s: float = 0.0
    commercial_s: float = 0.0
    home_big_missed: int = 0
    big_missed_anywhere: int = 0
    home_late_s: float = 0.0
    clips_shown: int = 0
    missed: list = field(default_factory=list)

    def add(self, o: "ReelCard") -> None:
        for k, v in vars(o).items():
            if isinstance(v, list):
                getattr(self, k).extend(v)
            else:
                setattr(self, k, getattr(self, k) + v)

    def summary(self) -> dict:
        pct = lambda a, b: round(100 * a / max(1e-9, b), 1) if b else None
        return {"reels": self.reels, "reel_s": round(self.reel_s), "reels_in_dead_pct": pct(self.dead_s, self.reel_s),
                "reel_commercial_s": round(self.commercial_s), "reel_home_big_missed": self.home_big_missed,
                "reel_big_missed_anywhere": self.big_missed_anywhere,
                "reel_home_late_s": round(self.home_late_s), "reel_clips_shown": self.clips_shown}


def grade(replays, switches, lag: float, tune_s: float, t1: float, clips_shown: int = 0,
          homes: dict | None = None) -> ReelCard:
    """How each reel went, against what really happened in the game it stood in for (and everywhere).
    homes: reel switch time -> that game (the decider's home at the jump); else where the reel went
    back to, else where it was left from."""
    card = ReelCard(clips_shown=clips_shown)
    by_id = {r.gid: r for r in replays}
    for n, s in enumerate(switches):
        if s.to != "reel" or s.kind == "replay":
            continue
        start = s.t + tune_s
        end = switches[n + 1].t if n + 1 < len(switches) else t1
        if end <= start:
            continue
        card.reels += 1
        card.reel_s += end - start
        # The game the reel stood in for: where we go back to (a reel taken from a visit or a
        # replay still answers to the game whose break it filled).
        back = switches[n + 1].to if n + 1 < len(switches) else None
        home = by_id.get((homes or {}).get(s.t) or "") or by_id.get(back or "") or by_id.get(s.frm or "")
        t = start
        while t < end:
            if home is not None:
                dead, brk, _ = cutgrade._dead_at(home.league, home.wall, home.plays, t - lag)
                card.dead_s += 1.0 if dead else 0.0
                card.commercial_s += 1.0 if brk else 0.0
                card.home_late_s += 0.0 if dead else 1.0
            else:
                card.dead_s += 1.0   # left RedZone / nothing: nothing live was given up
            t += 1.0
        for r in replays:
            prev = None
            plen = cutgrade._plen(r.league)
            for w, p in zip(r.wall, r.plays):
                big = sportsgrade.is_big(_base(r.league), p, prev)
                prev = p
                if not big:
                    continue
                a0, a1 = w + lag - plen, w + lag
                if a1 > s.t and a0 < end:
                    card.big_missed_anywhere += 1
                    if r is home:
                        card.home_big_missed += 1
                        card.missed.append((s.t, r.matchup, str(p.get("text") or "")[:80]))
    return card


def big_in(r, a: float, b: float, lag: float) -> int:
    """Big plays of r that air (screen time) inside [a, b)."""
    out, prev = 0, None
    plen = cutgrade._plen(r.league)
    for w, p in zip(r.wall, r.plays):
        if sportsgrade.is_big(_base(r.league), p, prev) and w + lag > a and w + lag - plen < b:
            out += 1
        prev = p
    return out


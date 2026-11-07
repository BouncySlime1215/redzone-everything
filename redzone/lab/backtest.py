"""Replay a real slate of past games through the scorer and decider, then grade it.

The simulation is honest about time:
  - the app only "sees" a play feed_lag seconds after it happened (wallclock)
  - your screen shows it delay seconds after the app saw it
  - the decider only knows what it has seen, never the future

Report card (what "good" means):
  mid_play_cuts      cut away while a play was on screen       -> must be 0
  big_plays_live     TDs/turnovers you saw live, all games      -> higher is better
  dead_air_pct       time spent watching a break on screen      -> lower is better
  switches_per_hour  observed switch frequency; no arbitrary cap or target
"""
from __future__ import annotations

import json
import math
import random
import zlib
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from redzone.channels import REDZONE_TARGET, redzone_on
from redzone.config import Config
from redzone.decider.decider import REDZONE, Candidate, Decider, FocusMode
from redzone.decider.screen import DelayBook
from redzone.grading import Lags, SwitchLog, idx as _bisect, lag_of  # noqa: F401  [one brain] moved, step 3
from redzone.grading import restart_before
from redzone.models import REAL_BREAKS, League, Phase
from redzone.scoring.drive import DriveScorer
from redzone.scoring.engine import Personal
from redzone.lab import leverage
from redzone.sources import espn

ET = ZoneInfo("America/New_York")


@dataclass
class Replay:
    league: League
    gid: str
    data: dict
    plays: list[dict]
    wall: list[float]
    matchup: str = ""
    _cache: dict = field(default_factory=dict)

    @classmethod
    def load(cls, league: League, path: Path) -> "Replay | None":
        data = json.loads(path.read_text())
        plays = espn.flatten_plays(league, data)
        if not plays:
            return None
        wall, last = [], None
        for p in plays:
            t = espn._ts(p.get("wallclock"))
            t = t if t is not None else (last + 5 if last else None)
            wall.append(t)
            last = t if t is not None else last
        if wall[0] is None:
            return None
        # Make wallclock monotonic (feeds occasionally go backwards by a second).
        for i in range(1, len(wall)):
            wall[i] = max(wall[i], wall[i - 1])
        r = cls(league, path.stem.split("_")[-1], data, plays, wall)
        g = espn.parse_summary(league, data, upto=0, plays=plays)
        r.matchup = g.matchup if g else r.gid
        return r

    def state_at(self, idx: int):
        if idx not in self._cache:
            g = espn.parse_summary(self.league, self.data, upto=idx, plays=self.plays)
            if g:
                # Final records would leak the future; a backtest doesn't get them.
                for t in (g.home, g.away):
                    t.wins = t.losses = t.ties = None
            self._cache[idx] = g
        return self._cache[idx]


@dataclass
class Report:
    switches: list[SwitchLog]
    mid_play_cuts: list[tuple[float, str]]
    big_plays: int
    big_plays_live: int
    my_big_plays: int
    my_big_plays_live: int
    dead_air_s: float
    watched_s: float
    hours: float
    missed: list[tuple[float, str, str]]
    decisions: list = field(default_factory=list)
    sync_honesty: dict = field(default_factory=dict)   # [auto-anchors] honesty_summary of the on-screen delay
    big_plays_caught: int = 0
    crunch: int = 0
    crunch_live: int = 0
    my_crunch: int = 0
    my_crunch_live: int = 0
    dead_causes: dict[str, float] = field(default_factory=dict)  # seconds of dead air by cause
    tuning_blackout_s: float = 0.0  # modelled single-stick tuning time, separate from commercials
    cut: object = None              # cutgrade.CutCard: where each switch left and landed
    # [redzone mode] what a whip-around show is judged by
    behind_live_s: float = 0.0       # time the screen showed a catch-up replay (behind live)
    big_missed_on_replay: int = 0    # big plays that aired (anywhere) while we sat on a replay
    big_seen_10m: int = 0            # big plays seen live or replayed within CATCHUP_S of airing
    my_big_seen_10m: int = 0         # ...of your team's
    big_seen_3m: int = 0             # live or replayed within CATCHUP_3_S (the gate, Nick Nov 4 11:00 AM)
    big_seen_2m: int = 0             # ...within CATCHUP_2_S (stretch)
    my_big_seen_3m: int = 0          # your team's, within CATCHUP_3_S
    big_see_s: list = field(default_factory=list)   # [time to see] per big play: time_to_see()
    crunch_on_replay: int = 0        # crunch plays that aired while a catch-up replay was on screen
    big_from_start: int = 0          # [from the start] big plays on screen from the previous dead ball through the end
    crunch_from_start: int = 0       # ...crunch plays the same way
    my_crunch_from_start: int = 0    # ...your team's crunch plays
    drive_plays: int = 0             # NFL scrimmage plays on drives that scored or reached the red zone
    drive_plays_live: int = 0        # ...of those, watched live start to finish
    drive_plays_other_sport: int = 0  # ...of those, missed while another sport was on screen
    drive_plays_on_redzone: int = 0  # ...of those, aired while the RedZone channel was on (it may have shown them)
    mid_play_cuts_high: list = field(default_factory=list)   # mid-play leaves at a high-leverage moment

    @property
    def summary(self) -> dict:
        return {
            "mid_play_cuts": len(self.mid_play_cuts),
            "mid_play_cuts_high": len(self.mid_play_cuts_high),
            "big_plays_seen": f"{self.big_plays_caught}/{self.big_plays}"
                              f" ({100 * self.big_plays_caught / max(1, self.big_plays):.0f}%)",
            "big_plays_live": f"{self.big_plays_live}/{self.big_plays}"
                              f" ({100 * self.big_plays_live / max(1, self.big_plays):.0f}%)",
            "your_team_big_plays_seen": f"{self.my_big_plays_live}/{self.my_big_plays}",
            "replays": sum(1 for s in self.switches if s.kind == "replay"),
            "crunch_live": f"{self.crunch_live}/{self.crunch}",
            "your_team_crunch_live": f"{self.my_crunch_live}/{self.my_crunch}",
            "dead_air_pct": round(100 * self.dead_air_s / max(1, self.watched_s), 1),
            "tuning_blackout_s": round(self.tuning_blackout_s, 1),
            "tuning_blackout_pct": round(100 * self.tuning_blackout_s / max(1, self.hours * 3600), 1),
            "switches_per_hour": round(len(self.switches) / max(0.1, self.hours), 1),
            "switches": len(self.switches),
            **(self.cut.summary() if self.cut is not None else {}),
            "nfl_drive_live": f"{self.drive_plays_live}/{self.drive_plays}"
                              f" ({100 * self.drive_plays_live / max(1, self.drive_plays):.0f}%)",
            "behind_live_pct": round(100 * self.behind_live_s / max(1, self.hours * 3600), 1),
            "big_missed_on_replay": self.big_missed_on_replay,
            "big_seen_10m": f"{self.big_seen_10m}/{self.big_plays}"
                            f" ({100 * self.big_seen_10m / max(1, self.big_plays):.1f}%)",
            "your_team_big_seen_10m": f"{self.my_big_seen_10m}/{self.my_big_plays}",
            "crunch_on_replay": self.crunch_on_replay,
        }


CATCHUP_S = 600.0   # "seen" for the reel gate: live, or replayed within 10 min of airing (Nick, Nov 4)
# Nick, Nov 4 11:00 AM: the replay window is 2-3 MINUTES max. 3 min is the gate, 2 min the stretch row.
CATCHUP_3_S = 180.0
CATCHUP_2_S = 120.0


# [time to see, Nick Nov 5 12:28 AM] the catch-up window ladder, seconds: 3 min now, then 90 s, 30 s, live. A league
# steps down after holding the tighter window on locked nights for 2 releases in a row (scripts/catchup_ratchet.py).
CATCHUP_LADDER = (180.0, 90.0, 30.0, 0.0)


def time_to_see(on: bool, replayed, key, air: float) -> float | None:
    """Seconds from a big play airing until it was on screen: 0 = live; the catch-up replay's finish less the
    airing; inf = never seen; None = replayed at an unknown time (an old plain set)."""
    if on:
        return 0.0
    if key not in replayed:
        return float("inf")
    at = replayed.get(key) if isinstance(replayed, dict) else None
    return None if at is None else max(0.0, float(at) - air)


def replayed_by(replayed, key, deadline: float) -> bool:
    """Was this play's catch-up replay finished by `deadline`? A plain set (no times) counts any replay."""
    if key not in replayed:
        return False
    at = replayed.get(key) if isinstance(replayed, dict) else None
    return at is None or at <= deadline


def _is_big(p: dict) -> bool:
    t = (espn._get(p, "type", "text") or "").lower()
    return bool(p.get("scoringPlay")) or "interception" in t or "fumble recovery (opponent)" in t \
        or bool(p.get("isTurnover"))


@dataclass
class _Feed:
    """How one game's data reaches the app in a backtest (the realism knobs, per game)."""
    arrive: list[float]                                       # when each play shows up in the feed
    drops: list[tuple[float, float]] = field(default_factory=list)  # windows where the source is silent
    no_wall: set[int] = field(default_factory=set)            # plays delivered without a wallclock
    late: list[int] = field(default_factory=list)             # plays that can arrive out of order
    fresh_at: float = 0.0                                     # last time this game's data was fresh

    def __post_init__(self):
        self.late_set = set(self.late)



SCREEN_LAG_S = 43.0   # run()'s default real life -> screen (feed_lag 8 + delay 35)


STREAM_DELAY_S = 26.0   # a YouTube TV channel at its live edge, behind real life (ESPN, Nov 2 2026)


def feed_lag_knobs(feed_lag: float | None, behind_live: float | None = None,
                   stream_delay: float = STREAM_DELAY_S) -> dict:
    """[switch timing] run() kwargs for a fixed feed lag and/or a time-shift buffer.

    feed_lag     the app learns of each play this long after it happened (default 8)
    behind_live  [time-shift buffer] the stream is held this far behind its live edge, so the screen
                 runs stream_delay + behind_live behind real life. Without it the screen stays where
                 the scorecard always had it (43 s), so only the warning before a play airs changes."""
    if feed_lag is None and behind_live is None:
        return {}
    fl = 8.0 if feed_lag is None else float(feed_lag)
    if not 0 <= fl <= 300:
        raise ValueError(f"feed lag {fl} s is outside 0..300")
    if behind_live is not None and not 0 <= behind_live <= 180:
        raise ValueError(f"behind_live {behind_live} s is outside 0..180")
    if behind_live is None:
        return {"feed_lag": fl, "delay": SCREEN_LAG_S - fl}
    return {"feed_lag": fl, "delay": stream_delay + float(behind_live) - fl, "calibrate_targets": True}


def channel_scenario(replays, targets, feed_lag: float, delay: float, spread: float, history: float,
                     seed: int, t0: float) -> tuple["Lags", list[tuple]]:
    """[per-channel delay] Each game's channel runs its own real delay (around `delay`, sd `spread`,
    clipped to 15..75 s, like the 22..42 s measured on one app on Nov 2 2026), and what earlier nights
    measured of it: (Lags real life -> screen per game, store rows (ts, target, sample, kind))."""
    per, rows = {}, []
    for r in replays:
        rng = random.Random(seed * 7_919 + zlib.crc32(("delay:" + r.gid).encode()))
        true_c = min(75.0, max(15.0, rng.gauss(delay, spread)))
        per[r.gid] = feed_lag + true_c
        target = (targets or {}).get(r.gid, f"youtubetv:{r.gid}")
        u = rng.random()
        if u < history:                     # tapped on earlier nights: tap error ~2 s, night-to-night ~2 s
            for _ in range(rng.randint(1, 3)):
                rows.append((t0 - rng.uniform(1, 5) * 86400, target,
                             feed_lag + true_c + rng.gauss(0, 2) + rng.gauss(0, 2), "tap"))
        elif u < history * 1.5:             # only show changes (looser: the title flips a few s off)
            for _ in range(rng.randint(1, 2)):
                rows.append((t0 - rng.uniform(1, 5) * 86400, target,
                             feed_lag + true_c + rng.gauss(0, 3), "boundary"))
    return Lags(feed_lag + delay, per), rows


def _feed(r: Replay, rng: random.Random, feed_lag: float, step: float, lag_jitter, drop: float,
          late_plays: float, no_wallclock: float, t0: float, t1: float, sampler=None) -> _Feed:
    if sampler is not None:
        # [switch timing] the measured feed: each play's own lag, delivered in order (a poll never
        # hands over a play before an older one it already carries)
        lg = r.league.value
        arrive, prev = [], float("-inf")
        for w in r.wall:
            prev = max(prev, w + sampler.draw(lg, rng))
            arrive.append(prev)
    elif lag_jitter:
        lo, hi = lag_jitter
        arrive, prev = [], float("-inf")
        for w in r.wall:
            prev = max(prev, w + rng.uniform(lo, hi))   # feeds deliver in order, at a varying lag
            arrive.append(prev)
    else:
        arrive = [w + feed_lag for w in r.wall]
    late = []
    if late_plays:
        for i in range(len(arrive)):
            if rng.random() < late_plays:
                arrive[i] += step      # a poll late: later plays can now land before it
                late.append(i)
    drops = []
    if drop:
        t = t0
        while True:
            t += rng.expovariate(drop / 60.0)   # `drop` = silences per game-minute
            if t >= t1:
                break
            dur = rng.uniform(60.0, 120.0)
            drops.append((t, t + dur))
            t += dur
    no_wall = {i for i in range(len(arrive)) if no_wallclock and rng.random() < no_wallclock}
    return _Feed(arrive, drops, no_wall, late)


def _deliver(r: Replay, f: _Feed, i: int, now: float):
    """The state the app would see: plays not yet arrived are missing, some lack a wallclock."""
    g = r.state_at(i)
    if g is None:
        return None
    missing = {j for j in f.late if j <= i and f.arrive[j] > now}
    strip = f.no_wall
    if missing or strip:
        lo = max(0, i - 7)                       # parse_summary keeps the last 8 plays
        ids = {str(r.plays[j].get("id")): j for j in range(lo, i + 1)}

        def fix(p):
            j = ids.get(p.id)
            if j is not None and j in strip:
                return dc_replace(p, wallclock=None, seen_at=None)
            return p
        recent = [fix(p) for p in g.recent_plays if ids.get(p.id) not in missing]
        last = fix(g.last_play) if g.last_play else None
        g = dc_replace(g, recent_plays=recent, last_play=last)
    g.updated_at = f.fresh_at
    return g


# [EXP-601] pre-registered (docs/prereg/exp-601.md): the Nov 4 live press span "tune" (p50 21.61 s, p95 44.74 s) as a
# lognormal clipped to [5, 120] s, and the 1 s resume after the hold. Not tunable: a new value needs a new EXP id.
HS_PREREG = {"tune_mu": math.log(21.61), "tune_sigma": 0.4424, "tune_clip": (5.0, 120.0), "resume": 1.0}
# [EXP-603] pre-registered (docs/prereg/exp-603.md): the Nov 6 overlay scrub (docs/sync/2026-11-06-yttv-scrub-buffer-spec.md,
# codex/resume-redzone a60c9cbd). CENTER, N x LEFT (15 s each), CENTER takes ~10 s over adb; one label read (CENTER,
# LEFT, RIGHT, CENTER) takes ~8 s and adds ~8 s of delay. Measured TNT: N=4 read 67 s behind, 75 s after that read.
# So a scrub with one read airs 15 N + 15 s behind (N=4 -> 75, N=5 -> 90) and is ready keys + read after the tune.
HS_SCRUB = {"keys_s": 10.0, "read_s": 8.0, "step_s": 15.0, "land_s": 15.0}
# [EXP-605] Nick's Nov 7 stick runs (integration/next 8d765bf5): enter_buffer 2/2 at 15 s each; one label read
# costs 7.5 s and adds 7.5 s (31-min ESPN soak: 82, 89, ... 119 s at 5-min reads, no snap). Read once, then trust.
HS_SCRUB_MEASURED = {"keys_s": 15.0, "read_s": 7.5}
HS_ENTRIES = ("hold", "scrub", "scrub_measured")
# [EXP-608] Nick's Nov 7 05:26Z stick run (live Prime POR@LAL, stick .150): MEDIA_PAUSE 60 s then MEDIA_PLAY put the
# picture 67.7 s behind (from 6.6 at the edge, so +61.1 s) and Prime's own timecode held it for the full 4 min checked
# (02:20:54 -> 02:24:44 over 230 s wall). So Prime CAN buffer: pause B s, resume. Cost = the hold time (B) + resume;
# the 1.1 s overshoot is left out (lands within the 3 s sync noise). Rewind (continuous, 214/665 s) is not used.
HS_PRIME_ENTRIES = ("edge", "pause")
HS_NO_BUFFER = ("replay", "resume", "skip")   # catch-up footage needs no hold (as live)


class HiddenStick:
    """[EXP-601] The hidden stick's readiness, as pre-registered in docs/prereg/exp-601.md (grader, frozen by the
    integrator). One preload at a time, its target picked by the real standby planner from sim state. A new
    target is tuned after a lognormal tune draw and held (ready) B * hold_overrun + resume s later; Prime holds
    nothing (B = 0). After a switch the stick we left holds the game we left, ready at once.

    A switch to a target that isn't held:
      wait       (arm A, report only) the cut waits until it is (a preload started now if it's another
                 target); the old game stays on screen; the picture airs buffered.
      live_edge  (arm B, the verdict arm) a target already tuned airs now; otherwise the hidden stick tunes
                 it (a fresh tune draw) with the old game on screen. Either way it airs at the live edge:
                 offset -= B, so Rule #1 is graded where the picture really is."""

    def __init__(self, arm: str, behind: float, hold_overrun: float, cfg, clock, seed: int, my_teams: dict,
                 entry: str = "hold", prime_entry: str = "edge"):
        if arm not in ("wait", "live_edge"):
            raise ValueError(f"hidden_stick must be None, 'wait' or 'live_edge', not {arm!r}")
        if entry not in HS_ENTRIES:
            raise ValueError(f"hidden-stick entry must be one of {HS_ENTRIES}, not {entry!r}")
        if entry in ("scrub", "scrub_measured"):
            n = (behind - HS_SCRUB["land_s"]) / HS_SCRUB["step_s"]
            if n < 1 or abs(n - round(n)) > 1e-9:
                raise ValueError(f"[EXP-603] a scrub lands at 15 N + 15 s behind live (75, 90, ...), not {behind:g}")
        if prime_entry not in HS_PRIME_ENTRIES:
            raise ValueError(f"prime entry must be one of {HS_PRIME_ENTRIES}, not {prime_entry!r}")
        self.entry = entry
        self.prime_entry = prime_entry   # [EXP-608] edge: Prime airs at its live edge (B = 0); pause: holds B
        self.true_sync = False         # [EXP-605] a re-sync reading sees an edge-aired picture at the edge
        self.recheck = None            # [EXP-604] (by_id, lag_belief, feed_lag, tune_s) when the re-check is on
        self.rechecked = self.recheck_late = self.recheck_none = 0
        self.recheck_s: list[float] = []
        from redzone import standby
        self.arm, self.B, self.overrun, self.cfg, self.my_teams = arm, behind, hold_overrun, cfg, my_teams
        self.rng = random.Random(seed * 7_000_003 + 601)
        self.planner = standby.Planner(cfg, clock=clock)
        self._standby = standby
        self.clock = clock
        self.gid: str | None = None
        self.target: str | None = None
        self.tuned_at = self.ready_at = 0.0
        self.n = self.buffered = self.prime = self.preloads = self.retunes = 0
        self.waits: list[float] = []
        self.at_edge: set[str] = set()      # games last aired unbuffered (live edge): the stick we leave must hold first
        self.prime_t: list[float] = []      # decision times of switches to Prime (B = 0)
        self.unbuffered_t: list[float] = []  # decision times of every switch that aired at the live edge

    def tune(self) -> float:
        lo, hi = HS_PREREG["tune_clip"]
        return min(hi, max(lo, self.rng.lognormvariate(HS_PREREG["tune_mu"], HS_PREREG["tune_sigma"])))

    def hold_of(self, target: str | None) -> float:
        """Seconds from tuned to ready: Prime holds nothing; a hold pauses B * overrun s (+ resume); a scrub
        [EXP-603] costs its keys plus one label read, whatever B."""
        prime = (target or "").startswith("prime:")
        if prime and self.prime_entry == "pause":
            return self.B + HS_PREREG["resume"]   # [EXP-608] MEDIA_PAUSE B s, MEDIA_PLAY
        if self.entry == "scrub":
            return 0.0 if prime else HS_SCRUB["keys_s"] + HS_SCRUB["read_s"]
        if self.entry == "scrub_measured":
            return 0.0 if prime else HS_SCRUB_MEASURED["keys_s"] + HS_SCRUB_MEASURED["read_s"]
        return (0.0 if prime else self.B * self.overrun) + HS_PREREG["resume"]   # EXP-601 exactly as frozen

    def load(self, gid: str, target: str, now: float) -> None:
        self.gid, self.target = gid, target
        self.tuned_at = now + self.tune()
        self.ready_at = self.tuned_at + self.hold_of(target)
        self.preloads += 1

    def plan(self, cands: dict, dec, decision, states: dict) -> None:
        home = dec.home_break_visit if dec.home_break_visit != dec.current else None
        opts = self._standby.options(cands, current=dec.current, home=home,
                                     back_to=dec.stack[-1] if dec.stack else None, games=states,
                                     my_teams=self.my_teams, want=getattr(decision, "want", None),
                                     return_bonus=float(self.cfg.get("decider.switch_margin", 10)))
        pick = self.planner.plan(opts, (self.target, None))
        if pick is not None and pick.target != self.target:
            self.load(pick.game_id, pick.target, self.clock.t)

    @staticmethod
    def keep_order(switches: list, watching: list, n_sw: int) -> None:
        """A cut can't air before the one still waiting ahead of it: it waits behind it."""
        if len(switches) > n_sw and len(watching) >= 2 and watching[-1][0] < watching[-2][0]:
            d = watching[-2][0] - watching[-1][0]
            switches[-1].leave_dt += d
            switches[-1].arrive_dt += d
            watching[-1] = (watching[-2][0], watching[-1][1])

    def on_switch(self, sw, watching: list, targets: dict | None) -> None:
        if sw.kind in HS_NO_BUFFER:
            return
        to = (targets or {}).get(sw.to, f"youtubetv:{sw.to}")
        cut = sw.t + (sw.leave_dt or 0.0)
        self.n += 1
        prime = to.startswith("prime:")
        self.prime += prime
        if prime:
            self.prime_t.append(sw.t)
        same = sw.to == self.gid
        ready = same and cut >= self.ready_at
        wait = 0.0
        live_edge = prime and self.prime_entry == "edge"
        if not ready:
            if self.arm == "wait":
                if not same:
                    self.load(sw.to, to, cut)
                    self.retunes += 1
                wait = max(0.0, self.ready_at - cut)
            else:
                live_edge = True
                if not (same and cut >= self.tuned_at):
                    wait = self.tune()   # the hidden stick tunes it at the live edge now
                    self.retunes += 1
        if wait > 0:
            sw.leave_dt += wait
            sw.arrive_dt += wait
            at, what = watching[-1]
            watching[-1] = (at + wait, what)
            self.waits.append(wait)
            if self.recheck is not None and not live_edge:
                more = self.safe_after(sw, cut + wait)
                if more > 0:
                    sw.leave_dt += more
                    sw.arrive_dt += more
                    at, what = watching[-1]
                    watching[-1] = (at + more, what)
                    wait += more
        if live_edge:
            sw.offset -= self.B
            if self.true_sync:
                sw.hs_edge = self.B
            self.unbuffered_t.append(sw.t)
            self.at_edge.add(sw.to)
        else:
            self.buffered += 1
            self.at_edge.discard(sw.to)
        # the stick we left holds the game we left, ready at once -- unless that game was airing at the live edge:
        # then it is tuned but must hold B first (control._buffer_watch rebuilds the hold off screen)
        left = sw.frm
        self.gid, self.target = left, ((targets or {}).get(left, f"youtubetv:{left}") if left else None)
        self.tuned_at = self.ready_at = cut + wait
        if left in self.at_edge:
            self.ready_at += self.hold_of(self.target)

    def safe_after(self, sw, cut: float) -> float:
        """[EXP-604] The live path (#378) scrubs with the old game on screen, then waits for a safe moment
        (_wait_safe, may_cut) before the flip. Re-check from `cut` in 1 s steps: the first moment the target's
        delayed picture is at a dead ball, judged as the app sees it (only plays its feed delivered by then; the
        believed lag, not the true one). Extra seconds to that moment; past 25 s (MAX_SAFE_WAIT_S) live drops the
        switch and the decider picks again, modelled as waiting on (counted late); none within 145 s -> 0."""
        from redzone.grading import dead_at
        by_id, lag_belief, feed_lag, tune_s = self.recheck
        dst = by_id.get(sw.to or "")
        if dst is None or not dst.wall:
            return 0.0
        off = getattr(sw, "offset", 0.0) or 0.0
        base = sw.t + tune_s + (sw.arrive_dt or 0.0) - lag_of(lag_belief, dst.gid) - off
        for e in range(0, 146):
            now = cut + e
            n = sum(1 for w in dst.wall if w <= now - feed_lag)   # plays the app has by now
            st = base + e
            if st < dst.wall[0]:
                return float(e)   # before the game started on screen: nothing to judge
            if n < len(dst.wall) and (n == 0 or st >= dst.wall[n - 1]):
                continue   # the app hasn't seen what's on screen yet: no safe call (conservative)
            if dead_at(dst.league, dst.wall[:n], dst.plays[:n], st)[0]:
                self.rechecked += 1
                self.recheck_late += e > 25
                if e:
                    self.recheck_s.append(float(e))
                return float(e)
        self.recheck_none += 1
        return 0.0

    def card(self) -> dict:
        return {"arm": self.arm, "entry": self.entry, **({"prime_entry": "pause"} if self.prime_entry == "pause" else {}),
                "behind_live_s": self.B, "hold_overrun": self.overrun, "switches": self.n,
                "buffered": self.buffered, "buffered_pct": round(100.0 * self.buffered / self.n, 1) if self.n else None,
                "waits": len(self.waits), "wait_s": round(sum(self.waits), 1),
                "wait_p50_s": round(sorted(self.waits)[len(self.waits) // 2], 1) if self.waits else 0.0,
                "prime_switches": self.prime, "prime_t": self.prime_t, "unbuffered_t": self.unbuffered_t,
                "preloads": self.preloads, "retunes": self.retunes,
                **({"recheck": {"checked": self.rechecked, "moved": len(self.recheck_s),
                                "moved_s": round(sum(self.recheck_s), 1), "late_over_25s": self.recheck_late,
                                "none_in_145s": self.recheck_none}} if self.recheck else {})}


def run(replays: list[Replay], cfg: Config, my_teams: set[str], *, feed_lag: float = 8.0,
        delay: float = 35.0, play_len: float = 7.0, tune_s: float = 5.0, step: float | None = None,
        focus: str | None = None, start: float | None = None, end: float | None = None,
        targets: dict[str, str] | None = None,
        true_delay: float | None = None, sigma: float = 3.0, lag_jitter: tuple[float, float] | None = None,
        drop: float = 0.0, late_plays: float = 0.0, no_wallclock: float = 0.0, seed: int = 0,
        channel_spread: float = 0.0, history: float = 0.0, delay_model: str | None = None,
        resync: bool = False, anchors: bool = False, tune_jitter_s: float = 0.0, flip_dist: bool = False, positions: bool = False,
        pretune_dist: bool = False, real_seek: bool = False,
        feed_lag_model: str | None = None,
        calibrate_targets: bool = False,
        hidden_stick: str | None = None, hold_overrun: float = 1.0, behind_live_s: float | None = None,
        hs_entry: str = "hold", hs_recheck: bool = False, hs_prime_edge: bool = False,
        hs_resync_prime_edge: bool = False, hs_prime_pause: bool = False,
        on_tick: Callable | None = None) -> Report:
    """Realism knobs (all off by default, so the scorecard is unchanged):
      targets       game_id -> tuner target (default youtubetv:<id>), e.g. Prime for League Pass
      true_delay    the stream's real delay; `delay` is only the app's estimate of it
      sigma         the uncertainty the app states for that estimate (seconds)
      lag_jitter    (lo, hi): each play reaches the feed lo..hi s after it happened (seeded)
      drop          source goes silent 60-120 s, this many times per game-minute on average
      late_plays    fraction of plays that arrive one poll late (so out of order)
      no_wallclock  fraction of plays delivered without a wallclock
      channel_spread  [per-channel delay] each game's channel really runs its own delay, drawn around
                    `delay` with this sd (seeded). The app is no longer handed a calibrated delay: it
                    starts from the config default and learns only from `history`
      history       [per-channel delay] fraction of channels measured on earlier nights (1-3 taps);
                    half as many again have only show-change samples; the rest were never measured
      delay_model   DelayBook model ("channel" | "legacy"); default from cfg delay.model
      resync        [auto sync] model live re-sync: the per-channel Kalman filter (sync.py) is attached
                    as live attaches it, every tune re-syncs it, and the channel on screen gives it one
                    screen-delay reading per poll from tune_s after the tune (noise sd RESYNC_SD_S,
                    seeded). Channels off screen keep their last figure and age the way live ages them
                    (sigma floored at delay.sync.min_sigma_s, doubt growing to the settled cap, stale
                    after 30 min). Without it, every channel keeps the kickoff calibration.
      anchors       [auto-anchors, needs sync.break_anchor.enabled] the filter is attached as live
                    attaches it and fed ONLY what live can feed it today: each ad break on the screen of
                    the game you're watching, matched to the feed's dead ball (engine._break_event). The
                    true whistle -> ad-on-screen gap is drawn per break (sync.break_anchor.true_gap_s /
                    true_gap_sd_s, default = the config prior: an assumption until the Wednesday probe
                    data). Every on-screen second is graded for honesty (Report.sync_honesty).
      positions     [yttv position] with anchors: the stick on screen reports its playback position
      flip_dist     [live lab] each picture comes up with the measured TV flip's spread around tune_s
      pretune_dist  [live lab] ...or a cold hidden-stick pre-tune (4K Plus / 4K 2023 in turn) + flip + settle
      real_seek     [seek model] an entry rewind is done the way YouTube TV really seeks (YTTV_SEEK: 15 s
                    steps, first press swallowed, a 2-4 s stall) from the presses control.py SENDS for
                    the configured replay.seek_* model; a pause cue (decision.cue) lands where planned
                    but flips later by the hidden stick's cue time (seek_cue below)
      tune_jitter_s [auto-anchors] each tune lands U(0, S) s behind the live edge, unknown to the app (a
                    YouTube TV tune, redzone/lab/syncsim.py: 0-6 s). Graded as a real offset of that
                    visit, so the delay the app believes is wrong by it until something re-syncs it.
      feed_lag_model  [switch timing] "measured": each play reaches the app after a lag drawn from the
                    league's MEASURED detection lag (redzone/lab/feed_lag_profile.json, the flight
                    recorder's espn_summary rows; seeded, in order), instead of the fixed feed_lag.
                    The app still believes feed_lag + delay is real life -> screen.
      hidden_stick  [EXP-601] model the hidden stick's readiness (HiddenStick below; docs/prereg/exp-601.md):
                    None = off (today's grader, byte for byte), "wait" (arm A, report only) or "live_edge"
                    (arm B, the verdict arm). hold_overrun scales the B s hold; behind_live_s is B for this
                    run (default: the config's timing.behind_live_s for the league, else 0)
      on_tick       optional read-only lab observer (historical time, decider), before selection
    """
    step = step or cfg.get("polling.live_s", 10)
    clock = _Clock()
    lag_total = feed_lag + delay   # real life -> your screen, as the app believes it
    lag_true = feed_lag + (delay if true_delay is None else true_delay)  # ...and as it really is
    model = delay_model or cfg.get("delay.model", "channel")
    t0 = start or min(r.wall[0] for r in replays) - 60
    t1 = end or max(r.wall[-1] for r in replays) + 60
    history_rows = []
    if channel_spread > 0:
        lag_true, history_rows = channel_scenario(replays, targets, feed_lag, lag_true - feed_lag, channel_spread,
                                                  history, seed, t0)
        sigma_book = float(cfg.get("delay.sigma_default", 15))
    else:
        sigma_book = sigma
    # calibrate_targets: every app the night's games are on starts from the same believed delay (the
    # simulated screen runs one delay for all, lag_true). Off, only YouTube TV gets it and a Prime/ESPN
    # game is planned with the config default (40 s): 3 s off on today's scorecard, 46 s off with a
    # 60 s time-shift buffer. On only with --behind-live for now: turning it on for today's NBA card
    # moves it a lot (big seen 92.0 -> 77.0) for a reason not yet understood (docs/timing.md).
    apps = {"youtubetv"} | ({t.split(":", 1)[0] for t in (targets or {}).values()} if calibrate_targets else set())
    # [EXP-605] hs_prime_edge: Prime can't scrub, so the app plans Prime at its live edge (belief lag_total - B);
    # its picture airs at the edge too (HiddenStick offsets it), so belief and truth agree for Prime.
    if hs_prime_pause and (hs_prime_edge or hs_resync_prime_edge):
        raise ValueError("[EXP-608] hs_prime_pause buffers Prime like YouTube TV: it can't also plan Prime at its edge")
    prime_b = float(behind_live_s or 0.0) if (hs_prime_edge and hidden_stick is not None) else 0.0
    delays = DelayBook({a: lag_total - (prime_b if a == "prime" else 0.0) for a in sorted(apps)},
                       sigma_default=sigma_book,
                       clock=clock, model=model)
    scorer = DriveScorer(cfg, clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    dec.entry_cost = lambda target: tune_s   # [cut timing] this simulator's picture is up tune_s after a switch
    me = Personal(my_teams={lg: set(my_teams) for lg in League})
    if focus:
        dec.user.focus_game, dec.user.focus_mode = focus, FocusMode.FOCUS

    clock.t = t0
    if channel_spread > 0:
        delays.load(history_rows)
    else:
        for r in replays:
            d = delays.get((targets or {}).get(r.gid, f"youtubetv:{r.gid}") if calibrate_targets
                           else f"youtubetv:{r.gid}")
            d.calibrated_at = t0
            d.sigma = sigma
    sync = None
    anchors = anchors and bool(cfg.get("sync.break_anchor.enabled", False))

    def _pin_edge(gid: str) -> float:
        """[EXP-607] With hs_resync_prime_edge, the re-sync prior of a Prime target is its live edge, as the
        app's own book starts it (EXP-605 prime_b); off, every pin is lag_total (EXP-605 byte for byte)."""
        if not hs_resync_prime_edge:
            return 0.0
        tg = (targets or {}).get(gid, f"youtubetv:{gid}") if calibrate_targets else f"youtubetv:{gid}"
        return prime_b if tg.startswith("prime:") else 0.0
    if resync or anchors:
        sync = _attach_resync(delays, cfg, pinned=None if channel_spread > 0 else
                              {((targets or {}).get(r.gid, f"youtubetv:{r.gid}") if calibrate_targets
                                                          else f"youtubetv:{r.gid}"): (lag_total - _pin_edge(r.gid), sigma, t0)
                                                         for r in replays})
        tgt_of = {r.gid: (targets or {}).get(r.gid, f"youtubetv:{r.gid}") for r in replays}
        noise = random.Random(seed * 7_000_003 + 17)
        breaks = ad_breaks(replays, lag_true, cfg, random.Random(seed * 9_000_011 + 5)) if anchors else {}
        if anchors and cfg.get("delay.self_calibrate.enabled", False):
            delays.attach_cal(noise_cal(cfg, replays[0].league if replays else None))
    jitter_rng = random.Random(seed * 5_000_011 + 3)
    flip_rng = random.Random(seed * 5_000_011 + 7)   # its own stream: the tune jitter draws stay the same
    seek_rng = random.Random(seed * 5_000_011 + 13)  # [seek model] stalls and pause errors, own stream
    honesty: list[tuple[float, float]] = []   # (error of the delay used, its sigma), each on-screen tick
    stick = None
    if hidden_stick is not None:
        if behind_live_s is None and replays:
            from redzone.lab.evaldata import config_behind_live
            behind_live_s = config_behind_live(cfg, replays[0].league.value)
        stick = HiddenStick(hidden_stick, float(behind_live_s or 0.0), hold_overrun, cfg, clock, seed,
                            {lg: set(my_teams) for lg in League}, entry=hs_entry,
                            prime_entry="pause" if hs_prime_pause else "edge")
        stick.true_sync = hs_prime_edge
        if hs_recheck:
            if hidden_stick != "wait":
                raise ValueError("[EXP-604] hs_recheck models the wait arm's post-scrub re-check only")
            known = feed_lag
            if feed_lag_model == "measured" and replays:   # plays reach the app late: judge on the league's p95
                known = max(feed_lag, float((cfg.get("timing.behind_live_s.adaptive.feed_lag_p95_s") or {})
                                            .get(replays[0].league.value, feed_lag)))
            stick.recheck = ({r.gid: r for r in replays}, lag_total, known, tune_s)
    sampler = None
    if feed_lag_model == "measured":
        from redzone.lab.timing import LagSampler, sources_in_use
        sampler = LagSampler(use=sources_in_use(cfg))
    elif feed_lag_model not in (None, "fixed"):
        raise ValueError(f"feed_lag_model must be 'measured' or 'fixed', not {feed_lag_model!r}")
    feeds = {r.gid: _feed(r, random.Random(seed * 1_000_003 + zlib.crc32(r.gid.encode())), feed_lag, step,
                          lag_jitter, drop, late_plays, no_wallclock, t0, t1, sampler) for r in replays}

    switches: list[SwitchLog] = []
    decisions = []
    replayed: dict[tuple[str, int], float] = {}   # (game, play) -> when its replay finished
    replay_windows: list[tuple[str, int, float, float]] = []
    watching: list[tuple[float, str | None]] = []  # (time cut happens on screen, game)
    idx = {r.gid: -1 for r in replays}
    clock.t = t0
    rz_ok = bool(cfg.get("sunday_ticket", False))
    while clock.t <= t1:
        cands = {}
        states = {}
        for r in replays:
            f = feeds[r.gid]
            silent = any(a <= clock.t < b for a, b in f.drops)
            i = idx[r.gid]
            if not silent:
                f.fresh_at = clock.t
                # Newest play that has arrived. A late play doesn't hold back the ones after it
                # (out-of-order delivery); _deliver leaves it out until it lands.
                k = i + 1
                while k < len(r.plays):
                    if f.arrive[k] <= clock.t:
                        i = k
                    elif k not in f.late_set:
                        break
                    k += 1
            idx[r.gid] = i
            if i < 0:
                continue
            g = _deliver(r, f, i, f.fresh_at)
            if g is None:
                continue
            # parse_summary already stamps plays with their real wallclock.
            dec.timeline(r.gid, r.league).observe(g)
            states[r.gid] = g
            if g.phase != Phase.LIVE:
                continue
            cands[r.gid] = Candidate(scorer.score(g, me), (targets or {}).get(r.gid, f"youtubetv:{r.gid}"),
                                     stale=clock.t - f.fresh_at > 60)
        before = dec.current
        if on_tick is not None:
            on_tick(clock.t, dec)
        decision = dec.decide(cands, REDZONE_TARGET if rz_ok and redzone_on(states.values()) else None)
        decisions.append(decision)
        n_sw = len(switches)
        _record(decision, before, clock.t, switches, watching, replayed)
        if stick is not None:
            stick.keep_order(switches, watching, n_sw)
        _apply_defer(decision, switches, watching, n_sw, step)
        if stick is not None:
            if len(switches) > n_sw:
                stick.on_switch(switches[-1], watching, targets)
            stick.plan(cands, dec, decision, states)
        if tune_jitter_s > 0 and len(switches) > n_sw and switches[-1].kind != "replay":
            j = jitter_rng.uniform(0.0, tune_jitter_s)
            switches[-1].offset += j
            switches[-1].jitter = j
        if (flip_dist or pretune_dist) and len(switches) > n_sw and switches[-1].kind != "replay":
            # [live lab] the TV flip's measured spread around its median (CEC off, 30 flips, Nov 4)
            from redzone.contracts.switching import FLIP_PRIOR, PRETUNE_PRIOR_S, sample_prior
            flip = sample_prior(FLIP_PRIOR, flip_rng)
            if pretune_dist:
                # every switch a cold pre-tune on the hidden stick, the two sticks taking turns
                # (pessimistic: a hot standby is a flip alone): pre-tune + flip + HDMI settle vs tune_s
                stick = list(PRETUNE_PRIOR_S.values())[len(switches) % len(PRETUNE_PRIOR_S)]
                switches[-1].arrive_dt = sample_prior(stick, flip_rng) + flip + float(cfg.get("two_stick.input_settle_s")) - tune_s
            else:
                switches[-1].arrive_dt = flip - FLIP_PRIOR["median"]
        if real_seek and len(switches) > n_sw and switches[-1].kind not in ("replay", "resume", "skip"):
            seek_cue(switches[-1], decision, cfg, seek_rng)
        if sync is not None and resync:
            _resync_tick(delays, sync, decision, switches, tgt_of, lag_true, tune_s, clock.t, noise)
        elif sync is not None:
            _anchor_tick(delays, sync, decision, switches, tgt_of, breaks, cfg, tune_s, clock.t, step)
            if positions:
                _position_tick(sync, switches, tgt_of, lag_true, delays, tune_s, clock.t)
        _honesty_tick(delays, switches, targets, lag_true, tune_s, clock.t, honesty)
        if decision.action == "replay" and decision.replay and getattr(dec, "replay_until", None) is not None:
            replay_windows.append((decision.target, round(decision.replay.t), clock.t, dec.replay_until))
        _credit_completed_replays(replay_windows, watching, clock.t, replayed)
        clock.t += step

    _credit_completed_replays(replay_windows, watching, t1, replayed)
    rep = _grade(replays, switches, watching, my_teams, lag_true, play_len, tune_s, t0, t1, replayed)
    rep.decisions = decisions
    rep.replayed, rep.t0, rep.t1, rep.lag_total = replayed, t0, t1, lag_total
    rep.lag_true = lag_true
    rep.sync_honesty = honesty_summary(honesty)
    if stick is not None:
        rep.hidden_stick = stick.card()
    return rep


RESYNC_SD_S = 1.5   # one screen-delay reading (the score-bug clock's sd; positions are tighter)


def _attach_resync(delays: DelayBook, cfg: Config, pinned: dict | None):
    """Attach the live filter. In the pinned scenario (no channel_spread) the kickoff calibration is
    the predictor's own figure, so a refresh (the filter dropping back after a tune) restores it."""
    if pinned:
        own = delays._refresh_model

        def refresh_model(target: str) -> None:
            own(target)
            if target in pinned:
                d = delays.book[target]
                mu, sig, at = pinned[target]
                d.mu, d.sigma, d.calibrated_at, d.drift_cap = mu + d.behind, sig, at, 6.0
        delays._refresh_model = refresh_model
        for target in pinned:
            delays._refresh(target)
    return delays.attach_sync(min_sigma_s=float(cfg.get("delay.sync.min_sigma_s", 4)))


def _resync_tick(delays: DelayBook, sync, decision, switches: list, tgt_of: dict, lag_true,
                 tune_s: float, now: float, noise: random.Random) -> None:
    """A tune re-syncs its channel; the channel on screen gives one reading per poll once its
    picture is up. Replays and the RedZone channel give none (not a game's own stream)."""
    if decision.action == "switch" and decision.target in tgt_of and switches and switches[-1].t == now:
        delays.tuned(tgt_of[decision.target])
    last = switches[-1] if switches else None
    if last is None or last.kind == "replay" or last.to not in tgt_of or now - last.t < tune_s:
        return
    target = tgt_of[last.to]
    d = delays.get(target)
    edge = getattr(last, "hs_edge", 0.0) or 0.0   # [EXP-605] this picture aired at its live edge, B s sooner
    sync.measure(target, lag_of(lag_true, last.to) - edge + d.behind + noise.gauss(0, RESYNC_SD_S),
                 "clock", t=now, sd=RESYNC_SD_S)


# [auto-anchors] Plays the broadcast follows with an ad break. NHL: ESPN says only "Stoppage", so the TV
# timeouts are the first stoppage after 6:00, 10:00 and 14:00 have been played in a period.
_AD_BREAK = ("official timeout", "full timeout", "end period", "period end", "end of half", "two-minute warning",
             "end of regulation", "halftime")
_NHL_TV_MARKS = (14 * 60, 10 * 60, 6 * 60)   # seconds LEFT in a 20:00 period


def ad_breaks(replays, lag_true, cfg, rng: random.Random) -> dict[str, list[tuple[float, float]]]:
    """game -> [(feed wallclock of the dead ball, true screen time the ad comes up)], the latter without
    the time-shift buffer (added at match time). The gap is drawn per break."""
    mu = float(cfg.get("sync.break_anchor.true_gap_s", cfg.get("sync.break_anchor.start_bias_s", 15.0)))
    sd = float(cfg.get("sync.break_anchor.true_gap_sd_s", cfg.get("sync.break_anchor.bias_sd_s", 10.0)))
    out: dict[str, list[tuple[float, float]]] = {}
    for r in replays:
        rows, done = [], set()
        for w, p in zip(r.wall, r.plays):
            t = (espn._get(p, "type", "text") or "").lower()
            hit = any(k in t for k in _AD_BREAK)
            if not hit and r.league == League.NHL and t == "stoppage":
                per = espn._int(espn._get(p, "period", "number")) or 0
                clk = espn._clock_s(espn._get(p, "clock", "displayValue"))
                for m in _NHL_TV_MARKS:
                    if clk is not None and clk <= m and (per, m) not in done:
                        done.update((per, mm) for mm in _NHL_TV_MARKS if mm >= m)
                        hit = True
                        break
            if hit and w:
                gap = min(90.0, max(2.0, rng.gauss(mu, sd)))
                rows.append((w, w + lag_of(lag_true, r.gid) + gap))
        out[r.gid] = rows
    return out


_CALS: dict = {}   # [self-calibrating doubt] league -> NoiseCal, kept across the nights of one run (live: across nights)


def noise_cal(cfg, league):
    """The run's NoiseCal for this league: nights are run in date order, so a night only ever starts from
    earlier nights' residuals (forward-only, like live)."""
    from redzone.decider.screen import NoiseCal
    key = (getattr(league, "value", league), int(cfg.get("delay.self_calibrate.min_n", 20)),
           float(cfg.get("delay.self_calibrate.min_sigma_s", 2.0)))
    if key not in _CALS:
        _CALS[key] = NoiseCal(min_n=key[1], min_sigma_s=key[2])
    return _CALS[key]


def _anchor_tick(delays: DelayBook, sync, decision, switches: list, tgt_of: dict, breaks: dict, cfg,
                 tune_s: float, now: float, step: float) -> None:
    """A tune re-syncs its channel (as live); an ad break that comes up on the screen of the game being
    watched in this tick gives one anchor, matched and weighted the way engine._break_event does."""
    if decision.action == "switch" and decision.target in tgt_of and switches and switches[-1].t == now:
        delays.tuned(tgt_of[decision.target])
    last = switches[-1] if switches else None
    if last is None or last.kind == "replay" or last.to not in tgt_of:
        return
    target = tgt_of[last.to]
    d = delays.get(target)
    bias = float(cfg.get("sync.break_anchor.start_bias_s", 15.0))
    bias_sd = float(cfg.get("sync.break_anchor.bias_sd_s", 10.0))
    for w, ts in breaks.get(last.to, ()):
        ts += d.behind + last.jitter
        if now - step < ts <= now and ts - last.t >= tune_s:
            delays.anchor(target, ts - w - bias, "break", ts, (1.0 + bias_sd * bias_sd) ** 0.5)


def _position_tick(sync, switches: list, tgt_of: dict, lag_true, delays: DelayBook, tune_s: float,
                   now: float) -> None:
    """[yttv position] The stick on screen reports its playback position each tick (YouTube TV on the
    4K Plus, live lab Nov 4). It is a per-TUNE clock: every fresh tune starts near the same position
    (46,790 s) whatever the hour, so a read says nothing about how far behind live the tune landed. It
    only moves the delay when the stream itself moves under the same tune (a hold, a stall). Before
    Nov 4 the sim read position = wall - channel origin - true delay, which handed the filter each
    tune's unknown landing (tune_jitter) for free. Only YouTube TV channels report it."""
    last = switches[-1] if switches else None
    if last is None or last.kind == "replay" or last.to not in tgt_of or now - last.t < tune_s:
        return
    target = tgt_of[last.to]
    if not str(target).startswith("youtubetv"):
        return
    true = lag_of(lag_true, last.to) + delays.get(target).behind + last.jitter
    if last.pos_true0 is None:
        last.pos_true0 = true
    # Every fresh tune starts at the same position (46,790 s in the lab); any shared origin works, so 0.
    sync.position(target, (now - last.t) - (true - last.pos_true0), t=now)


def _honesty_tick(delays: DelayBook, switches: list, targets, lag_true, tune_s: float, now: float,
                  out: list) -> None:
    """The game on screen (picture up, not a replay): the delay the app uses vs the true one."""
    last = switches[-1] if switches else None
    if last is None or last.kind == "replay" or not last.to or now - last.t < tune_s or last.to == REDZONE:
        return
    d = delays.get((targets or {}).get(last.to, f"youtubetv:{last.to}"))
    out.append((d.mu - (lag_of(lag_true, last.to) + d.behind + last.jitter), d.current_sigma(now)))


def honesty_summary(rows: list[tuple[float, float]]) -> dict:
    """Is the stated doubt honest (true error within 2 sigma >= 95% of on-screen ticks) and small?"""
    if not rows:
        return {"n": 0}
    n = len(rows)
    err = sorted(abs(e) for e, _ in rows)
    sig = sorted(s for _, s in rows)
    return {"n": n, "within_2sigma": round(sum(abs(e) <= 2 * s for e, s in rows) / n, 4),
            "sigma_le_8": round(sum(s <= 8 for _, s in rows) / n, 4),
            "sigma_le_3": round(sum(s <= 3 for _, s in rows) / n, 4),
            "sigma_p50": round(sig[n // 2], 2), "err_p50": round(err[n // 2], 2),
            "err_p95": round(err[min(n - 1, int(0.95 * n))], 2)}


def seek_cue(sw: "SwitchLog", decision, cfg, rng) -> None:
    """[seek model] Re-land an entry rewind the way the stick really does it (live lab, Nov 4).

    seek (on screen, after the flip): control.py sends seek_model(cfg).sent(r) presses; YTTV_SEEK
      says where that lands (15 s a press, the first swallowed, + a 2-4 s stall). The live edge shows
      for the presses' time first (sw.flash).
    pause (decision.cue == "pause"): on the hidden stick seek PAST the dead ball, play up to it, pause,
      then flip and play. It lands where planned (+/- the pause error) whatever the flip costs, but the
      flip (and leaving the old game) comes later by the cue time."""
    from redzone.contracts.switching import YTTV_SEEK, seek_model
    r = float(getattr(decision, "rewind_s", 0.0) or 0.0)
    if r <= 0:
        return
    t = YTTV_SEEK
    press, keys, perr = (float(cfg.get(k)) for k in ("replay.seek_press_s", "replay.pause_keys_s", "replay.pause_err_s"))
    stall = rng.uniform(t.stall_lo, t.stall_hi)
    if getattr(decision, "cue", "") == "pause":
        eff = max(1, math.ceil((r + perr * 2 - t.stall_lo) / t.step - 1e-9))
        over = eff * t.step + stall - r                       # played forward up to the dead ball
        cue_t = press * (eff + t.swallowed) + stall + over + keys
        err = max(-2 * perr, min(2 * perr, rng.gauss(0.0, perr)))
        # paused content: the flip's own spread and the fresh tune's landing don't move it
        sw.offset = r + cue_t + sw.arrive_dt + err
        sw.jitter = 0.0
        sw.arrive_dt += cue_t
        sw.leave_dt = cue_t
        return
    sent = seek_model(cfg).sent(r)
    if getattr(decision, "cue", "") == "hidden":
        # seek on the hidden stick, then flip: no live glimpse, the flip waits for presses + stall
        cue_t = press * sent + keys + stall
        sw.offset = sw.jitter + t.landed(sent, stall)
        sw.arrive_dt += cue_t
        sw.leave_dt = cue_t
        return
    sw.offset = sw.jitter + t.landed(sent, stall)
    sw.flash = press * sent + keys if sent else 0.0


def _apply_defer(decision, switches: list[SwitchLog], watching: list[tuple[float, str | None]], n_sw: int,
                 step: float) -> None:
    """[EXP-803 A1] A switch decided now but cut defer_s later: the OLD game stays on screen until then, for
    every line (watching drives big/crunch live and dead air; leave_dt / arrive_dt drive cutgrade). Capped at
    one step, so the next decision (a step later) always sorts after the deferred cut and watching stays in
    time order."""
    defer = min(float(getattr(decision, "defer_s", 0.0) or 0.0), step)
    if defer <= 0 or len(switches) <= n_sw:
        return
    switches[-1].leave_dt += defer
    switches[-1].arrive_dt += defer
    at, what = watching[-1]
    watching[-1] = (at + defer, what)
    assert len(watching) < 2 or watching[-2][0] <= watching[-1][0], "watching out of time order"


def _record(decision, before: str | None, t: float, switches: list[SwitchLog],
            watching: list[tuple[float, str | None]], replayed: set[tuple[str, int]]) -> None:
    """Log what the TV did. Any "switch" that survives decide() is a real tune — including one back
    to the same game after a replay (kind "home"/"break_visit"/"live"): when that went unlogged the
    grader thought the replay ran on forever AND skipped mid-play checks on the next cut."""
    if decision.action == "switch" and decision.target:
        switches.append(SwitchLog(t, before, decision.target, decision.kind, decision.reason,
                                  getattr(decision, "rewind_s", 0.0) or 0.0))
        watching.append((t, decision.target))
    elif decision.action == "replay" and decision.replay:
        switches.append(SwitchLog(t, before, decision.target, "replay", decision.reason))
        watching.append((t, f"replay:{decision.target}"))


def _credit_completed_replays(windows: list[tuple[str, int, float, float]],
                              watching: list[tuple[float, str | None]], now: float,
                              replayed: dict[tuple[str, int], float]) -> None:
    """A replay request earns no credit until its modeled clip finishes without another tune.

    This is simulation credit, not evidence that a physical player's seek landed correctly.
    A new switch exactly at the scheduled end is allowed; an earlier interruption is not.
    """
    for gid, event, start, end in windows[:]:
        if end > now:
            continue
        if not any(start < at < end for at, _ in watching):
            if isinstance(replayed, dict):
                replayed.setdefault((gid, event), end)
            else:
                replayed.add((gid, event))
        windows.remove((gid, event, start, end))


def _poll(replays: list[Replay], idx: dict[str, int], now: float, feed_lag: float, dec: Decider) -> dict:
    """One poll of every feed: what the app knows `feed_lag` seconds after each play happened."""
    states = {}
    for r in replays:
        i = idx[r.gid]
        while i + 1 < len(r.plays) and r.wall[i + 1] + feed_lag <= now:
            i += 1
        idx[r.gid] = i
        g = r.state_at(i) if i >= 0 else None
        if g is None:
            continue
        # parse_summary already stamps plays with their real wallclock.
        dec.timeline(r.gid, r.league).observe(g)
        states[r.gid] = g
    return states


def _on_screen_game(watching: list[tuple[float, str | None]], t: float, tune_s: float) -> str | None:
    cur = None
    for at, g in watching:
        if at + tune_s <= t:
            cur = g
        elif at <= t:
            cur = None  # mid-tune: black screen
        else:
            break
    return cur


def watched_whole(watching: list[tuple[float, str | None]], gid: str, start: float, end: float,
                  tune_s: float, horizon: float, offsets: list[float] | None = None) -> bool:
    """Full observed play interval, including no retune away and back during the play.

    offsets (aligned with watching): a visit that came up `o` s behind live (a rewind on entry)
    shows this play `o` s later than the live edge would."""
    if not offsets or not any(offsets):
        return (end <= horizon and _on_screen_game(watching, start, tune_s) == gid
                and not any(start < at <= end for at, _ in watching))
    for i, (at, g) in enumerate(watching):
        if g != gid:
            continue
        o = offsets[i] if i < len(offsets) else 0.0
        nxt = watching[i + 1][0] if i + 1 < len(watching) else float("inf")
        if end + o <= horizon and at + tune_s <= start + o and end + o < nxt:
            return True
    return False


def from_start(watching: list[tuple[float, str | None]], r, i: int, lag: float, tune_s: float, horizon: float,
               offsets: list[float] | None = None) -> bool:
    """[from the start] Was game r on screen, offset-aware and with no retune away, from the moment the ball
    went live after the last dead ball before play i (grading.restart_before) through the end of play i?"""
    start = restart_before(r.league, r.wall, r.plays, i) + lag
    return watched_whole(watching, r.gid, start, r.wall[i] + lag, tune_s, horizon, offsets)


def on_screen(watching: list[tuple[float, str | None]], offsets: list[float] | None, t: float,
              tune_s: float) -> tuple[str | None, float]:
    """(what the screen shows at t, how far behind live that visit runs)."""
    cur, off = None, 0.0
    for i, (at, g) in enumerate(watching):
        if at + tune_s <= t:
            cur, off = g, (offsets[i] if offsets and i < len(offsets) else 0.0)
        elif at <= t:
            cur, off = None, 0.0
        else:
            break
    return cur, off


def shows(watching: list[tuple[float, str | None]], offsets: list[float] | None, gid: str, t: float,
          tune_s: float) -> bool:
    """Does the screen show game gid's live-edge moment t (= wallclock + stream delay) at some point,
    counting a visit that runs `o` s behind live as showing it at t + o?"""
    if not offsets or not any(offsets):
        return _on_screen_game(watching, t, tune_s) == gid
    for i, (at, g) in enumerate(watching):
        if g != gid:
            continue
        o = offsets[i] if i < len(offsets) else 0.0
        nxt = watching[i + 1][0] if i + 1 < len(watching) else float("inf")
        if at + tune_s <= t + o < nxt:
            return True
    return False


def offsets_of(switches) -> list[float]:
    return [getattr(s, "offset", 0.0) or 0.0 for s in switches]


def leave_offsets(switches) -> list[float]:
    """For each switch: how far behind live the picture it leaves was running."""
    out, cur, off = [], None, 0.0
    for s in switches:
        out.append(off if s.frm is not None and s.frm == cur else 0.0)
        cur, off = s.to, (getattr(s, "offset", 0.0) or 0.0)
    return out


def tuning_blackout_seconds(watching: list[tuple[float, str | None]], tune_s: float,
                           t0: float, t1: float) -> float:
    """Union of modeled tuning intervals; don't hide them in the commercial denominator."""
    return sum(max(0.0, min(at + tune_s, watching[i + 1][0] if i + 1 < len(watching) else t1, t1)
                   - max(at, t0)) for i, (at, _) in enumerate(watching))


def _grade(replays, switches, watching, my_teams, lag_total, play_len, tune_s, t0, t1, replayed) -> Report:
    by_id = {r.gid: r for r in replays}
    cuts = []
    cuts_high: list = []   # the mid-play leaves at a high-leverage moment (redzone/lab/leverage.py)
    prev_kind = None
    offs = offsets_of(switches) if len(switches) == len(watching) else None
    left_off = leave_offsets(switches)
    for s, lo in zip(switches, left_off):
        r = by_id.get(s.frm or "")
        was_replay = prev_kind == "replay"
        prev_kind = s.kind
        if not r or was_replay:
            continue
        screen_t = s.t - lag_of(lag_total, r.gid) - lo  # game time showing on screen when we cut
        for w, p in zip(r.wall, r.plays):
            t = (espn._get(p, "type", "text") or "").lower()
            if t in ("timeout", "official timeout", "end period", "end of half", "two-minute warning",
                     "end of regulation", "end of game", "coin toss") or espn.stoppage_for(r.league, p) in REAL_BREAKS and not p.get("scoringPlay"):
                continue
            if w - play_len <= screen_t <= w:
                cuts.append((s.t, f"{r.matchup}: {(p.get('text') or '')[:70]}"))
                if leverage.high_leverage(r.league, p):
                    cuts_high.append(cuts[-1])
                break

    big = live = mine = mine_live = caught = missed_on_replay = seen10 = my_seen10 = crunch_on_replay = 0
    seen3 = seen2 = my_seen3 = 0
    see_s: list = []
    missed = []
    big_fs = crunch_fs = my_crunch_fs = 0
    for r in replays:
        for k, (w, p) in enumerate(zip(r.wall, r.plays)):
            if not _is_big(p):
                continue
            big += 1
            air = w + lag_of(lag_total, r.gid)
            on = watched_whole(watching, r.gid, air - play_len, air, tune_s, t1, offs)
            live += on
            big_fs += on and from_start(watching, r, k, lag_of(lag_total, r.gid), tune_s, t1, offs)
            rep_seen = (r.gid, round(w)) in replayed
            caught += on or rep_seen
            soon = on or replayed_by(replayed, (r.gid, round(w)), air + CATCHUP_S)
            seen10 += soon
            soon3 = on or replayed_by(replayed, (r.gid, round(w)), air + CATCHUP_3_S)
            seen3 += soon3
            seen2 += on or replayed_by(replayed, (r.gid, round(w)), air + CATCHUP_2_S)
            see_s.append(time_to_see(on, replayed, (r.gid, round(w)), air))
            ours = any(a in my_teams for a in r.matchup.replace("@", " ").split())
            if ours:
                mine += 1
                mine_live += on or rep_seen
                my_seen10 += soon
                my_seen3 += soon3
            if not on and (_on_screen_game(watching, air - play_len, tune_s) or "").startswith("replay:"):
                missed_on_replay += 1   # aired while we sat on a replay: live was given up for old footage
            if not (on or rep_seen):
                missed.append((air, r.matchup, (p.get("text") or "")[:90]))

    # Crunch time: final 2:00 of a one-score game (or OT). Did you see those plays live?
    crunch = crunch_live = my_crunch = my_crunch_live = 0
    for r in replays:
        for k, (w, p) in enumerate(zip(r.wall, r.plays)):
            per = espn._int(espn._get(p, "period", "number")) or 0
            clk = espn._clock_s(espn._get(p, "clock", "displayValue"))
            hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
            t = (espn._get(p, "type", "text") or "").lower()
            if any(k in t for k in ("timeout", "end ", "end of", "two-minute", "coin toss", "kickoff")) \
                    and not p.get("scoringPlay"):
                continue
            close = hs is not None and as_ is not None and abs(hs - as_) <= 8
            if not ((per == 4 and clk is not None and clk <= 120 and close) or per >= 5):
                continue
            crunch += 1
            on = watched_whole(watching, r.gid, w + lag_of(lag_total, r.gid) - play_len,
                               w + lag_of(lag_total, r.gid), tune_s, t1, offs)
            crunch_live += on
            fs = on and from_start(watching, r, k, lag_of(lag_total, r.gid), tune_s, t1, offs)
            crunch_fs += fs
            if not on and (_on_screen_game(watching, w + lag_of(lag_total, r.gid) - play_len, tune_s)
                           or "").startswith("replay:"):
                crunch_on_replay += 1   # a crunch play aired while we sat on old footage
            if any(a in my_teams for a in r.matchup.replace("@", " ").split()):
                my_crunch += 1
                my_crunch_live += on
                my_crunch_fs += fs

    dead = watched = 0.0
    causes: dict[str, float] = {}
    t = t0
    while t <= t1:
        g, o = on_screen(watching, offs, t, tune_s)
        cause = None
        if g == REDZONE:
            # Commercial-free, but only while it's on the air; parked on a dead channel is dead air.
            watched += 10
            cause = None if _redzone_on_screen(replays, t - lag_total) else "RedZone off the air"
        elif g and g.startswith("replay:"):
            watched += 10
        elif g:
            r = by_id[g]
            screen_t = t - lag_of(lag_total, g) - o
            i = max(0, _bisect(r.wall, screen_t))
            st = r.state_at(i)
            if st and st.phase == Phase.LIVE:
                watched += 10
                if st.stoppage in REAL_BREAKS:
                    cause = _dead_cause(replays, r, i, t, switches, watching, lag_total, tune_s)
        if cause:
            dead += 10
            causes[cause] = causes.get(cause, 0.0) + 10
        t += 10
    rep = Report(switches, cuts, big, live, mine, mine_live, dead, watched, (t1 - t0) / 3600, missed)
    rep.mid_play_cuts_high = cuts_high
    rep.dead_causes = causes
    rep.tuning_blackout_s = tuning_blackout_seconds(watching, tune_s, t0, t1)
    from redzone.lab import cutgrade
    rep.cut = cutgrade.grade(replays, switches, lag_total, tune_s)
    rep.behind_live_s = behind_live_seconds(watching, t0, t1)
    rep.big_missed_on_replay = missed_on_replay
    rep.big_seen_10m, rep.my_big_seen_10m, rep.crunch_on_replay = seen10, my_seen10, crunch_on_replay
    rep.big_seen_3m, rep.big_seen_2m, rep.my_big_seen_3m = seen3, seen2, my_seen3
    rep.big_see_s = see_s
    rep.drive_plays, rep.drive_plays_live, rep.drive_plays_other_sport, rep.drive_plays_on_redzone = drive_coverage(
        replays, watching, lag_total, play_len, tune_s, t1)
    rep.big_plays_caught = caught
    rep.crunch, rep.crunch_live, rep.my_crunch, rep.my_crunch_live = crunch, crunch_live, my_crunch, my_crunch_live
    rep.big_from_start, rep.crunch_from_start, rep.my_crunch_from_start = big_fs, crunch_fs, my_crunch_fs
    return rep


def behind_extra(switches: list, t1: float) -> list[tuple[float, float]]:
    """[replay and resume] (seconds, extra s behind its usual place) per live stretch on screen: the
    rewinds on entry and the pauses for replays, never the tune's own landing jitter. Replays excluded."""
    out = []
    for i, s in enumerate(switches):
        end = switches[i + 1].t if i + 1 < len(switches) else t1
        if s.kind == "replay" or end <= s.t:
            continue
        out.append((end - s.t, max(0.0, (s.offset or 0.0) - (s.jitter or 0.0))))
    return out


def weighted_pct(pairs: list[tuple[float, float]], q: float) -> float:
    """The time-weighted q-quantile of (weight, value) pairs (0 when empty)."""
    tot = sum(w for w, _ in pairs)
    if tot <= 0:
        return 0.0
    acc = 0.0
    for w, v in sorted(pairs, key=lambda p: p[1]):
        acc += w
        if acc >= q * tot:
            return v
    return max(v for _, v in pairs)


def behind_live_seconds(watching: list[tuple[float, str | None]], t0: float, t1: float) -> float:
    """[redzone mode] Seconds the screen showed a catch-up replay: time spent behind live."""
    out = 0.0
    for i, (at, g) in enumerate(watching):
        if not (g or "").startswith("replay:"):
            continue
        end = watching[i + 1][0] if i + 1 < len(watching) else t1
        out += max(0.0, min(end, t1) - max(at, t0))
    return out


_NOT_SCRIMMAGE = ("timeout", "end period", "end of half", "two-minute", "end of regulation", "end of game",
                  "coin toss", "kickoff", "penalty", "extra point", "two-point")


def hot_drive_plays(r: "Replay") -> list[int]:
    """[redzone mode] Indexes (into r.plays) of the scrimmage plays on NFL drives worth seeing: the
    drive scored, or reached the red zone. What RedZone exists to show; a punt-fest has none."""
    if r.league.base != League.NFL:
        return []
    data = getattr(r, "data", None)
    if not data:
        return []   # a replay without its raw summary (test fakes, partial archives): no drive data
    drives = list(espn._get(data, "drives", "previous") or [])
    cur = espn._get(data, "drives", "current")
    if cur:
        drives.append(cur)
    hot_ids: set[str] = set()
    for dr in drives:
        plays = dr.get("plays") or []
        deep = any((espn._int(espn._get(p, "start", "yardsToEndzone")) or 99) <= 20 for p in plays)
        if dr.get("isScore") or deep:
            hot_ids.update(str(p.get("id")) for p in plays)
    out = []
    for i, p in enumerate(r.plays):
        t = (espn._get(p, "type", "text") or "").lower()
        if str(p.get("id")) not in hot_ids or any(k in t for k in _NOT_SCRIMMAGE):
            continue
        if not espn._int(espn._get(p, "start", "down")):
            continue
        out.append(i)
    return out


def drive_coverage(replays, watching, lag_total: float, play_len: float, tune_s: float,
                   t1: float) -> tuple[int, int, int, int]:
    """[redzone mode] (hot-drive plays, watched live, missed while another sport was on screen,
    aired while on the RedZone channel)."""
    by_id = {r.gid: r for r in replays}
    n = live = other = rz = 0
    for r in replays:
        for i in hot_drive_plays(r):
            air = r.wall[i] + lag_of(lag_total, r.gid)
            n += 1
            if watched_whole(watching, r.gid, air - play_len, air, tune_s, t1):
                live += 1
                continue
            g = _on_screen_game(watching, air - play_len, tune_s)
            o = by_id.get(g or "")
            if o is not None and o.league.base != League.NFL:
                other += 1
            rz += g == REDZONE
    return n, live, other, rz


def _dead_cause(replays, r: Replay, i: int, t: float, switches, watching, lag_total: float, tune_s: float) -> str:
    """Why is this break on screen? Bookkeeping only: it never changes what counts as dead."""
    screen_t = t - lag_of(lag_total, r.gid)
    others = [x.state_at(j) for x in replays if x is not r and (j := _bisect(x.wall, screen_t)) >= 0]
    if not _redzone_on_screen(replays, screen_t) and not any(
            o and o.phase == Phase.LIVE and o.stoppage not in REAL_BREAKS for o in others):
        return "alone: one game on, nothing else to show"
    arrived = max((k for k, (at, _) in enumerate(watching) if at <= t), default=None)
    brk_on, brk_off = _break_span(r, i)
    lag = lag_of(lag_total, r.gid)
    brk_on, brk_off = brk_on + lag, brk_off + lag
    if arrived is not None and watching[arrived][0] + tune_s > brk_on:
        return f"landed in it ({switches[arrived].kind})"
    left = arrived is not None and any(t < s.t < brk_off for s in switches[arrived + 1:])
    return "slow to leave (delay margin)" if left else "sat through it"


def _break_span(r: Replay, i: int) -> tuple[float, float]:
    """Wallclock span of the run of breaks around play i (score -> timeout -> ... -> next snap)."""
    def brk(k):
        return espn.stoppage_for(r.league, r.plays[k]) in REAL_BREAKS
    a, b = i, i + 1
    while a > 0 and brk(a - 1):
        a -= 1
    while b < len(r.plays) and brk(b):
        b += 1
    return r.wall[a], (r.wall[b] if b < len(r.wall) else 1e18)


def _redzone_on_screen(replays: list[Replay], screen_t: float) -> bool:
    """Was RedZone on the air at this (screen) time? Same rule the decider is fed."""
    states = [r.state_at(i) for r in replays if (i := _bisect(r.wall, screen_t)) >= 0]
    return redzone_on(g for g in states if g)


class _Clock:
    t = 0.0

    def __call__(self):
        return self.t


def fmt_time(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone(ET).strftime("%-I:%M:%S %p")


def timeline_markdown(rep: Report, replays: list[Replay], title: str) -> str:
    names = {r.gid: r.matchup for r in replays}
    lines = [f"# {title}", "", "| Grade | Value |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in rep.summary.items()]
    lines += ["", "## Would have switched", "", "| Time (ET) | To | Why |", "|---|---|---|"]
    for s in rep.switches:
        lines.append(f"| {fmt_time(s.t)} | {names.get(s.to, s.to)} | {s.kind}: {s.reason} |")
    if rep.mid_play_cuts:
        lines += ["", "## Mid-play cuts (bugs)", ""] + [f"- {fmt_time(t)} {w}" for t, w in rep.mid_play_cuts]
    lines += ["", "## Big plays it missed (first 25)", ""]
    lines += [f"- {fmt_time(t)} {m}: {txt}" for t, m, txt in rep.missed[:25]]
    return "\n".join(lines) + "\n"

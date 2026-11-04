"""The real Engine, replaying a past day on a simulated clock. Never touches the TV.

Everything that decides runs unchanged (Scorer, Decider with its gates/replays/RedZone/crunch/garbage
logic, Engine.decide/snapshot/action, the phone UI). Only three things are swapped:
  - the clock: one SimClock drives the scorer, decider, merger, delay book AND every time.time()
    inside redzone.engine (snapshot timestamps, switch log, skips) while the demo is stepping;
  - the data: plays arrive from archived summaries feed_lag seconds after they happened
    (redzone.lab.backtest's Replay/_feed/_deliver), instead of from ESPN;
  - the controller: InertController. Dry run forced, no adb, no TV, no sticks, ever.
"""
from __future__ import annotations

import contextlib
import json
import random
import zlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import time as _real_time
import yaml

from redzone import engine as engine_mod, standby
from redzone.channels import REDZONE_TARGET, ChannelMap, Where
from redzone.config import Config, ConfigError, _deep_merge, default, validate
from redzone.control import Controller, Outcome
from redzone.decider.decider import REDZONE, Decider
from redzone.decider.screen import DelayBook
from redzone.engine import Engine
from redzone.lab import backtest
from redzone.lab.backtest import Replay, _deliver, _feed
from redzone.models import League, Phase
from redzone.scoring.drive import DriveScorer
from redzone.sources.feeds import FeedBook
from redzone.sources import espn
from redzone.sources.merge import Merger
from redzone.store import Store

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent.parent
TEAM_NAMES = {"nfl": "Commanders", "nba": "Wizards", "nhl": "Capitals"}


def _crunch_counts(r: Replay, watching, lag_total: float, play_len: float, tune_s: float,
                   last_period: int = 4, margin: int = 8, period_len: float | None = None,
                   window: tuple[float, float] | None = None,
                   offsets: list[float] | None = None) -> tuple[int, int]:
    """One game's crunch-time plays (final 2:00 of a one-score game, or OT): (how many, how many live).
    Uses backtest.crunch_counts when the backtest has it."""
    if hasattr(backtest, "crunch_counts"):
        return backtest.crunch_counts(r, watching, lag_total, play_len, tune_s, last_period, margin,
                                      period_len, window)
    n = live = 0
    for w, p in zip(r.wall, r.plays):
        per = espn._int(espn._get(p, "period", "number")) or 0
        clk = espn._clock_s(espn._get(p, "clock", "displayValue"))
        if period_len is not None and clk is not None:
            clk = period_len - clk
        if window and not (window[0] <= w + lag_total <= window[1]):
            continue
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        t = (espn._get(p, "type", "text") or "").lower()
        if any(k in t for k in ("timeout", "end ", "end of", "two-minute", "coin toss", "kickoff")) \
                and not p.get("scoringPlay"):
            continue
        close = hs is not None and as_ is not None and abs(hs - as_) <= margin
        if not ((per == last_period and clk is not None and clk <= 120 and close) or per > last_period):
            continue
        n += 1
        live += backtest.shows(watching, offsets, r.gid, w + lag_total - play_len, tune_s)
    return n, live


class SimClock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@contextlib.contextmanager
def sim_time(clock: SimClock):
    """Make redzone.engine's time.time() read the simulated clock (restored afterwards)."""
    real = engine_mod.time
    engine_mod.time = SimpleNamespace(time=clock, sleep=_real_time.sleep, monotonic=_real_time.monotonic,
                                      strftime=_real_time.strftime)
    try:
        yield
    finally:
        engine_mod.time = real


class InertController(Controller):
    """A Controller that can't reach anything: no adb, no sticks, no TV. Every tune is a dry run."""

    def __init__(self, cfg, channels, clock):
        super().__init__(cfg, channels)
        self._clock = clock

    @property
    def live_control(self) -> bool:
        return False

    @property
    def two_stick(self) -> bool:
        return False

    def load_devices(self) -> None:
        self.sticks, self.hdmi = [], {}

    async def start(self) -> None:
        self.adb_ok = False
        self.note = ""

    async def keepalive(self) -> None:
        return None

    def submit(self, target, replay_back_s=None, on_done=None, may_cut=None, **kw) -> None:
        out = Outcome(True, target, 0.0, "replay demo (dry run)" if target else "no channel for this game",
                      True, self._clock())
        self.on_target = target
        self.last = out
        if on_done:
            on_done(out)

    async def execute(self, target, replay_back_s=None, may_cut=None, **kw) -> Outcome:
        return Outcome(True, target, 0.0, "replay demo (dry run)", True, self._clock())

    def can_tune(self, target) -> bool:
        return bool(target)

    def preload(self, target, *a, **kw) -> bool:
        return None

    async def _tune(self, *a, **k):
        raise RuntimeError("replay demo never tunes")

    async def _rewind(self, *a, **k):
        raise RuntimeError("replay demo never tunes")

    async def _flip_to(self, *a, **k):
        raise RuntimeError("replay demo never flips inputs")

    async def scan_sunday_ticket(self, codes, games, on_found=None) -> dict:
        return {}


class DemoChannels(ChannelMap):
    """Real channel rules, but every game gets its own target (in dry run, unmatched Sunday Ticket /
    League Pass / ESPN+ games would otherwise share one placeholder target and look like one channel)."""

    SHARED = ("youtubetv:st", "youtubetv:espnplus", "prime:leaguepass", "prime:monumental", "prime:nba")

    def save(self) -> None:
        return None

    def where(self, g, sunday_ticket: bool = True) -> Where:
        w = super().where(g, sunday_ticket)
        if w.target in self.SHARED:
            label = w.label.split(" (")[0]
            return Where(f"{w.target}:{g.game_id}", label, w.confidence, True)
        if w.target is None and g.phase == Phase.LIVE:
            # No TV listing in the archive: still show what it would pick, as if you'd set the channel.
            return Where(f"youtubetv:demo:{g.game_id}", f"{w.label} (demo)", "likely", True)
        return w


def find_user_config() -> Path | None:
    """data/config.yaml here, or (in a git worktree) in the main checkout. Read only."""
    here = ROOT / "data" / "config.yaml"
    if here.exists():
        return here
    git = ROOT / ".git"
    if git.is_file():
        line = git.read_text().strip()
        if line.startswith("gitdir:"):
            main = Path(line.split(":", 1)[1].strip()).resolve().parents[2]
            p = main / "data" / "config.yaml"
            if p.exists():
                return p
    return None


def demo_config(work: Path, source: Path | None) -> Config:
    """Your real settings (read only), dry run forced, saved only inside the demo folder."""
    work.mkdir(parents=True, exist_ok=True)
    cfg = Config(user_path=work / "config.yaml", history_dir=work / "config_history")
    over = {}
    if source and source.exists():
        try:
            over = yaml.safe_load(source.read_text()) or {}
            validate(over, cfg.defaults)
        except (ConfigError, yaml.YAMLError):
            over = {}
    over = _deep_merge(over, {"mode": {"dry_run": True}})
    cfg.overrides = over
    cfg.data = _deep_merge(cfg.defaults, over)
    return cfg


def load_day(folder: Path, leagues=("nfl", "nba", "nhl")) -> tuple[list[Replay], dict[str, tuple[list[str], bool]]]:
    """Replays for every finished game in the folder, plus each game's TV listings from the scoreboard."""
    reps, tv = [], {}
    for lg in leagues:
        d = folder / lg
        if not d.exists():
            continue
        for sb in d.glob("scoreboard_*.json"):
            for e in json.loads(sb.read_text()).get("events", []):
                comp = (e.get("competitions") or [{}])[0]
                tv[str(e.get("id"))] = espn._broadcasts(comp)
        for p in sorted(d.glob("summary_*.json")):
            try:
                r = Replay.load(League(lg), p)
            except (ValueError, KeyError, TypeError):
                r = None
            if r:
                reps.append(r)
    return reps, tv


def et_at(day: str, hhmm: str) -> float:
    """'13:00' or '1:00pm' on that day (ET) -> epoch seconds. Times before 6am mean the next morning."""
    s = hhmm.strip().lower().replace(" ", "")
    pm, am = s.endswith("pm"), s.endswith("am")
    s = s.rstrip("apm")
    h, _, m = s.partition(":")
    h, m = int(h), int(m or 0)
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    base = datetime.strptime(day, "%Y%m%d").replace(tzinfo=ET)
    t = base.replace(hour=h, minute=m).timestamp()
    return t + 86400 if h < 6 else t


def hm(t: float) -> str:
    return datetime.fromtimestamp(t, ET).strftime("%-I:%M %p")


class DemoEngine(Engine):
    def __init__(self, replays: list[Replay], tv: dict, cfg: Config, *, day: str = "", speed: float = 30.0,
                 step: float = 2.0, work: Path | None = None, my_teams: set[str] | None = None, out=print,
                 calibrated: bool = True):
        work = work or (ROOT / "data" / "demo" / "_run")
        self.clock = SimClock()
        # Timing, from default_config.yaml demo.* (a play reaches the app feed_lag_s after it happens,
        # same as the backtest; the replay's picture is up tune_s after a switch).
        t = {k: float(cfg.get(f"demo.{k}", default(f"demo.{k}")))
             for k in ("feed_lag_s", "play_len_s", "tune_s", "calibrated_sigma_s")}
        self.feed_lag_s, self.play_len_s = t["feed_lag_s"], t["play_len_s"]
        self.tune_s, self.calibrated_sigma_s = t["tune_s"], t["calibrated_sigma_s"]
        super().__init__(cfg, store=Store(Path(":memory:")))
        clock = self.clock
        self.channels = DemoChannels(path=work / "channels.json")
        self.merger = Merger(clock=clock)
        # Engine.feeds (contracts Feeds) must read THIS merger on the sim clock; the replayed plays
        # themselves are self.replay_feeds (never fetched by URL, so the merger's rule decides).
        self.feeds = FeedBook(self.merger, self.fetcher, self._feed_urls, self._feed_cadence, clock=clock)
        self.scorer = DriveScorer(self.cfg, clock=clock)
        self.delays = DelayBook(self.cfg.get("delay.default"), self.cfg.get("delay.sigma_default"), clock=clock,
                                model=self.cfg.get("delay.model", "channel"))
        self.decider = Decider(self.cfg, self.delays, clock=clock)
        self.decider.entry_cost = lambda target: self.tune_s   # [cut timing] the replay's picture is up tune_s after a switch
        self.control = InertController(self.cfg, self.channels, clock)
        # The hidden stick, modeled: what the real standby Planner would have had ready at each switch.
        self.planner = standby.Planner(self.cfg, clock=clock)
        self.shadow = standby.ShadowStick(app_s={a: float(v) for a, v in (self.cfg.get("decider.switch_cost") or {}).items()
                                                 if a != "hdmi_flip"},
                                         cfg=self.cfg)
        self.started = 0.0
        self.replays = replays
        self.tv = tv
        self.day, self.speed, self.step_s = day, speed, step
        self.out = out
        self.my_teams = my_teams if my_teams is not None else {
            a for v in (self.cfg.get("teams") or {}).values() for a in v}
        self.t0 = min(r.wall[0] for r in replays) - 600
        self.t1 = max(r.wall[-1] for r in replays) + 300
        self.clock.t = self.t0
        self.replay_feeds = {r.gid: _feed(r, random.Random(zlib.crc32(r.gid.encode())), self.feed_lag_s, step,
                                   None, 0.0, 0.0, 0.0, self.t0, self.t1) for r in replays}
        self.idx = {r.gid: -1 for r in replays}
        self.switches: list[backtest.SwitchLog] = []
        self.watching: list[tuple[float, str | None]] = []
        self.replayed: set[tuple[str, int]] = set()
        self.replay_windows: list[tuple[str, int, float, float]] = []  # (game, play time, start, planned end)
        self.timeline: list[str] = []
        self.quiet = False
        self.calibrated = calibrated
        for r in replays:
            pre = r.state_at(-1)
            if pre is not None:
                pre.home.score = pre.away.score = 0   # the header carries the final; no spoilers

    # ---------------------------------------------------------------- clock + data

    def _feed_games(self) -> None:
        now = self.clock()
        for r in self.replays:
            f = self.replay_feeds[r.gid]
            f.fresh_at = now
            i = self.idx[r.gid]
            while i + 1 < len(r.plays) and f.arrive[i + 1] <= now:
                i += 1
            self.idx[r.gid] = i
            if i < 0:
                g = r.state_at(-1)
                if g is None or (g.start_time and g.start_time - now > 6 * 3600):
                    continue
            else:
                g = _deliver(r, f, i, now)
                if g is None:
                    continue
            g.broadcasts, g.national_tv = self.tv.get(r.gid, (g.broadcasts, g.national_tv))
            got = self.merger.update("espn_summary", g)
            if got is not g:
                # The archive is the whole truth here: a play whose clock reads "earlier" (a reset
                # after a review, say) must not bench the only source the demo has.
                g.updated_at, g.sources = now, {"espn_summary": now}
                self.merger.games[g.game_id] = g
                self.merger._per_source["espn_summary"][g.game_id] = g
                h = self.merger.health["espn_summary"]
                h.benched, h.regressions = False, 0
            if self.calibrated and g.phase == Phase.LIVE:
                self._calibrate_target(g, now)
            if g.phase == Phase.LIVE or (g.phase == Phase.POST and g.game_id in self.decider.timelines):
                self.decider.timeline(g.game_id, g.league).observe(g)

    def _calibrate_target(self, g, now: float) -> None:
        """As if you'd tapped "I just saw it" (or the mic matched a crowd roar) on each channel, and
        again every 30 min — same assumption as the backtest. --uncalibrated turns this off."""
        w = self.channels.where(g, sunday_ticket=self.cfg.get("sunday_ticket", True))
        for target in filter(None, (w.target, REDZONE_TARGET if g.league == League.NFL else None)):
            self._calibrate(target, now)

    def _calibrate(self, target: str, now: float) -> None:
        d = self.delays.get(target)
        if d.calibrated_at is None or now - d.calibrated_at > 1800:
            d.calibrated_at, d.sigma = now, self.calibrated_sigma_s

    def advance(self, until: float) -> None:
        """Run the engine from now to `until` (sim time), one step at a time."""
        while self.clock.t + self.step_s <= until and self.clock.t <= self.t1:
            self.clock.t += self.step_s
            self._feed_games()
            self.decide()

    @property
    def finished(self) -> bool:
        return self.clock.t > self.t1

    # ---------------------------------------------------------------- engine hooks (all on sim time)

    def decide(self) -> bool:
        with sim_time(self.clock):
            before = self.decider.current
            changed = super().decide()
            d = self.last_decision
            if d is not None and d.action in ("switch", "replay") and d.target:
                rid = d.replay.pid if d.action == "replay" and d.replay else None
                # A break filled: a visit, or a catch-up / chase that leaves a game on a break.
                left = self.scored.get(before) if before else None
                on_break = bool(left and left.is_break) or before in self.decider._screen_break
                brk = d.kind == "break_visit" or (d.kind in ("replay", "chase") and on_break)
                self.shadow.switch(self._target(d.target), rid, self._target(before), self.clock(), brk=brk)
                backtest._record(d, before, self.clock(), self.switches, self.watching, self.replayed)
                if d.action == "replay" and d.replay and self.decider.replay_until:
                    # [redzone mode] the same credit rule as the backtest: a replay counts once its
                    # clip has run to the end without another tune (it never counted here before).
                    self.replay_windows.append((d.target, round(d.replay.t), self.clock(), self.decider.replay_until))
                self._narrate(d)
            backtest._credit_completed_replays(self.replay_windows, self.watching, self.clock(), self.replayed)
            return changed

    def _target(self, gid: str | None) -> str | None:
        if gid == REDZONE:
            return REDZONE_TARGET
        return getattr(self.where.get(gid or ""), "target", None) or self.decider.target_of(gid)

    def _preload_standby(self, cands: dict, redzone: str | None = None) -> None:
        """Same Planner as live, on the modeled hidden stick (the inert controller has no sticks)."""
        if self.decider.user.paused:
            return
        pick = self.planner.plan(self._standby_options(cands, redzone), self.shadow.standby)
        if pick:
            self.shadow.preload(pick.target, pick.replay_id, self.clock())

    def snapshot(self) -> dict:
        with sim_time(self.clock):
            s = super().snapshot()
        when = datetime.strptime(self.day, "%Y%m%d").strftime("%a %b %-d") if self.day else "a past day"
        s["mode"]["demo"] = {"label": f"Replay of {when} · {self.speed:g}x", "speed": self.speed,
                             "sim_time": self.clock(), "clock": hm(self.clock()), "day": self.day}
        s["mode"]["dry_run"] = True
        s["mode"]["tv_control"] = False
        return s

    def action(self, action: str, game_id: str | None = None, value=None) -> tuple[bool, str]:
        if action in ("dry_run", "scan_st", "mic", "saw_it"):
            return False, "Not in the replay demo — the TV is never touched here"
        with sim_time(self.clock):
            return super().action(action, game_id, value)

    async def game_detail(self, gid: str) -> dict | None:
        g = self.merger.games.get(gid)
        if g is None:
            return None
        r = next((x for x in self.replays if x.gid == gid), None)
        summary = None
        if r is not None and self.idx.get(gid, -1) >= 0:
            # Only the plays that have "happened" so far (the archived file holds the whole game).
            summary = dict(r.data)
            summary["plays"] = r.plays[: self.idx[gid] + 1]
            summary.pop("drives", None)
            summary.pop("winprobability", None)
        try:
            from redzone import views
            return views.game_detail(g, summary, self.game_item(gid))
        except Exception:
            from redzone import views
            return views.game_detail(g, None, self.game_item(gid))

    async def refresh_links(self, eventlinks) -> int:
        return 0

    def _keep_awake(self, live: bool) -> None:
        return None

    # ---------------------------------------------------------------- the terminal timeline

    def _narrate(self, d) -> None:
        name = self._name(d.target)
        arrow = "↺" if d.action == "replay" else "→"
        line = f"{hm(self.clock()):>8}  {arrow}  {name:<14} — {d.reason}"
        if d.action == "replay":
            line = f"{hm(self.clock()):>8}  {arrow}  replay: {name} — {d.reason}"
        self.timeline.append(line)
        if not self.quiet:
            self.out(line)

    # ---------------------------------------------------------------- report card

    def report(self, start: float | None = None, end: float | None = None) -> dict:
        """The backtest's report card over [start, end] (sim time), split the way you'd judge it."""
        t0 = start or self.t0
        t1 = min(end or self.t1, self.clock())
        lag = float((self.cfg.get("delay.default") or {}).get("youtubetv", 45))
        backtest._credit_completed_replays(self.replay_windows, self.watching, t1, self.replayed)
        rep = backtest._grade(self.replays, self.switches, self.watching, self.my_teams, lag, self.play_len_s,
                              self.tune_s, t0, t1, self.replayed)
        sw = [x for x in self.switches if t0 <= x.t <= t1]
        hours = max(0.1, (t1 - t0) / 3600)
        cuts = [(t, w) for t, w in rep.mid_play_cuts if t0 <= t <= t1]
        win = (t0, t1)

        offs = backtest.offsets_of(self.switches)

        def crunch(r):
            hockey = r.league == League.NHL
            return _crunch_counts(r, self.watching, lag, self.play_len_s, self.tune_s, 3 if hockey else 4,
                                  1 if hockey else 8, 1200.0 if hockey else None, win, offs)

        # Big plays: NFL TDs/FGs/turnovers, NHL goals (every NBA bucket is a "scoring play": left out).
        big = {}
        on_replay = 0   # NFL big plays / NHL goals that aired while we sat on a replay
        for lg in (League.NFL, League.NHL):
            n = live = rz = rp = 0
            for r in (x for x in self.replays if x.league == lg):
                for w, pl in zip(r.wall, r.plays):
                    if not backtest._is_big(pl) or not (t0 <= w + lag <= t1):
                        continue
                    n += 1
                    on = backtest._on_screen_game(self.watching, w + lag - self.play_len_s, self.tune_s)
                    on_replay += (on or "").startswith("replay:")
                    if backtest.shows(self.watching, offs, r.gid, w + lag - self.play_len_s, self.tune_s):
                        on = r.gid
                    if on == r.gid:
                        live += 1
                    elif (r.gid, round(w)) in self.replayed:
                        rp += 1
                    elif on == REDZONE and lg == League.NFL:
                        rz += 1
            big[lg.value] = (n, live, rp, rz)
        cn = cl = 0
        for r in self.replays:
            a, b = crunch(r)
            cn, cl = cn + a, cl + b
        teams = self.cfg.get("teams") or {}
        mine = {}
        for lg, nick in TEAM_NAMES.items():
            abbrs = set(teams.get(lg) or [])
            games = [r for r in self.replays if r.league.value == lg
                     and abbrs & set(r.matchup.replace("@", " ").split())]
            if not games:
                mine[nick] = "no game that day"
                continue
            n = live = 0
            for r in games:
                a, b = crunch(r)
                n, live = n + a, live + b
            mine[nick] = (f"{live}/{n} crunch plays live" if n else "no crunch time (not close late)") \
                + f" · {games[0].matchup}"
        pct = lambda a, b: f"{a}/{b} ({100 * a / max(1, b):.0f}%)"  # noqa: E731
        nfl, nhl = big["nfl"], big["nhl"]
        return {
            "window": f"{hm(t0)} – {hm(t1)} ET",
            "mid_play_cuts": len(cuts),
            "mid_play_cut_list": [f"{hm(t)} {w}" for t, w in cuts[:5]],
            "commercial_pct": round(100 * rep.dead_air_s / max(1, rep.watched_s), 1),
            "nfl_big_plays": {"seen": pct(nfl[1] + nfl[2], nfl[0]), "live": nfl[1], "replay": nfl[2],
                              "on_redzone": nfl[3]},
            "nhl_goals": {"seen": pct(nhl[1] + nhl[2], nhl[0]), "live": nhl[1], "replay": nhl[2]},
            "crunch_live": pct(cl, cn),
            "your_teams_crunch": mine,
            "replays": sum(1 for x in sw if x.kind == "replay"),
            # [redzone mode] drives that scored or reached the red zone: their plays seen live (and
            # how many of the misses were spent on another sport); time on catch-up replays.
            "nfl_drive_live": pct(rep.drive_plays_live, rep.drive_plays),
            "nfl_drive_missed_on_other_sport": rep.drive_plays_other_sport,
            "nfl_drive_on_redzone": rep.drive_plays_on_redzone,
            "behind_live_min": round(rep.behind_live_s / 60, 1),
            "behind_live_pct": round(100 * rep.behind_live_s / max(1, (t1 - t0)), 1),
            "big_missed_on_replay": on_replay,
            "switches": len(sw),
            "switches_per_hour": round(len(sw) / hours, 1),
            "preload": self.shadow.card(),
            "cut_timing": rep.cut.summary() if rep.cut is not None else {},
            "dead_air_causes": {k: f"{v / 60:.0f} min" for k, v in sorted(rep.dead_causes.items(),
                                                                           key=lambda kv: -kv[1]) if v >= 60},
        }


def report_text(card: dict) -> str:
    nfl, nhl = card["nfl_big_plays"], card["nhl_goals"]
    lines = ["", f"REPORT CARD  ({card['window']})", "-----------",
             f"  Mid-play cuts          {card['mid_play_cuts']}   (must be 0)",
             f"  Commercials on screen  {card['commercial_pct']}% of watched time",
             f"  NFL big plays seen     {nfl['seen']}  — {nfl['live']} live, {nfl['replay']} as catch-up replays"
             + (f" (+{nfl['on_redzone']} while on RedZone)" if nfl["on_redzone"] else ""),
             f"  NHL goals seen         {nhl['seen']}  — {nhl['live']} live, {nhl['replay']} as replays",
             f"  Crunch time live       {card['crunch_live']}",
             f"  Switches               {card['switches']}  ({card['switches_per_hour']}/hour), "
             f"{card['replays']} of them catch-up replays",
             f"  NFL drives live        {card['nfl_drive_live']} plays of scoring/red-zone drives"
             f" ({card['nfl_drive_missed_on_other_sport']} missed on another sport)",
             f"  Behind live            {card['behind_live_min']} min on catch-up replays"
             f" ({card['behind_live_pct']}%), {card['big_missed_on_replay']} big plays missed meanwhile"]
    if card.get("preload"):
        p = card["preload"]
        lines.append(f"  Preload hit rate       {p['hits']}/{p['switches']} ({p['hit_rate']}%) switches were a"
                     f" flip to a ready hidden stick")
        for app, x in (p.get("seconds_to_picture") or {}).items():
            lines.append(f"    {app:<10} {x['instant']}/{x['switches']} instant, {x['mean_s']} s to picture on average")
    ct = card.get("cut_timing") or {}
    if ct:
        lines.append(f"  Entries landing mid-play {ct['entries_mid_play']}/{ct['entries']} (must be 0); "
                     f"{ct['entries_in_break']} into a commercial, {ct['entry_wait_s']}s dead time after landing")
        lines.append(f"  Leaves in a dead window {ct['leaves_in_dead_pct']}% of {ct['leaves']}; "
                     f"plays missed at the start of a visit {ct['plays_missed_entry']}")
    for nick, v in card["your_teams_crunch"].items():
        lines.append(f"  {nick:<22} {v}")
    for c in card.get("mid_play_cut_list") or []:
        lines.append(f"    cut: {c}")
    if card.get("dead_air_causes"):
        lines.append("  Commercial time by cause: " + ", ".join(f"{k} {v}" for k, v in card["dead_air_causes"].items()))
    return "\n".join(lines)

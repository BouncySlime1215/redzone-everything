"""Exact-date replay diagnostics. No live engine, provider login, device, or wallclock clock.

The archive is a timing proxy, not video ground truth. Audit every archived game's
counterfactual exit permission, rather than rewarding a policy for never switching.
"""
from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from redzone.config import Config
from redzone.decider.screen import Delay, leave_window, screen_evidence
from redzone.lab import backtest, sportsgrade
from redzone.models import League
from redzone.sources import espn


def utc(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


@dataclass(frozen=True)
class ReplayClock:
    historical_start: float
    speed: float = 1.0

    def historical_at(self, session_elapsed_s: float) -> float:
        return self.historical_start + session_elapsed_s * self.speed

    def elapsed_at(self, historical_time: float) -> float:
        return (historical_time - self.historical_start) / self.speed


def load_date(root: Path, league: League, day: str) -> tuple[list, dict]:
    datetime.strptime(day, "%Y%m%d")
    folder = root / league.value
    scoreboard = folder / f"scoreboard_{day}.json"
    manifest = {"league": league.value, "requested_date": day, "date_timezone": "America/New_York",
                "scoreboard": str(scoreboard), "games": [], "missing": [], "status": "unavailable"}
    if not scoreboard.exists():
        manifest["missing"].append("scoreboard")
        return [], manifest
    data = json.loads(scoreboard.read_text())
    manifest["scoreboard_sha256"] = hashlib.sha256(scoreboard.read_bytes()).hexdigest()
    replays = []
    for event in data.get("events", []):
        gid = str(event["id"])
        path = folder / f"summary_{gid}.json"
        replay = backtest.Replay.load(league, path) if path.exists() else None
        if replay is None:
            manifest["missing"].append(gid)
            continue
        replays.append(replay)
        manifest["games"].append({"id": gid, "matchup": replay.matchup,
                                  "scheduled_utc": event.get("date"),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  **timestamp_quality(replay)})
    manifest["status"] = "incomplete" if manifest["missing"] else "complete" if replays else "no_games"
    return replays, manifest


def timestamp_quality(replay) -> dict:
    raw = [espn._ts(p.get("wallclock")) for p in replay.plays]
    pairs = [(a, b) for a, b in zip(raw, raw[1:]) if a is not None and b is not None]
    backwards = [a-b for a, b in pairs if b < a]
    return {"play_entries": len(raw), "missing_raw_timestamps": sum(x is None for x in raw),
            "backward_transitions": len(backwards), "largest_backward_s": max(backwards, default=0),
            "equal_transitions": sum(a == b for a, b in pairs),
            "normalized_timestamps": sum(a != b for a, b in zip(raw, replay.wall)),
            "first_event_utc": utc(replay.wall[0]), "last_event_utc": utc(replay.wall[-1])}


class ArchiveProxy:
    """Precompute intervals using the independent archived-event grader's assumptions.

    Timestamp repairs are unknown, not eligible for a safety success or failure.
    NBA/NHL restart boundaries remain coarse proxies even outside those repairs.
    """
    def __init__(self, replay):
        self.replay = replay
        self.unknown = []
        pad = max(30.0, sportsgrade.RESUME_LEAD.get(replay.league, 0))
        for p, norm in zip(replay.plays, replay.wall):
            raw = espn._ts(p.get("wallclock"))
            if raw is None or raw != norm:
                self.unknown.append(((raw if raw is not None else norm)-pad, norm+pad))
        # Every point at which the archive grader can change its answer.
        points = set(replay.wall)
        if replay.league in sportsgrade.CONTINUOUS:
            points.update(w + 4 for w in replay.wall)
            points.update(w - sportsgrade.RESUME_LEAD[replay.league] for w in replay.wall)
        else:
            points.update(w - sportsgrade.PLAY_LEN[replay.league] for w in replay.wall)
        points = sorted(points)
        self.unsafe = []
        for a, b in zip(points, points[1:]):
            safe, _ = self.at((a+b)/2)
            if not safe:
                self.unsafe.append((a, b))
        # Include exact boundary events; a zero-duration permission is not usable.
        for t in points:
            if not self.at(t)[0]:
                self.unsafe.append((t, t))
        self.unsafe.sort()
        self.starts = [a for a, _ in self.unsafe]

    def at(self, t: float):
        r = self.replay
        return sportsgrade._clean_cut(r.league, r.wall, r.plays, t)

    def window(self, start: float, duration: float) -> str:
        end = start + duration
        if start < self.replay.wall[0] or end > self.replay.wall[-1]:
            return "unknown"
        if any(a <= end and b >= start for a, b in self.unknown):
            return "unknown"
        # Earlier intervals may overlap start; the short bounded list is cheap.
        stop = bisect.bisect_right(self.starts, end)
        if any(b >= start for _, b in self.unsafe[:stop]):
            return "unsafe_proxy"
        return "clear_proxy"


class ExitAudit:
    def __init__(self, replays, true_delay, step, horizon, cfg, clock):
        self.by_id = {r.gid: ArchiveProxy(r) for r in replays}
        self.true_delay, self.step, self.horizon, self.cfg, self.clock = true_delay, step, horizon, cfg, clock
        self.counts = {k: 0 for k in ("samples", "allowed", "unsafe_proxy", "clear_proxy", "unknown_proxy",
                                    "allowed_on_unsafe_proxy", "allowed_on_unknown_proxy", "held_on_clear_proxy",
                                    "model_unknown")}
        self.bad_run, self.longest, self.episodes, self.examples = {}, 0, 0, []

    def observe(self, now, dec):
        for gid, proxy in self.by_id.items():
            screen_t = now-self.true_delay
            if not proxy.replay.wall[0] <= screen_t <= proxy.replay.wall[-1]:
                continue
            tl = dec.timelines.get(gid)
            delay = dec.delays.get(f"youtubetv:{gid}")
            evidence = screen_evidence(tl, delay, now, self.cfg.get("decider.uncalibrated_sigma_s"))
            allowed = bool(tl and leave_window(tl, delay, now, self.cfg.get("decider.uncalibrated_sigma_s"),
                                              self.horizon).safe)
            truth = proxy.window(screen_t, self.horizon)
            c = self.counts
            c["samples"] += 1
            c[truth if truth != "unknown" else "unknown_proxy"] += 1
            c["allowed"] += allowed
            c["model_unknown"] += evidence["status"] == "unknown"
            c["allowed_on_unknown_proxy"] += allowed and truth == "unknown"
            c["held_on_clear_proxy"] += not allowed and truth == "clear_proxy"
            bad = allowed and truth == "unsafe_proxy"
            c["allowed_on_unsafe_proxy"] += bad
            previous = self.bad_run.get(gid, 0)
            self.bad_run[gid] = previous+self.step if bad else 0
            self.longest = max(self.longest, self.bad_run[gid])
            if bad and not previous:
                self.episodes += 1
                if len(self.examples) < 20:
                    self.examples.append({"game": gid, "historical_utc": utc(now),
                                          "session_elapsed_s": self.clock.elapsed_at(now),
                                          "model_reason": evidence["reason"], "horizon_s": self.horizon})

    def summary(self):
        return {"scope": "counterfactual permission for every archived game, not only the selected game",
                "grid_step_s": self.step, "command_horizon_s": self.horizon,
                "counts": self.counts,
                "approx_game_seconds": {k: round(v*self.step, 1) for k, v in self.counts.items()},
                "unsafe_permission_episodes": self.episodes,
                "longest_unsafe_permission_grid_s": self.longest,
                "examples": self.examples,
                "visual_detection_accuracy": None,
                "limit": "Archive-derived action proxy; no video labels, no visual onset/end accuracy."}


def run_session(replays, cfg: Config, *, stress=False, seed=0, step=5.0, horizon=8.0):
    if not replays:
        raise ValueError("No archived games; refusing to substitute another date")
    start = min(r.wall[0] for r in replays)-60
    clock = ReplayClock(start)
    # backtest.delay is AFTER the usual source lag. Report both explicitly.
    feed_lag, after_feed_delay = 8.0, 35.0
    actual_after_feed = 41.0 if stress else after_feed_delay
    true_total = feed_lag+actual_after_feed
    audit = ExitAudit(replays, true_total, step, horizon, cfg, clock)
    knobs = dict(true_delay=actual_after_feed, sigma=5 if stress else 3, seed=seed)
    if stress:
        knobs.update(lag_jitter=(3, 25), drop=.1, late_plays=.15, no_wallclock=.5)
    mine = set(cfg.get(f"teams.{replays[0].league.value}") or [])
    rep = backtest.run(replays, cfg, mine, step=step, feed_lag=feed_lag, delay=after_feed_delay,
                       on_tick=audit.observe, **knobs)
    card = sportsgrade.grade(replays, rep.switches, mine, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
    switches = [{**asdict(s), "historical_utc": utc(s.t), "session_elapsed_s": clock.elapsed_at(s.t)}
                for s in rep.switches]
    return {"scenario": "combined_faults" if stress else "baseline", "seed": seed,
            "clock": {"historical_start_utc": utc(start), "speed": 1,
                      "mapping": "historical_time = historical_start + session_elapsed_s; no current wallclock used",
                      "assumed_feed_lag_s": feed_lag, "assumed_after_feed_delay_s": after_feed_delay,
                      "estimated_total_screen_delay_s": feed_lag+after_feed_delay,
                      "true_total_screen_delay_s": true_total},
            "scorecard": {**card.summary(), "big_seen_count": card.big_seen,
                          "big_live_count": card.big_live, "crunch_live_count": card.crunch_live},
            "exit_audit": audit.summary(), "switches": switches,
            "actual_device_switch_latency": None,
            "assumed_tune_s": 5.0,
            "limit": "Selection simulation; does not execute the asynchronous hardware controller."}


def cues(replays, count=8):
    result = []
    for r in replays:
        previous = None
        found = []
        for p, wall in zip(r.plays, r.wall):
            if sportsgrade.is_big(r.league, p, previous):
                found.append({"play_id": str(p.get("id", "")), "event_utc": utc(wall),
                              "raw_event_utc": p.get("wallclock"), "label": p.get("text", ""),
                              "period": espn._get(p, "period", "number"),
                              "game_clock": espn._get(p, "clock", "displayValue")})
            previous = p
        result.append({"game": r.gid, "matchup": r.matchup, "anchors": found[:count],
                       "note": "Match an exact visible outcome in the provider replay; these are not VOD offsets."})
    return result

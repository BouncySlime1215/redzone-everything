"""Rebuild the NFL RedZone show a control room would plausibly have aired, from ESPN play-by-play. RECONSTRUCTION.

THIS IS A RECONSTRUCTION, NOT A RECORD OF WHAT AIRED. No public record of RedZone's cuts exists (searched:
docs/agents/data/2026-11-09-redzone-viewers-production.md). Every row here is what a written rule set picks from
play-by-play. Real cut times, dwell, split screens, replay choices and host lines are not in the data. Use the rows
only as weak labels for cut PACING; never quote them as what RedZone did.

    python scripts/redzone_reconstruct.py --archive data/archive --out /tmp/rz [--nights 20250907,...] [--delay-s 7]

Writes to --out, which must be outside the repo (the repo is public): cuts.csv (time, from, to, reason, rule, one row
per cut), windows.csv (10-minute pacing), summary.json (checks against the published numbers).

Dev-only: Sunday nights on the fit side of the split (DevOnly in scripts/coverage_audit.py). A locked or fresh night
is refused by name before any file opens; asking for one exits 2. Show = Sunday games kicking off 12:00-18:00 ET
(the international morning and Sunday night games are not on RedZone, by assumption).

The policy is one per-second loop with one decider, in air time. The control room may see plays up to delay_s ahead of
air and nothing further (delay_s is an assumption: 7 s, a normal broadcast delay, less than the 8 s play length, so it
never reveals how a play ended before the play starts). A cut is taken only at a dead ball in BOTH games (Rule #1).
Rules from "What to copy from how RedZone cuts" in that report, by its number:
  1 trigger order: score, red-zone entry, turnover, late one-possession game (redzone/lab/leverage.py, within 8)
  2 follow events, not a clock; hard ceiling 3 cuts in any 60 s (published peak). Nothing else targets a pace.
  3 one decider; 4 decide last (the pick is re-made every second until the cut commits)
  5 late narrowing: when no live game is before Q4 and a close-late game exists, only scores and close games count
  6 a score or turnover missed live is replayed within 60 s or dropped; 8 hold 7 s on a score
  10 wait for the dead ball; 12 last game standing: stay
Not applied: 7 (the only visit budgets are NBA contract terms), 9 (no host here), 11 (RedZone has no favourite team).
Two rules are not from the report and exist because the show needs them: leave a game at a break (no commercials: an
ESPN timeout/period record, or no snap for 60 s = 40 s play clock + play + margin), and when every game is quiet fall
back to the deepest drive that snapped within one play clock ("a strong drive"), only when the game on air is at a
break. Two equal live triggers whip to the game waiting longest for its snap.

Measured, not assumed: ESPN `wallclock` is close to the snap. On the dev Sundays the gap to the next play grows
with the gain (median 40 s under 10 yards, 46 s on 30+) while the gap before the play does not (42-43 s).
Pacing check: only the 8.9 TD/h and the 3 cuts/min peak are published. The 5 and 9-14 per 10 minutes are our own count
of the fewest cuts that show every event (R-NUMBERS), and 3/min is enforced here, so it cannot validate anything.

Why: cut pacing has no labels because RedZone's real cuts are unpublished; this is the closest honest substitute
(data job 13), and it says so everywhere it writes.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import statistics
import sys
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from redzone.lab import leverage  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.moments.detect import detect, parse_ts  # noqa: E402
from scripts.coverage_audit import DevOnly, LockedNightError  # noqa: E402

NOTICE = ("RECONSTRUCTION, NOT A RECORD OF WHAT AIRED: rule-based guess from ESPN play-by-play; no public record of "
          "RedZone's cuts exists. Weak labels for cut pacing only.")
ET = ZoneInfo("America/New_York")
SCORE, RZ, TURNOVER, LATE, IDLE, FILLER, MUST_LEAVE = 1, 2, 3, 4, 5, 6, 9   # filler is for breaks only
BREAK_TYPES = {"official timeout", "timeout", "two-minute warning", "end period", "end of half", "end of regulation"}
END_TYPE = "end of game"
RULE = {SCORE: "copy.1.score", RZ: "copy.1.rz_entry", TURNOVER: "copy.1.turnover", LATE: "copy.1.late_close",
        FILLER: "added.strong_drive"}
REASON = {SCORE: "score", RZ: "rz_entry", TURNOVER: "turnover", LATE: "late_close", FILLER: "strong_drive"}
WINDOW_S = 600
PUBLISHED = {"touchdowns_per_hour": 8.9, "peak_cuts_per_minute": 3}     # the only two figures RedZone-side sources give
MEASURED_NEED = {"median_per_10": 5, "p90_per_10": 9, "max_per_10": 14}  # R-NUMBERS: our count, not published


@dataclass(frozen=True)
class Params:
    delay_s: int = 7             # how far ahead of air the control room can see (assumption, see docstring)
    play_s: int = 8              # scrimmage play length after the snap
    kick_play_s: int = 12        # kickoff or punt
    break_gap_s: int = 60        # no snap for this long = a break (40 s play clock + play + margin)
    play_clock_s: int = 40       # a fallback game must have snapped within one play clock (else: heading to break)
    replay_window_s: int = 60    # copy.6
    score_hold_s: int = 7        # copy.8, one replay
    max_cuts_per_60s: int = 3    # copy.2, published peak
    whip_margin_s: int = 10      # a same-rank whip must reach a game waiting this much longer


@dataclass(frozen=True)
class Rec:
    t: int
    is_play: bool
    end: int              # when the outcome is known (t for non-plays)
    brk: bool
    over: bool
    period: int
    high_leverage: bool
    yards_to_goal: int | None


@dataclass
class Trigger:
    game: str
    rank: int
    kind: str
    t: int                # when it happened on the field
    known_at: int
    until: int            # last second it may still cause a cut
    play_id: str = ""
    served: str = ""      # live | replay | dropped


@dataclass
class Game:
    gid: str
    label: str
    recs: list[Rec]
    triggers: list[Trigger]
    times: list[int] = field(init=False)
    snaps: list[Rec] = field(init=False)
    snap_times: list[int] = field(init=False)

    def __post_init__(self):
        self.times = [r.t for r in self.recs]
        self.snaps = [r for r in self.recs if r.is_play]
        self.snap_times = [r.t for r in self.snaps]

    @property
    def first(self) -> int:
        return self.recs[0].t

    @property
    def last(self) -> int:
        return self.recs[-1].t

    def known(self, now: int, delay: int) -> Rec | None:
        i = bisect.bisect_right(self.times, now + delay)
        return self.recs[i - 1] if i else None

    def last_snap(self, now: int, delay: int) -> int | None:
        i = bisect.bisect_right(self.snap_times, now + delay)
        return self.snap_times[i - 1] if i else None

    def in_play(self, now: int) -> bool:
        i = bisect.bisect_right(self.snap_times, now)
        return bool(i) and now < self.snaps[i - 1].end

    def is_break(self, now: int, p: Params) -> bool:
        r, s = self.known(now, p.delay_s), self.last_snap(now, p.delay_s)
        if r is None or r.over or r.brk or s is None:
            return True
        return now - s > p.break_gap_s


@dataclass(frozen=True)
class Cut:
    t: int
    from_game: str
    to_game: str
    reason: str
    rule: str
    rank: int
    known_at: int
    play_id: str = ""


@dataclass
class Show:
    start: int
    end: int
    games: list[Game]
    cuts: list[Cut]                     # cuts[0] is the opening shot (rule "open"), not a cut
    air: list[str]                      # game id on air, one per second from start

    @property
    def real_cuts(self) -> list[Cut]:
        return [c for c in self.cuts if c.rule != "open"]

    def on_air(self, t: int) -> str:
        return self.air[t - self.start]

    def why(self, t: int) -> Cut:
        return self.cuts[bisect.bisect_right([c.t for c in self.cuts], t) - 1]

    def touchdowns(self) -> list[Trigger]:
        return [x for g in self.games for x in g.triggers if x.kind == "td"]


def _played(p: dict) -> bool:
    return "no play" not in (p.get("text") or "").lower()


def load_game(summary: dict, gid: str, p: Params = Params()) -> Game | None:
    """One archived summary -> records and triggers, or None without usable wallclocks."""
    drives = (summary.get("drives") or {}).get("previous") or []
    recs, by_id, drive_end, prev_end, last_t = [], {}, {}, {}, None
    for n, drive in enumerate(drives):
        for raw in drive.get("plays") or []:
            t0 = parse_ts(raw.get("wallclock"))
            if not t0:
                continue
            t = int(t0) if last_t is None else max(int(t0), last_t)    # the feed sometimes steps back a second
            last_t = t
            ty = ((raw.get("type") or {}).get("text") or "").lower()
            over, brk = ty == END_TYPE, ty in BREAK_TYPES
            is_play = not (over or brk)
            end = t + (p.kick_play_s if "kickoff" in ty or "punt" in ty else p.play_s) if is_play else t
            yards = (raw.get("end") or {}).get("yardsToEndzone")
            if yards is None:
                yards = (raw.get("start") or {}).get("yardsToEndzone")
            rec = Rec(t, is_play, end, brk, over, ((raw.get("period") or {}).get("number") or 0),
                      bool(leverage.high_leverage(League.NFL, raw)), yards)
            pid = str(raw.get("id") or "")
            prev_end[pid] = recs[-1].end if recs else t
            drive_end[n] = end
            by_id[pid] = (rec, raw, n)
            recs.append(rec)
    if not recs:
        return None
    triggers: dict[str, Trigger] = {}
    for m in detect("nfl", summary):
        if m.play_id not in by_id:
            continue
        rec, raw, n = by_id[m.play_id]
        ty = ((raw.get("type") or {}).get("text") or "")
        kind = m.kind
        if kind == "turnover" and ty == "Safety":
            kind = "safety"
        scored = kind in ("td", "safety") or (kind == "fg" and raw.get("scoringPlay") and _played(raw))
        window = rec.t + p.replay_window_s
        if scored:
            new = Trigger(gid, SCORE, kind, rec.t, rec.end, window, m.play_id)
        elif kind == "turnover":
            new = Trigger(gid, TURNOVER, kind, rec.t, rec.end, window, m.play_id)
        elif kind == "rz_trip":
            known = min(prev_end[m.play_id], rec.t)              # the ball was inside the 20 when the last play ended
            new = Trigger(gid, RZ, kind, rec.t, known, max(drive_end[n], known), m.play_id)
        else:
            continue
        key = f"{m.play_id}:{'rz' if new.rank == RZ else 'event'}"   # a return TD is one event: the top rank wins
        if key not in triggers or new.rank < triggers[key].rank:
            triggers[key] = new
    comp = (summary.get("header") or {}).get("competitions") or [{}]
    names = [(c.get("team") or {}).get("abbreviation") or "?" for c in sorted(
        comp[0].get("competitors") or [], key=lambda c: c.get("homeAway") != "away")]
    return Game(gid, "@".join(names) or gid, recs, sorted(triggers.values(), key=lambda x: (x.known_at, x.rank)))


def candidates(games, now, p, narrowing, on_air):
    """Every game that could be on air now: (rank, order key, game, trigger or None). Reads only now + delay."""
    out = []
    for g in games:
        r = g.known(now, p.delay_s)
        if r is None or r.over:
            continue
        brk = g.is_break(now, p)
        close = r.high_leverage
        active = [x for x in g.triggers if not x.served and x.known_at - p.delay_s <= now <= x.until]
        for x in active:
            live_only = x.rank == RZ
            if (live_only and brk) or (narrowing and x.rank != SCORE and not close):
                continue
            waited = now - (g.last_snap(now, p.delay_s) or now)
            out.append((x.rank, -waited if live_only else x.known_at, g, x))
        if close and not brk:
            waited = now - (g.last_snap(now, p.delay_s) or now)
            out.append((LATE, -waited, g, None))
        waited = now - (g.last_snap(now, p.delay_s) or now)
        if not brk and g.gid != on_air and waited <= p.play_clock_s and not (narrowing and not close):
            yards = next((s.yards_to_goal for s in reversed(g.recs[:bisect.bisect_right(g.times, now + p.delay_s)])
                          if s.yards_to_goal is not None), 100)
            out.append((FILLER, yards, g, None))
    return out


def simulate(games: list[Game], p: Params = Params()) -> Show:
    """The one decider. Walks air time second by second; only reads what is known at now + delay_s."""
    games = sorted(games, key=lambda g: (g.first, g.gid))
    for g in games:
        for x in g.triggers:
            x.served = ""
    by_id = {g.gid: g for g in games}
    start, end = games[0].first, max(g.last for g in games)
    current = games[0].gid
    cuts = [Cut(start, "", current, "open", "open", 0, start)]
    air: list[str] = []
    recent: deque[int] = deque()
    hold_until, aired_snap = start, False
    for now in range(start, end + 1):
        on = by_id[current]
        aired_snap = aired_snap or (now in on.snap_times)
        air.append(current)
        for g in games:
            for x in g.triggers:
                if x.served or x.rank == RZ:
                    continue
                if now > x.until:
                    x.served = "dropped"
                elif g.gid == current and x.known_at - p.delay_s <= now:
                    live = x.t >= start and air[x.t - start] == current if x.t <= now else True
                    x.served = "live" if live else "replay"
                    if x.rank == SCORE:
                        hold_until = max(hold_until, x.known_at + p.score_hold_s)
        while recent and now - recent[0] >= 60:
            recent.popleft()
        if now < hold_until or on.in_play(now) or len(recent) >= p.max_cuts_per_60s:
            continue
        live = [g for g in games if (r := g.known(now, p.delay_s)) is not None and not r.over]
        states = [g.known(now, p.delay_s) for g in live]
        narrowing = bool(states) and all(r.period >= 4 for r in states) and any(r.high_leverage for r in states)
        cands = [c for c in candidates(games, now, p, narrowing, current) if not c[2].in_play(now)]
        mine = [c[0] for c in cands if c[2].gid == current and c[0] != FILLER]
        on_rec = on.known(now, p.delay_s)
        must_leave = on_rec is None or on_rec.over or on.is_break(now, p)
        my_rank = MUST_LEAVE if must_leave else min(mine, default=IDLE)
        others = sorted((c for c in cands if c[2].gid != current), key=lambda c: (c[0], c[1], c[2].gid))
        if not others:
            continue
        rank, order, dest, trig = others[0]
        whip = False
        if rank == my_rank and rank in (RZ, LATE) and aired_snap:
            mine_wait = now - (on.last_snap(now, p.delay_s) or now)
            whip = -order - mine_wait >= p.whip_margin_s
        if not (rank < my_rank or whip):                # FILLER (6) only ever beats MUST_LEAVE (9)
            continue
        rule, reason = RULE[rank], REASON[rank]
        if whip:
            reason = f"whip:{reason}"
        if rank == FILLER and len(live) == 1:
            rule, reason = "copy.12.last_game", "last_game"
        elif narrowing and rank in (LATE, RZ, TURNOVER):
            rule = "copy.5.late_narrowing"
        elif rank == FILLER:
            reason = "break:strong_drive"
        if trig is not None and trig.rank != RZ:
            trig.served = "replay"
            if trig.rank == SCORE:
                hold_until = now + p.score_hold_s
        cuts.append(Cut(now, current, dest.gid, reason, rule, rank, trig.known_at if trig else now,
                        trig.play_id if trig else ""))
        recent.append(now)
        current, aired_snap = dest.gid, False
        air[-1] = current
    return Show(start, end, games, cuts, air)


def _iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def windows(show: Show) -> list[dict]:
    """Per 10-minute window: games live at its middle, cuts, touchdowns on the field and shown."""
    tds = show.touchdowns()
    out = []
    for w0 in range(show.start, show.end + 1, WINDOW_S):
        w1 = w0 + WINDOW_S
        mid = w0 + WINDOW_S // 2
        out.append({"start": _iso(w0), "games_live": sum(g.first <= mid <= g.last for g in show.games),
                    "cuts": sum(w0 <= c.t < w1 for c in show.real_cuts),
                    "event_cuts": sum(w0 <= c.t < w1 and not c.reason.startswith("break") for c in show.real_cuts),
                    "tds": sum(w0 <= x.t < w1 for x in tds),
                    "tds_shown": sum(w0 <= x.t < w1 and x.served in ("live", "replay") for x in tds)})
    return out


def peak_per_60s(cuts: list[Cut]) -> int:
    times, best, lo = [c.t for c in cuts], 0, 0
    for hi, t in enumerate(times):
        while t - times[lo] >= 60:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def mid_play_cuts(show: Show) -> int:
    """Independent re-check of Rule #1 over the finished show: cuts where either game was in a play."""
    by_id = {g.gid: g for g in show.games}
    return sum(by_id[c.to_game].in_play(c.t) or by_id[c.from_game].in_play(c.t) for c in show.real_cuts)


def summarize(shows: dict[str, Show], p: Params) -> dict:
    """Pacing and the comparison to published figures. Nothing here feeds back into the rules."""
    rows = [w for s in shows.values() for w in windows(s)]
    busy = [w for w in rows if w["games_live"] >= 4]
    hours = sum(s.end - s.start for s in shows.values()) / 3600
    tds = [x for s in shows.values() for x in s.touchdowns()]
    shown = sum(x.served in ("live", "replay") for x in tds)
    per_10 = sorted(w["cuts"] for w in busy) or [0]
    events = sorted(w["event_cuts"] for w in busy) or [0]
    return {"notice": NOTICE, "sundays": sorted(shows), "params": asdict(p),
            "games": sum(len(s.games) for s in shows.values()),
            "cuts": sum(len(s.real_cuts) for s in shows.values()), "show_hours": round(hours, 1),
            "mid_play_cuts": sum(mid_play_cuts(s) for s in shows.values()),
            "tds_on_field": len(tds), "tds_live": sum(x.served == "live" for x in tds),
            "tds_replay": sum(x.served == "replay" for x in tds),
            "tds_dropped": sum(x.served not in ("live", "replay") for x in tds),
            "tds_shown_per_hour_whole_show": round(shown / hours, 2) if hours else None,
            "tds_shown_per_hour_4plus_windows": round(sum(w["tds_shown"] for w in busy) / (len(busy) / 6), 2)
            if busy else None,
            "windows_4plus": len(busy), "cuts_per_10_4plus": {
                "mean": round(statistics.fmean(per_10), 2), "median": statistics.median(per_10),
                "p75": per_10[int(len(per_10) * .75)], "p90": per_10[int(len(per_10) * .9)], "max": per_10[-1]},
            "event_cuts_per_10_4plus": {"median": statistics.median(events), "p90": events[int(len(events) * .9)],
                                        "max": events[-1]},
            "peak_cuts_in_60s": max((peak_per_60s(s.real_cuts) for s in shows.values()), default=0),
            "cuts_by_reason": dict(Counter(c.reason for s in shows.values() for c in s.real_cuts).most_common()),
            "published": PUBLISHED, "measured_need_not_published": MEASURED_NEED}


def inside_repo(path: Path) -> bool:
    return path.resolve().is_relative_to(ROOT.resolve())


def write_outputs(out: Path, shows: dict[str, Show], summary: dict) -> None:
    if inside_repo(out):
        raise ValueError(f"{out}: generated rows stay out of the repo (it is public); use a temp folder")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cuts.csv").open("w", newline="") as f:
        f.write(f"# {NOTICE}\n")
        w = csv.writer(f)
        w.writerow(["sunday", "time_utc", "from", "to", "reason", "rule", "knew_at_utc", "play_id"])
        for night, s in sorted(shows.items()):
            for c in s.real_cuts:
                w.writerow([night, _iso(c.t), c.from_game, c.to_game, c.reason, c.rule, _iso(c.known_at), c.play_id])
    with (out / "windows.csv").open("w", newline="") as f:
        f.write(f"# {NOTICE}\n")
        w = csv.writer(f)
        w.writerow(["sunday", "window_start_utc", "games_live", "cuts", "event_cuts", "tds", "tds_shown"])
        for night, s in sorted(shows.items()):
            for x in windows(s):
                w.writerow([night, x["start"], x["games_live"], x["cuts"], x["event_cuts"], x["tds"], x["tds_shown"]])
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")


def _in_show(summary: dict) -> bool:
    comps = (summary.get("header") or {}).get("competitions") or [{}]
    t = parse_ts(comps[0].get("date"))
    if not t:
        return False
    kick = dt.datetime.fromtimestamp(t, ET)
    return kick.weekday() == 6 and 12 <= kick.hour < 18


def sundays(dev: DevOnly, asked: list[str] | None) -> list[str]:
    """Dev Sundays to build. A locked or fresh night asked for by name raises before anything opens."""
    if asked:
        dev.require(asked)
        return sorted(asked)
    return sorted(n for n in dev.nights if dev.allows(n) and dt.datetime.strptime(n, "%Y%m%d").weekday() == 6)


def build(archive: Path, nights: list[str] | None, p: Params) -> tuple[dict[str, Show], int]:
    dev = DevOnly(archive, "nfl")
    wanted = set(sundays(dev, nights))
    by_night: dict[str, list[Game]] = {n: [] for n in sorted(wanted)}
    skipped = 0
    for gid, night in sorted(dev.game_ids().items()):
        if night not in wanted:
            continue
        path = archive / "nfl" / f"summary_{gid}.json"
        if not path.exists():
            skipped += 1
            continue
        summary = json.loads(path.read_text())
        if not _in_show(summary):
            continue
        game = load_game(summary, gid, p)
        if game is None:
            skipped += 1
            continue
        by_night[night].append(game)
    return {n: simulate(g, p) for n, g in by_night.items() if g}, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="a folder outside the repo (the repo is public)")
    ap.add_argument("--nights", default="", help="comma list of Sundays; exit 2 if any is locked or fresh")
    ap.add_argument("--delay-s", type=int, default=Params.delay_s)
    a = ap.parse_args(argv)
    if inside_repo(a.out):
        print(f"refused: {a.out} is inside the repo, which is public; use a temp folder", file=sys.stderr)
        return 2
    params = Params(delay_s=a.delay_s)
    asked = [n.strip() for n in a.nights.split(",") if n.strip()] or None
    try:
        shows, skipped = build(a.archive, asked, params)
    except LockedNightError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    summary = summarize(shows, params) | {"games_skipped_no_data": skipped}
    write_outputs(a.out, shows, summary)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

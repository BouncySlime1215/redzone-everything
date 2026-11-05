"""Tonight's plan: every game on today's slate, where it will be watched, and whether that was
checked on the hidden stick before it started.

    build_plan()   today's games -> one entry each: start, chosen source + fallbacks,
                   blackout/skip status, your-team priority, known gaps (missing channel codes...)
    Tonight        the engine's daily job: rebuilds the plan from the live slate, saves
                   data/plan_YYYYMMDD.json, and ~20 minutes before each start window, while
                   autopilot is idle, tunes each planned source on the HIDDEN stick and records
                   verified / failed. It never touches the stick on screen, never runs in dry run,
                   and steps aside for any switch (a switch cancels the check like a preload).

scripts/plan_tonight.py prints and writes the same plan without the engine (no device work).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from redzone.channels import PRETTY, describe
from redzone.models import GameState, League, Phase, league_order

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
DATA = Path(__file__).resolve().parent.parent / "data"

LEAD_S = 20 * 60          # checks start this long before a start window
STOP_S = 2 * 60           # ...and no new check starts this close to the start
WINDOW_S = 15 * 60        # games starting within 15 minutes of the first form one window
GAP_S = 20.0              # breathing room between two checks
REBUILD_S = 60.0          # the plan follows the slate (new links, your corrections) every minute
LEAGUE_POS = {League(v): i for i, v in enumerate(league_order())}   # [one brain, step 1] models.LEAGUE_ORDER
WARMUP_LEAD_S = 10 * 60   # [warmup.pregame] the first window's likely first game goes on the hidden stick this early
WARMUP_LATE_S = 30 * 60   # ...and is kept until it goes live: listed starts run late (tip / kick ~5-15 min after)

# Measured on the real sticks (2026-11-02). Shown next to any game that depends on the app.
APP_NOTES = {
    "espn": "ESPN+ events have not started in the ESPN app on these sticks yet",
    "peacock": "Peacock is signed in on one stick only (4K 2023)",
}


# --- building ---------------------------------------------------------------------------

def day_of(ts: float) -> date:
    return datetime.fromtimestamp(ts, tz=ET).date()


def path_for(day: date, data_dir: Path = DATA) -> Path:
    return Path(data_dir) / f"plan_{day.strftime('%Y%m%d')}.json"


def _clock(ts: float | None) -> str:
    if not ts:
        return "time TBD"
    return datetime.fromtimestamp(ts, tz=ET).strftime("%-I:%M %p")


def _src(x) -> dict | None:
    target = getattr(x, "target", None)
    if not target:
        return None
    return {"target": target, "label": getattr(x, "label", "") or describe(target),
            "tunable": bool(getattr(x, "tunable", False)), "confidence": getattr(x, "confidence", ""),
            "app": target.partition(":")[0]}


def gaps_for(target: str | None, label: str, tunable: bool) -> list[str]:
    """What's missing before this game can be put on the TV, in plain English."""
    if not target:
        return [label or "No way to watch this game was found"]
    out = []
    app, _, rest = target.partition(":")
    if not tunable:
        if app == "youtubetv" and rest == "st":
            out.append("Sunday Ticket channel not matched yet (the scan runs before kickoff)")
        elif app == "youtubetv" and rest == "espnplus":
            out.append("ESPN+ event link not found yet")
        elif app == "youtubetv":
            out.append(f"No YouTube TV code for {PRETTY.get(rest, rest.upper())}: paste its link in Settings")
        elif app == "prime":
            out.append("Prime Video event link not found yet")
        else:
            out.append(f"{describe(target)} can't be tuned yet")
    if "black" in (label or "").lower():
        out.append("May be blacked out here")
    if app in APP_NOTES:
        out.append(APP_NOTES[app])
    return out


def _status(src: dict | None, label: str) -> tuple[str, str]:
    """ready | needs setup | blackout | skip, and why."""
    low = (label or "").lower()
    if src is None:
        return ("blackout" if "black" in low else "skip"), label or "No way to watch this game was found"
    if not src["tunable"]:
        return "needs setup", label
    return "ready", ""


def build_plan(games: Iterable[GameState], choose: Callable[[GameState], object], *, my_teams=None,
               day: date | None = None, now: float | None = None, leagues: Iterable[League] | None = None) -> dict:
    """One entry per game starting on `day` (Eastern) or live now. `choose(g)` is the engine's
    source pick (a Pick with .alternates, or a plain Where)."""
    now = time.time() if now is None else now
    day = day or day_of(now)
    allowed = set(leagues) if leagues is not None else set(League)
    mine = {League(k) if not isinstance(k, League) else k: set(v) for k, v in (my_teams or {}).items()
            if (k in League._value2member_map_ or isinstance(k, League))}
    entries = []
    for g in games:
        if g.league not in allowed or g.phase == Phase.POST:
            continue
        if g.phase != Phase.LIVE and (not g.start_time or day_of(g.start_time) != day):
            continue
        try:
            pick = choose(g)
        except Exception:
            log.exception("plan: choosing a source failed for %s", g.game_id)
            pick = None
        src = _src(pick)
        label = getattr(pick, "label", "") or ""
        status, why = _status(src, label)
        fallbacks = [f for f in (_src(a) for a in getattr(pick, "alternates", None) or []) if f]
        gaps = gaps_for(src and src["target"], label, bool(src and src["tunable"]))
        backup = next((f for f in fallbacks if f["tunable"]), None)
        if src and not src["tunable"] and backup:
            status, why = "ready", f"{label} isn't ready; using {backup['label']}"
            gaps += [APP_NOTES[backup["app"]]] if backup["app"] in APP_NOTES else []
        teams = mine.get(g.league, set()) & {g.home.abbr, g.away.abbr}
        entries.append({
            "game_id": g.game_id, "league": g.league.value, "matchup": g.matchup,
            "away": g.away.abbr, "home": g.home.abbr, "start": g.start_time, "start_et": _clock(g.start_time),
            "phase": g.phase.value, "mine": bool(teams),
            "priority": f"your team ({', '.join(sorted(teams))})" if teams else "",
            "source": src, "fallbacks": fallbacks[:4], "status": status, "why": why,
            "gaps": gaps,
            "check": {"state": "pending" if status == "ready" else "n/a", "target": None, "at": None, "detail": ""},
        })
    entries.sort(key=lambda e: (e["start"] or 0, not e["mine"], LEAGUE_POS.get(League(e["league"]), 9)))
    return {"day": day.isoformat(), "built": now, "games": entries, "windows": windows(entries),
            "gaps": sorted({x for e in entries for x in e["gaps"]}), "note": ""}


def windows(entries: list[dict]) -> list[dict]:
    """Start windows: the first start, and every game starting within WINDOW_S of it."""
    out: list[dict] = []
    for e in sorted((e for e in entries if e["start"]), key=lambda e: e["start"]):
        if out and e["start"] - out[-1]["start"] <= WINDOW_S:
            out[-1]["games"].append(e["game_id"])
        else:
            out.append({"start": e["start"], "start_et": e["start_et"], "check_from": e["start"] - LEAD_S,
                        "games": [e["game_id"]]})
    return out


def _target(e: dict) -> str | None:
    """What the check tunes: the chosen source, or the first tunable fallback."""
    src = e.get("source")
    if src and src["tunable"]:
        return src["target"]
    return next((f["target"] for f in e.get("fallbacks") or [] if f["tunable"]), None)


def carry_checks(new: dict, old: dict | None) -> dict:
    """Keep check results across rebuilds while the game's source is unchanged."""
    if not old or old.get("day") != new.get("day"):
        return new
    prev = {e["game_id"]: e for e in old.get("games", [])}
    for e in new["games"]:
        p = prev.get(e["game_id"])
        if p and p.get("check", {}).get("state") not in (None, "pending", "n/a") \
                and p["check"].get("target") == _target(e):
            e["check"] = dict(p["check"])
    new["note"] = old.get("note", "")
    return new


def record_check(plan: dict, target: str, ok: bool, detail: str, at: float) -> int:
    """Mark every pending game that plays on `target`. Returns how many were marked."""
    n = 0
    for e in plan["games"]:
        if e["check"]["state"] == "pending" and _target(e) == target:
            e["check"] = {"state": "verified" if ok else "failed", "target": target, "at": at,
                          "detail": detail}
            n += 1
    return n


def expire(plan: dict, now: float, why: str = "") -> None:
    """A game that started before its check ran: say so instead of 'pending' forever."""
    for e in plan["games"]:
        if e["check"]["state"] == "pending" and e["start"] and now >= e["start"] - STOP_S:
            e["check"] = {"state": "not checked", "target": _target(e), "at": now,
                          "detail": why or "it started before a check could run"}


def due(plan: dict, now: float, lead_s: float = LEAD_S) -> list[str]:
    """Targets to check now: pending games starting within lead_s (not within STOP_S); your
    teams' games first, then by start. One check per target covers every game on it."""
    out: list[str] = []
    cands = [e for e in plan["games"] if e["check"]["state"] == "pending" and e["start"]
             and e["start"] - lead_s <= now < e["start"] - STOP_S]
    for e in sorted(cands, key=lambda e: (not e["mine"], e["start"])):
        t = _target(e)
        if t and t not in out:
            out.append(t)
    return out


def warmup_target(plan: dict, now: float, lead_s: float = WARMUP_LEAD_S) -> tuple[str, float] | None:
    """[warmup.pregame] (target, window start) to warm now: the night's FIRST start window, from lead_s
    before its listed start until a game goes live (at most WARMUP_LATE_S after the listed start). Its likeliest first pick: your team, then league
    order (models.LEAGUE_ORDER: NFL > CFB > NBA > CBB > NHL > MLB), then the earliest start. None outside that time or with no tunable game."""
    if any(e["phase"] == Phase.LIVE.value for e in plan["games"]):
        return None
    first = next(iter(plan.get("windows") or []), None)
    if first is None or not (first["start"] - lead_s <= now < first["start"] + WARMUP_LATE_S):
        return None
    ids = set(first["games"])
    cands = [e for e in plan["games"] if e["game_id"] in ids and e["status"] == "ready" and _target(e)]
    cands.sort(key=lambda e: (not e["mine"], LEAGUE_POS.get(League(e["league"]), 9), e["start"] or 0))
    return (_target(cands[0]), first["start"]) if cands else None


def save(plan: dict, data_dir: Path = DATA) -> Path:
    p = path_for(date.fromisoformat(plan["day"]), data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=1))
    tmp.replace(p)
    return p


def load(day: date, data_dir: Path = DATA) -> dict | None:
    p = path_for(day, data_dir)
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("games"), list) else None


def fallback_labels(e: dict) -> list[str]:
    """Fallbacks for people: one line per distinct label ("Prime Video (3 feeds)")."""
    seen: dict[str, list[dict]] = {}
    for f in e.get("fallbacks") or []:
        seen.setdefault(f["label"], []).append(f)
    return [f"{label}{f' ({len(fs)} feeds)' if len(fs) > 1 else ''}"
            f"{'' if any(f['tunable'] for f in fs) else ' (not tunable yet)'}" for label, fs in seen.items()]


def text(plan: dict) -> str:
    """The plan as plain lines (scripts/plan_tonight.py)."""
    lines = [f"Tonight's plan · {plan['day']} · {len(plan['games'])} games"]
    for e in plan["games"]:
        src = e["source"]["label"] if e["source"] else "-"
        star = " *" if e["mine"] else ""
        lines.append(f"  {e['start_et']:>8}  {e['league'].upper():3}  {e['matchup']}{star}  ->  {src}"
                     f"  [{e['status']}{', ' + e['check']['state'] if e['check']['state'] != 'n/a' else ''}]")
        for f in fallback_labels(e):
            lines.append(f"{'':16}fallback: {f}")
        if e["why"] and e["status"] != "ready":
            lines.append(f"{'':16}why: {e['why']}")
        for gp in e["gaps"]:
            lines.append(f"{'':16}gap: {gp}")
    if plan["windows"]:
        lines.append("Check windows: " + ", ".join(f"{w['start_et']} ({len(w['games'])})" for w in plan["windows"]))
    return "\n".join(lines)


# --- the engine's daily job ----------------------------------------------------------------

class Tonight:
    """Keeps today's plan current and runs the pre-game checks (see module docstring)."""

    def __init__(self, engine, data_dir: Path | None = None, clock: Callable[[], float] = time.time):
        self.engine = engine
        user_path = getattr(engine.cfg, "user_path", None)
        self.data_dir = Path(data_dir) if data_dir else (Path(user_path).parent if user_path else DATA)
        self.clock = clock
        self.plan: dict | None = None
        self._built_at = 0.0
        self._last_check = 0.0
        self._checking: str | None = None
        self._saved = ""

    # building
    def _choose(self, g: GameState):
        e = self.engine
        base = e.channels.where(g, sunday_ticket=e.cfg.get("sunday_ticket", True))
        try:
            return e.discovery.choose(g, base)
        except Exception:
            log.exception("plan: provider choice failed for %s", g.game_id)
            return base

    def rebuild(self) -> dict:
        now = self.clock()
        e = self.engine
        day = day_of(now)
        old = self.plan if self.plan and self.plan.get("day") == day.isoformat() else load(day, self.data_dir)
        new = build_plan(list(e.merger.games.values()), self._choose, my_teams=e.cfg.get("teams") or {},
                         day=day, now=now, leagues=e._leagues())
        new = carry_checks(new, old)
        if old and old.get("warmup"):
            new["warmup"] = old["warmup"]   # [warmup.pregame] its state outlives the minute rebuilds
        self._save_quiet(new)
        self.plan, self._built_at = new, now
        return new

    # checking
    def idle_reason(self, check: bool = True) -> str | None:
        """Why the hidden stick can't be used for a check (or, check=False, the warm-up) right now."""
        e, c = self.engine, self.engine.control
        if check and not e.cfg.get("plan.verify", True):
            return "pre-game checks are off in settings"
        if e.cfg.get("mode.dry_run", True):
            return "dry run: the sticks are not touched"
        if getattr(e.recorded, "active", False) or getattr(e.discovery, "device_suspended", False):
            return "a recorded session is using the sticks"
        if not c.two_stick or c.hidden is None:
            return "needs two sticks: the stick on screen is never used for checks"
        if getattr(c, "input_unknown", False):
            return "the TV input is unknown"
        if e.decider.user.paused:
            return "paused"
        if e._pending_decision is not None:
            return "a switch is in flight"
        if any(g.phase == Phase.LIVE and g.league in e._leagues() for g in e.merger.games.values()):
            return "games are on: autopilot owns the hidden stick"
        if e.decider.current is not None:
            return "autopilot is showing a game"
        if c._busy():
            return "the hidden stick is busy"
        return None

    async def step(self) -> None:
        now = self.clock()
        if self.plan is None or now - self._built_at >= REBUILD_S or self.plan.get("day") != day_of(now).isoformat():
            self.rebuild()
        plan = self.plan
        if self.engine.cfg.get("warmup.pregame", False) and self._warmup(plan, now):
            # The warm stick holds the first game from here on: a check of another game would re-tune it.
            expire(plan, now + LEAD_S, "not checked: the warm-up holds the hidden stick")
            self._save_quiet(plan)
            return
        targets = due(plan, now, float(self.engine.cfg.get("plan.verify_lead_min", 20)) * 60)
        why = self.idle_reason() if targets else None
        if why:
            plan["note"] = why
        expire(plan, now, f"not checked: {plan['note']}" if plan.get("note") else "")
        if not targets or why or now - self._last_check < GAP_S:
            self._save_quiet(plan)
            return
        await self._check(plan, targets[0])

    def _warmup(self, plan: dict, now: float) -> bool:
        """[warmup.pregame] Put the night's likeliest first game on the HIDDEN stick ~10 min before the
        first start, live (and held behind live when the time-shift buffer is on: a fresh hidden tune
        builds it), so the first switch is a flip. Never the stick on screen: whatever you're watching
        there stays. Returns True while the warm-up owns the hidden stick (checks then stand aside)."""
        got = warmup_target(plan, now, float(self.engine.cfg.get("warmup.lead_min", 10)) * 60)
        if got is None:
            return False
        target, start = got
        c = self.engine.control
        w = plan.setdefault("warmup", {})
        if c.standby == (target, None) and c.hidden_ready:
            if w.get("state") != "ready":
                w.update(target=target, state="ready", at=now, window=start)
                log.info("warm-up: %s is ready on the hidden stick %.0f min before the first start",
                         target, (start - now) / 60)
            return True
        why = self.idle_reason(check=False)
        if why:
            w.update(target=target, state="waiting", detail=why, window=start)
            return False
        if c.preload(target):
            w.update(target=target, state="tuning", at=now, window=start)
            log.info("warm-up: tuning %s on the hidden stick for the first start", target)
        return True

    async def _check(self, plan: dict, target: str) -> None:
        task = self.engine.control.verify_hidden(target)
        if task is None:
            plan["note"] = "the hidden stick couldn't take the check"
            return
        self._checking = target
        try:
            await asyncio.wait({task})
        finally:
            self._checking = None
        self._last_check = self.clock()
        if task.cancelled():
            plan["note"] = "a switch interrupted the check; it will retry"
            return
        if task.exception() is not None:
            plan["note"] = f"the check crashed: {task.exception()}"
            return
        res = task.result()
        if res is None:
            plan["note"] = "the hidden stick is asleep; checks wait for it to wake"
            return
        detail = f"played in {res.took:.0f}s" if res.ok else (res.error or "no video")
        record_check(plan, target, res.ok, detail, self.clock())
        plan["note"] = ""
        self._save_quiet(plan)
        if not res.ok:
            self.engine.toast(f"Pre-game check failed: {describe(target)} ({detail[:60]})", "error")

    def _save_quiet(self, plan: dict) -> None:
        """Save only when something changed (step runs every 15 s)."""
        content = _content(plan)
        if content == self._saved:
            return
        try:
            save(plan, self.data_dir)
            self._saved = content
        except OSError:
            log.exception("could not save tonight's plan")

    async def run(self, every_s: float = 15.0) -> None:
        while True:
            try:
                await self.step()
            except Exception:
                log.exception("tonight's plan failed")
            await asyncio.sleep(every_s)

    def view(self) -> dict | None:
        """The phone card: compact rows, your games first within each window."""
        p = self.plan
        if not p:
            return None
        rows = [{"game_id": e["game_id"], "league": e["league"], "matchup": e["matchup"], "start": e["start"],
                 "start_et": e["start_et"], "mine": e["mine"], "status": e["status"], "why": e["why"],
                 "source": e["source"]["label"] if e["source"] else "",
                 "fallbacks": fallback_labels(e)[:2],
                 "check": e["check"]["state"], "check_detail": e["check"]["detail"],
                 "gaps": e["gaps"][:2]} for e in p["games"]]
        count = lambda k, v: sum(1 for e in p["games"] if e[k] == v)  # noqa: E731
        return {"day": p["day"], "games": rows, "gaps": p["gaps"][:5], "note": p.get("note", ""),
                "checking": self._checking,
                "checking_games": [e["game_id"] for e in p["games"]
                                   if self._checking and e["check"]["state"] == "pending" and _target(e) == self._checking],
                "counts": {"games": len(rows), "ready": count("status", "ready"),
                           "verified": sum(1 for e in p["games"] if e["check"]["state"] == "verified"),
                           "failed": sum(1 for e in p["games"] if e["check"]["state"] == "failed")},
                "next_window": next((w for w in p["windows"] if w["start"] > self.clock()), None)}


def _content(plan: dict) -> str:
    return json.dumps({k: v for k, v in plan.items() if k != "built"}, sort_keys=True)

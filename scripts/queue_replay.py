"""Replay queue decisions from a scenario file, offline, and time them for a 32-game slate.

Why: WP-02 (P-QUEUE Q3, Q4, Q10) needs a way to read what the pure queue would do with no TV and no engine,
and contract rule K5 needs a caller until the engine wires it in (WP-12).

    python scripts/queue_replay.py                  # the built-in scenario
    python scripts/queue_replay.py --scenario f.json
    python scripts/queue_replay.py --bench          # median and p99 of one full tick plus view, 32 games
    nice -n 15 python scripts/queue_replay.py --nights --split fit --archive data/archive   # whole nights, queue on vs off

A scenario is {"games": {id: <Entry fields>}, "ticks": [...]}. A tick has `now`, `on_screen`, `hidden`, an
optional `stick` (<Stick fields>), optional `set` ({id: fields to change}) and optional `fail`
({"game", "failure", "attempt"}). Routes are lists of {"app", "ref", ...}. Pure: reads only the scenario.

Night mode (--nights). Why: the queue's top goal is "queue games fast", and the scenario replay can't say how
often a wanted game is ready when the decider wants it. This replays whole archived nights: the real decider
runs once per night (backtest.run, the grader's own loop, no stick feedback), its candidates, wants and cuts are
recorded, then the same trace drives two fake hidden sticks:
  off  today's `standby.Planner` path (queue.enabled false), failed targets sit out 120 s as control.can_tune does
  on   `gamequeue.tick` (queue.enabled true), failures answered by `gamequeue.on_failure`
Both sticks share one physics: per-app tune and buffer seconds from `gamequeue.Params` (a Prime hold is max(buffer,
behind_s)), one job at a time, a cut leaves the old game on the stick at the live edge. EVERY SECOND HERE IS A
MODELED STICK TIME until the TV test queue measures real ones (docs/test-queue.md 3a). Failures are drawn from
invented rates (--failures), the same draws for both arms. Not modeled: pins, locks, known-moment tiers (the
decider's look-ahead), alternate routes, the decider reacting to a late stick. Reads the archive only.
"""
from __future__ import annotations

import argparse
import dataclasses as dc
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.noggin import gamequeue as gq  # noqa: E402

YT, PRIME = "youtubetv", "prime"
BUILTIN = {
    "games": {
        "WSH": {"routes": [{"app": PRIME, "ref": "w", "klass": gq.LONG_LEAD}], "level": 1, "brain_rank": 1, "value": 80},
        "DAL": {"routes": [{"app": YT, "ref": "d"}], "level": 4, "value": 40},
        "GB": {"routes": [{"app": YT, "ref": "g"}, {"app": "espn", "ref": "g2", "klass": gq.LIVE_EDGE}], "level": 2,
               "brain_rank": 2, "value": 55},
        "KC": {"routes": [{"app": YT, "ref": "k"}], "level": 2, "planner_rank": 1, "value": 30},
    },
    "ticks": [
        {"now": 0, "on_screen": "DAL", "hidden": None},
        {"now": 10, "on_screen": "DAL", "hidden": "GB", "set": {"GB": {"level": 4}}},
        {"now": 20, "on_screen": "DAL", "hidden": "GB",
         "set": {"KC": {"tier": 1, "join_in_s": -5, "lookahead_s": 75, "value": 30}}},
        {"now": 30, "on_screen": "DAL", "hidden": "GB", "fail": {"game": "KC", "failure": "never_started", "attempt": 1}},
        {"now": 40, "on_screen": "DAL", "hidden": "GB", "set": {"WSH": {"user": "lock"}}},
        {"now": 50, "on_screen": "DAL", "hidden": "WSH", "fail": {"game": "WSH", "failure": "walled"}},
        {"now": 60, "on_screen": "DAL", "hidden": None},
    ],
}


def entry(game: str, raw: dict) -> gq.Entry:
    fields = dict(raw)
    fields["routes"] = tuple(gq.Route(**r) for r in fields.get("routes", ()))
    return gq.Entry(game=game, **fields)


def stick(raw: dict | None) -> gq.Stick:
    return gq.Stick(**(raw or {}))


def replay(scenario: dict) -> list[dict]:
    """One line per tick. A failure changes the game for the ticks after it."""
    games = {g: entry(g, raw) for g, raw in scenario["games"].items()}
    out = []
    for t in scenario["ticks"]:
        for g, change in t.get("set", {}).items():
            games[g] = dc.replace(games[g], **change)
        line = {"now": t["now"]}
        if "fail" in t:
            f = t["fail"]
            move = gq.on_failure(games[f["game"]], f["failure"], f.get("attempt", 0))
            games[f["game"]] = move.entry
            line["failure"] = {"game": f["game"], "kind": move.failure.value, "step": move.step.value,
                               "budget_s": move.budget_s, "backoff_s": move.backoff_s, "phone": move.phone}
        plan = gq.tick(list(games.values()), t.get("on_screen"), t.get("hidden"), stick(t.get("stick")), float(t["now"]))
        line["queue"] = gq.view(plan)
        out.append(line)
    return out


def slate(n: int = 32) -> list[gq.Entry]:
    """A hard slate for the timing check: every rung present, several routes per game."""
    rows = []
    for i in range(n):
        routes = (gq.Route(YT, f"y{i}", search=i % 5 == 0), gq.Route(PRIME, f"p{i}", klass=gq.LONG_LEAD))
        rows.append(gq.Entry(
            f"G{i:02d}", routes[::1 if i % 3 else -1], level=1 + i % 5, user=("", "pin", "lock", "")[i % 4] if i % 7 == 0 else "",
            tier=(None, 1, 2)[i % 3], join_in_s=-(i % 30.0) if i % 3 else None, lookahead_s=75.0,
            lead_s=float(i * 8), brain_rank=float(i) if i % 2 else None, planner_rank=float(i), value=float(i * 3)))
    return rows


def bench(runs: int = 2000) -> dict:
    games, s, took = slate(), gq.Stick(busy_until=105.0, job_game="G03", job_to=3), []
    for _ in range(runs):
        t0 = time.perf_counter()
        gq.view(gq.tick(games, "G00", "G01", s, 100.0))
        took.append((time.perf_counter() - t0) * 1000)
    took.sort()
    return {"games": len(games), "runs": runs, "median_ms": round(statistics.median(took), 4),
            "p99_ms": round(took[int(runs * .99)], 4), "max_ms": round(took[-1], 4)}


# ---- whole nights: queue on vs off through a fake stick ------------------------------------------------------

INF = float("inf")
FAIL_RATES = {   # per load attempt, INVENTED and uncalibrated: they exercise the Failure table, they don't measure it
    "youtubetv": ((gq.Failure.NEVER_STARTED, .04), (gq.Failure.SCRUB_FAILED, .03), (gq.Failure.BLACKOUT, .01),
                  (gq.Failure.STICK_HUNG, .01), (gq.Failure.LINK_REFUSED, .005)),
    "prime": ((gq.Failure.HOLD_FAILED, .06), (gq.Failure.NEVER_STARTED, .04), (gq.Failure.WALLED, .02),
              (gq.Failure.BLACKOUT, .01), (gq.Failure.STICK_HUNG, .01)),
}
HANG_S = 60.0            # a hung stick is unusable this long (modeled)
PLANNER_BACKOFF_S = 120.0   # control.can_tune: "no failed attempt in the last couple of minutes"
NO_BUFFER = ("replay", "resume", "skip")   # backtest.HS_NO_BUFFER: catch-up footage is not a live switch
LEAGUES = ("nfl", "cfb", "nba", "cbb", "nhl", "mlb")


@dc.dataclass
class Trace:
    """One night as the decider lived it: a tick per poll, and the cuts it made."""
    night: str
    ticks: list[dict]      # {"t", "cands", "current", "want", "opts"}
    switches: list[dict]   # {"t", "frm", "to", "kind"}


class Rec:
    """Fills a Trace from inside backtest.run: used as a HiddenStick stand-in, so the grader's own loop calls it."""


def record_night(reps, targets, cfg, mine, step=None, seed=0) -> Trace:
    """Run the real decider over one night and keep what the stick would have seen. The stand-in never alters a cut
    (keep_order and on_switch do nothing), so the decisions are the same as a run with no hidden stick."""
    from redzone import standby
    from redzone.lab import backtest as bt
    trace = Trace("", [], [])

    class Recorder(bt.HiddenStick):
        def keep_order(self, switches, watching, n_sw):
            return

        def on_switch(self, sw, watching, tg):
            trace.switches.append({"t": sw.t, "frm": sw.frm, "to": sw.to, "kind": sw.kind})

        def plan(self, cands, dec, decision, states):
            home = dec.home_break_visit if dec.home_break_visit != dec.current else None
            opts = standby.options(cands, current=dec.current, home=home, back_to=dec.stack[-1] if dec.stack else None,
                                   games=states, my_teams=self.my_teams, want=getattr(decision, "want", None),
                                   return_bonus=float(self.cfg.get("decider.switch_margin", 10)))
            trace.ticks.append({"t": self.clock.t, "cands": dict(cands), "current": dec.current,
                                "want": getattr(decision, "want", None), "opts": opts})

    real = bt.HiddenStick
    bt.HiddenStick = Recorder
    try:
        bt.run(reps, cfg, mine, targets=targets, step=step, hidden_stick="wait", seed=seed)
    finally:
        bt.HiddenStick = real
    return trace


def _app(target: str | None) -> str:
    return (target or "").partition(":")[0]


class FakeStick:
    """The hidden stick with modeled times. One job at a time; `busy` is when the job ends; `r3` and `r4` are when
    the held game reaches the live edge and the buffered level (inf = never, a failed buffer stays at R3)."""

    def __init__(self, p: gq.Params):
        self.p = p
        self.game = self.target = self.replay = None
        self.r3 = self.r4 = self.busy = 0.0

    def tune_s(self, app: str) -> float:
        return float(self.p.tune_s.get(app, self.p.tune_s["other"]))

    def buffer_s(self, app: str) -> float:
        b = float(self.p.buffer_s.get(app, self.p.buffer_s["other"]))
        return max(b, self.p.behind_s) if app in self.p.hold_apps else b

    def prep_s(self, app: str) -> float:
        return self.tune_s(app) + (0.0 if _klass(app) == gq.LIVE_EDGE else self.buffer_s(app))

    def goal_at(self) -> float:
        return self.r3 if _klass(_app(self.target)) == gq.LIVE_EDGE else self.r4

    def ready(self, game: str | None, t: float) -> bool:
        return game is not None and self.game == game and self.replay is None and t >= self.goal_at()

    def level_state(self, t: float) -> tuple[str, bool]:
        return ("ready" if t >= self.r3 else "loading"), t >= self.r4

    def load(self, game, target, replay, t, fail: gq.Failure | None = None) -> float | None:
        """Start a job. Returns when a failure shows itself (None = no failure)."""
        app = _app(target)
        self.game, self.target, self.replay = game, target, replay
        tune = self.tune_s(app)
        if replay is not None:
            self.r3 = self.r4 = self.busy = t + tune
            return None
        if fail is None:
            self.r3 = t + tune
            self.r4 = self.r3 if _klass(app) == gq.LIVE_EDGE else self.r3 + self.buffer_s(app)
            self.busy = self.r4
            return None
        F = gq.Failure
        if fail in (F.HOLD_FAILED, F.SCRUB_FAILED, F.SCRUB_BANNER_MISS):
            self.r3, self.r4 = t + tune, INF
            self.busy = self.r3 + (self.p.scrub_budget_s if fail != F.HOLD_FAILED else tune)
            return self.busy
        wasted = HANG_S if fail == F.STICK_HUNG else \
            self.p.start_budget_s.get(app, self.p.start_budget_s["other"]) if fail == F.NEVER_STARTED else tune
        self.game = self.target = None
        self.r3 = self.r4 = INF
        self.busy = t + wasted
        return self.busy

    def left_behind(self, game, target, t) -> None:
        """A cut: the stick we left holds the game we left, at the live edge, buffering from now."""
        self.game, self.target, self.replay = game, target, None
        app = _app(target)
        self.r3 = t
        self.r4 = t if _klass(app) == gq.LIVE_EDGE else t + self.buffer_s(app)
        self.busy = self.r4


def _klass(app: str) -> str:
    return {"prime": gq.LONG_LEAD, "espn": gq.LIVE_EDGE}.get(app, gq.FAST if app == "youtubetv" else gq.LONG_LEAD)


def _draw(seed, night, game, k, app, fail_on: bool) -> gq.Failure | None:
    if not fail_on:
        return None
    import random
    u = random.Random(f"{seed}:{night}:{game}:{k}").random()
    for kind, pr in FAIL_RATES.get(app) or FAIL_RATES.get("youtubetv", ()):
        if u < pr:
            return kind
        u -= pr
    return None


def simulate(arm: str, trace: Trace, cfg, p: gq.Params | None = None, *, fail_on: bool = False, seed: int = 0,
             free_gone: str = "") -> dict:
    """Drive one fake stick through a night. arm "off" is `standby.Planner`, arm "on" is `gamequeue.tick`.
    free_gone (arm "on" only) is a SUGGESTED change, not shipped. gamequeue.tick answers "hidden_unknown" (owners
    only) when the stick's game is not among the entries. "any": that game counts as no hidden game, so a reachable
    game may take the stick. "ended": only when it never comes back on the slate (what the engine knows when the game
    is final).
    Returns raw records: one per cut, the stick's idle ticks, swaps, failures and the queue's reasons."""
    from redzone import standby
    p = p or gq.Params()
    st, now = FakeStick(p), [0.0]
    planner = standby.Planner(cfg, clock=lambda: now[0])
    out = {"cuts": [], "wanted_ticks": 0, "idle_ticks": 0, "swaps": [], "failures": [], "loads": 0, "hist": {}, "why": {}}
    gs: dict[str, dict] = {}                 # on-arm per-game state: routes dropped, backoff, tries
    blocked: dict[str, float] = {}           # off-arm: target -> sit out until
    pending: list[tuple] = []                # (when, game, target, kind, tries)
    played: set[str] = set()
    last_seen = _last_seen(trace)
    sw_by_t: dict[float, list[dict]] = {}
    for sw in trace.switches:
        sw_by_t.setdefault(sw["t"], []).append(sw)
    attempts: dict[str, int] = {}

    def start(game, target, replay, t, k_try=0):
        k = attempts.get(game, 0)
        attempts[game] = k + 1
        if st.game and st.game != game and st.replay is None:
            out["swaps"].append({"t": t, "dropped": st.game, "was_ready": st.ready(st.game, t), "for": game})
        kind = _draw(seed, trace.night, game, k, _app(target), fail_on) if replay is None else None
        out["loads"] += 1
        when = st.load(game, target, replay, t, kind)
        if kind is not None:
            pending.append((when, game, target, kind, k_try))
            out["failures"].append({"t": t, "game": game, "kind": kind.value})

    for tk in trace.ticks:
        t = now[0] = tk["t"]
        cur, want, cands = tk["current"], tk["want"], tk["cands"]
        # 1. failures that show themselves by now
        for item in [x for x in pending if x[0] <= t]:
            pending.remove(item)
            _, g, target, kind, k_try = item
            if arm == "off":
                blocked[target] = t + PLANNER_BACKOFF_S
                continue
            e = _entry(g, target, gs, t)
            mv = gq.on_failure(e, kind, k_try)
            for f in reversed(out["failures"]):
                if f["game"] == g and f["kind"] == kind.value and "step" not in f:
                    f["step"], f["advance"], f["backoff_s"] = mv.step.value, mv.advance, mv.backoff_s
                    break
            s = gs.setdefault(g, {"routes": None, "back": None})
            if mv.entry is not None:
                if mv.step in (gq.Step.RETRY, gq.Step.RETUNE):
                    if st.game is None or st.game == g or t >= st.busy:
                        start(g, target, None, t, k_try + 1)
                else:
                    s["routes"] = mv.entry.routes
                    if mv.backoff_s > 0:
                        s["back"] = (t + mv.backoff_s, e.routes)
                if mv.entry.level == gq.Level.R0 and not mv.entry.routes:
                    s["routes"] = ()
        # 2. cuts at this tick: ready or not, then the stick holds the game left behind
        for sw in sw_by_t.get(t, ()):
            if sw["kind"] in NO_BUFFER:
                continue
            x = sw["to"]
            ready = st.ready(x, t)
            app = _app(next((c.target for g, c in cands.items() if g == x), None) or _target_of(trace, x))
            rem = max(0.0, st.goal_at() - t) if st.game == x and st.replay is None else st.prep_s(app)
            out["cuts"].append({"t": t, "to": x, "ready": ready, "remaining_s": 0.0 if ready else rem,
                                "held": st.game, "app": app, "wanted_at": _wanted_at(trace, x, t),
                                "kind": sw["kind"]})
            frm = sw["frm"]
            if frm:
                st.left_behind(frm, _target_of(trace, frm), t)
                played.add(frm)
            played.add(x)
        # 3. wanted-but-not-ready ticks and idle sticks
        if want and want != cur:
            out["wanted_ticks"] += 1
            if t >= st.busy and not st.ready(want, t):
                out["idle_ticks"] += 1
        # 4. plan
        if t < st.busy and t < st.r3 and st.game is None:
            continue   # a hung or failing stick is down; nothing to plan on it
        if arm == "off":
            opts = [o for o in tk["opts"] if blocked.get(o.target, 0.0) <= t]
            pick = planner.plan(opts, (st.target, st.replay))
            if pick is not None and (pick.target, pick.replay_id) != (st.target, st.replay):
                start(pick.game_id, pick.target, pick.replay_id, t)
        else:
            plan = _queue_tick(tk, st, gs, played, p, t, free_gone, last_seen)
            out["why"][plan.why] = out["why"].get(plan.why, 0) + 1
            kind_h = "none" if st.game is None else "on_screen" if st.game == cur else \
                ("ended" if last_seen.get(st.game, INF) < t else "gap") if st.game not in cands else "stale" if cands[st.game].stale else "candidate"
            out["hist"][t] = (st.game, st.ready(st.game, t), plan.want.entry.game if plan.want else None, plan.why,
                              {r.entry.game: (r.rung.name, r.level.name, round(r.prep, 1), r.reach)
                               for r in plan.rows[:6]}, kind_h)
            if plan.swap and plan.want is not None and plan.want.route is not None:
                w = plan.want
                start(w.entry.game, f"{w.route.app}:{w.route.ref}", None, t)
        out.setdefault("ticks", 0)
        out["ticks"] += 1
    return out


def _last_seen(trace: Trace) -> dict[str, float]:
    """game -> time of the last tick that had it on the slate; a game absent after that time is finished."""
    last: dict[str, float] = {}
    for tk in trace.ticks:
        for g in tk["cands"]:
            last[g] = tk["t"]
    return last


def _target_of(trace: Trace, gid: str | None) -> str | None:
    for tk in reversed(trace.ticks):
        c = tk["cands"].get(gid)
        if c is not None:
            return c.target
    return None


def _wanted_at(trace: Trace, x: str, t: float) -> float:
    """When the decider first wanted x, for the cut at t: the start of the run of ticks that wanted it, else t."""
    w = t
    for tk in reversed([k for k in trace.ticks if k["t"] <= t]):
        if tk["want"] == x and tk["current"] != x:
            w = tk["t"]
        elif tk["t"] < t:
            break
    return w


def _entry(gid: str, target: str | None, gs: dict, t: float, **kw) -> gq.Entry:
    s = gs.get(gid) or {}
    if s.get("back") and t >= s["back"][0]:
        s["routes"], s["back"] = None, None
    app, _, ref = (target or "").partition(":")
    routes = s["routes"] if s.get("routes") is not None else ((gq.Route(app, ref, _klass(app)),) if app and ref else ())
    return gq.Entry(gid, routes, **kw)


def _queue_tick(tk, st: FakeStick, gs: dict, played: set, p: gq.Params, t: float, free_gone: str = "", last_seen: dict | None = None) -> gq.Plan:
    entries = []
    state, buffered = st.level_state(t)
    for gid, c in tk["cands"].items():
        if c.stale and gid != st.game:
            continue
        rank = next((i for i, o in enumerate(tk["opts"]) if o.game_id == gid and o.replay_id is None), None)
        held = st.game == gid and st.replay is None
        lv = gq.level_of(True, gid in played, state if held else None, buffered if held else False)
        if gid == tk["current"]:
            lv = gq.Level.R4
        entries.append(_entry(gid, c.target, gs, t, level=lv, committed=gid == tk["want"],
                              planner_rank=float(rank) if rank is not None else None,
                              value=min(100.0, max(0.0, float(c.scored.score)))))
    busy = t < st.busy
    stick = gq.Stick(busy_until=st.busy if busy else 0.0, job_game=st.game if busy else None,
                     job_to=(gq.Level.R3 if _klass(_app(st.target)) == gq.LIVE_EDGE else gq.Level.R4) if busy else None)
    hidden = st.game if st.replay is None else None
    gone = hidden not in {e.game for e in entries}
    if hidden == tk["current"] or (gone and (free_gone == "any" or (free_gone == "ended" and (last_seen or {}).get(hidden, INF) < t))):
        hidden = None   # the stick plays what is on screen, or (suggested change) its game left the slate
    return gq.tick(entries, tk["current"], hidden, stick, t, p)


def score_arm(trace: Trace, raw: dict, near_s: float) -> dict:
    """Per-night counts from one arm's raw records; sum them across nights, then call `summarize`."""
    cuts = raw["cuts"]
    waits = [c["remaining_s"] + (c["t"] - c["wanted_at"]) for c in cuts]
    swaps_hit = 0
    for sw in raw["swaps"]:
        swaps_hit += any(c["to"] == sw["dropped"] and 0 <= c["t"] - sw["t"] <= near_s for c in cuts)
    ready_swaps = sum(sw["was_ready"] for sw in raw["swaps"])
    ready_swaps_hit = sum(sw["was_ready"] and any(c["to"] == sw["dropped"] and 0 <= c["t"] - sw["t"] <= near_s
                                                   for c in cuts) for sw in raw["swaps"])
    return {"cuts": len(cuts), "ready": sum(c["ready"] for c in cuts), "waits": waits,
            "wanted_ticks": raw["wanted_ticks"], "idle_ticks": raw["idle_ticks"],
            "swaps": len(raw["swaps"]), "swaps_wanted_soon": swaps_hit, "ready_swaps": ready_swaps,
            "ready_swaps_wanted_soon": ready_swaps_hit, "loads": raw["loads"], "failures": raw["failures"]}


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1)


def summarize(parts: list[dict]) -> dict:
    n = sum(x["cuts"] for x in parts)
    waits = [w for x in parts for w in x["waits"]]
    wt = sum(x["wanted_ticks"] for x in parts)
    fails: dict[str, int] = {}
    steps: dict[str, str] = {}
    for x in parts:
        for f in x["failures"]:
            fails[f["kind"]] = fails.get(f["kind"], 0) + 1
            if f.get("step"):
                steps[f["kind"]] = f["step"]
    return {
        "cuts": n,
        "ready_in_time_pct": round(100 * sum(x["ready"] for x in parts) / n, 1) if n else None,
        "wanted_to_ready_p50_s": _pct(waits, .5), "wanted_to_ready_p90_s": _pct(waits, .9),
        "wanted_ticks": wt,
        "idle_while_wanted_pct": round(100 * sum(x["idle_ticks"] for x in parts) / wt, 1) if wt else None,
        "swaps": sum(x["swaps"] for x in parts),
        "swaps_just_before_needed": sum(x["swaps_wanted_soon"] for x in parts),
        "ready_dropped_just_before_needed": sum(x["ready_swaps_wanted_soon"] for x in parts),
        "loads": sum(x["loads"] for x in parts), "failures": fails, "failure_steps": steps}


def flaws(trace: Trace, raw: dict, base: dict | None = None, p: gq.Params | None = None) -> list[dict]:
    """Cuts where the queue had no ready stick although the game was a candidate for its whole prep time (so a load
    could have finished). With `base` (the planner arm's raw records) only cuts it WAS ready for count: the queue
    did worse than today. Each case names what the stick held and why the queue chose it at the moment it should
    have started loading."""
    p, out = p or gq.Params(), []
    base_ready = {(c["t"], c["to"]) for c in (base or {"cuts": []})["cuts"] if c["ready"]}
    for c in raw["cuts"]:
        if c["ready"] or (base is not None and (c["t"], c["to"]) not in base_ready):
            continue
        need = FakeStick(p).prep_s(c["app"]) + p.reach_margin_s
        win = [tk for tk in trace.ticks if c["t"] - need <= tk["t"] < c["t"]]
        if not win or win[0]["t"] > c["t"] - need + 15 or any(c["to"] not in tk["cands"] or tk["current"] == c["to"]
                                                              for tk in win):
            continue
        h = raw["hist"].get(win[0]["t"])
        out.append({"game": c["to"], "cut_t": c["t"], "app": c["app"], "prep_s": round(need, 1),
                    "stick_held": c["held"], "held_was": h[5] if h else None,
                    "at_window_start": {"held": h[0], "held_ready": h[1], "queue_want": h[2], "why": h[3],
                                        "x_row": h[4].get(c["to"])} if h else None})
    return out


def run_nights(archive: Path, leagues, split: str, max_nights: int | None, step: float | None, seed: int,
               near_s: float, log=print) -> dict:
    """The whole report: per league, queue off vs on, with and without modeled failures."""
    import yaml  # noqa: F401  (Config reads yaml)
    from redzone.channels import ChannelMap
    from redzone.config import Config
    from redzone.lab import evaldata, splits
    from redzone.lab.backtest import Replay
    from redzone.models import League
    from redzone.sources import espn
    pinned = archive.parent / "eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else archive.parent / "config.yaml")
    evaldata.use_shipped_switch_costs(cfg)
    chans = archive.parent / "_eval_channels.json"
    report: dict = {"modeled": "every stick time is MODELED (gamequeue.Params), not measured; failure rates are invented",
                    "split": split, "leagues": {}}
    for lg_name in leagues:
        lg, arch = League(lg_name), archive / lg_name
        if not arch.is_dir() or not list(arch.glob("scoreboard_*.json")):
            report["leagues"][lg_name] = {"nights": 0, "note": "no archive for this league"}
            continue
        evaldata.flatten_league_overrides(cfg, lg_name)
        mine = set(cfg.get(f"teams.{lg_name}") or ["WSH"])
        end = evaldata.window_end(lg_name, None)
        keep = splits.nights_for(arch, split, end)
        sbs = sorted(sb for sb in arch.glob("scoreboard_*.json") if (not end or sb.stem.split("_")[-1] <= end))
        sbs = [sb for sb in sbs if keep is None or sb.stem.split("_")[-1] in keep]
        parts: dict[str, list] = {k: [] for k in ("off", "on", "on_fix", "on_fix_ended", "off_fail", "on_fail")}
        examples, used, why_hist, held_hist, n_flaw = [], 0, {}, {}, 0
        for sb in sbs[:max_nights] if max_nights else sbs:
            evs = json.loads(sb.read_text()).get("events", [])
            reps, targets = [], {}
            for e in evs:
                f = arch / f"summary_{e['id']}.json"
                r = Replay.load(lg, f) if f.exists() else None
                if r:
                    reps.append(r)
                    g = espn._parse_event(lg, e)
                    w = ChannelMap(chans).where(g) if g and chans.exists() else None
                    targets[r.gid] = f"{(w.target or 'youtubetv:x').split(':')[0] if w else 'youtubetv'}:{r.gid}"
            if len(reps) < 2:
                continue
            night = sb.stem.split("_")[-1]
            tr = record_night(reps, targets, cfg, mine, step, seed)
            tr.night = night
            if not any(s["kind"] not in NO_BUFFER for s in tr.switches):
                continue
            used += 1
            for name, arm, fo in (("off", "off", False), ("on", "on", False), ("on_fix", "on", False), ("on_fix_ended", "on", False),
                                  ("off_fail", "off", True), ("on_fail", "on", True)):
                raw = simulate(arm, tr, cfg, fail_on=fo, seed=seed, free_gone={"on_fix": "any", "on_fix_ended": "ended"}.get(name, ""))
                parts[name].append(score_arm(tr, raw, near_s))
                if name == "off":
                    off_raw = raw
                if name == "on":
                    cases = flaws(tr, raw, off_raw)
                    for c in cases:
                        w = (c["at_window_start"] or {}).get("why", "no_tick")
                        why_hist[w] = why_hist.get(w, 0) + 1
                        held_hist[c["held_was"]] = held_hist.get(c["held_was"], 0) + 1
                    n_flaw += len(cases)
                    examples += [dict(c, night=night) for c in cases[:1]] if len(examples) < 3 else []
            log(f"{lg_name} {night}: {len(tr.switches)} cuts")
        report["leagues"][lg_name] = {"nights": used, **{k: summarize(v) for k, v in parts.items() if v},
                                      "queue_worse_than_planner": {
                                          "cuts": n_flaw, "why_at_window_start": why_hist,
                                          "what_the_stick_held": held_hist, "examples": examples[:3]}}
    return report


def table(report: dict) -> str:
    rows = ["| league | arm | cuts | ready in time % | wanted->ready p50/p90 s | idle while wanted % | swapped just before needed |",
            "|---|---|---|---|---|---|---|"]
    for lg, d in report["leagues"].items():
        for arm in ("off", "on", "on_fix", "on_fix_ended", "off_fail", "on_fail"):
            x = d.get(arm)
            if x:
                rows.append(f"| {lg} | {arm} | {x['cuts']} | {x['ready_in_time_pct']} | {x['wanted_to_ready_p50_s']}/"
                            f"{x['wanted_to_ready_p90_s']} | {x['idle_while_wanted_pct']} | "
                            f"{x['swaps_just_before_needed']}/{x['swaps']} |")
    fl = ["", "failures drawn from invented rates (same draws both arms): kind count -> the queue's step (Failure table)"]
    for lg, d in report["leagues"].items():
        on, off = d.get("on_fail"), d.get("off_fail")
        if on and off:
            fl.append(f"{lg}: queue {on['failures']} -> {on['failure_steps']}; planner saw {off['failures']}, "
                      f"loads {off['loads']} vs queue {on['loads']}")
    return "MODELED stick times (not measured; docs/test-queue.md 3a)\n" + "\n".join(rows + fl)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", type=Path)
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--nights", action="store_true", help="whole archived nights, queue off vs on, modeled stick")
    ap.add_argument("--archive", type=Path, default=Path("data/archive"))
    ap.add_argument("--split", default="fit", choices=("train", "valid", "fit"), help="never the locked test split")
    ap.add_argument("--leagues", default=",".join(LEAGUES))
    ap.add_argument("--max-nights", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--near-s", type=float, default=90.0, help="a swap this close before the game's cut counts")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    if a.nights:
        rep = run_nights(a.archive, [x for x in a.leagues.split(",") if x in LEAGUES], a.split, a.max_nights, None,
                         a.seed, a.near_s, log=lambda m: print(m, file=sys.stderr))
        if a.json:
            a.json.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        print(table(rep))
        return 0
    if a.bench:
        print(json.dumps(bench()))
        return 0
    scenario = json.loads(a.scenario.read_text(encoding="utf-8")) if a.scenario else BUILTIN
    for line in replay(scenario):
        print(json.dumps(line, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

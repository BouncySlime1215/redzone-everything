"""Decisive-Q dataset (task A-02): Q for every legal option at sampled decision points of training-night slates.

    pool = night_pool(root)                                   # training nights only (fence + cutoff + fit split)
    plan = SlatePlan(pool, master_seed=7, slates=60000, per_shard=200)
    rows, summary = build_shard(plan, 0, QLoader(root))       # deterministic in (master seed, shard index, archive)
    write_shard(out_dir, plan, 0, rows, summary)              # shards/qdata_s00000_<night>.jsonl.gz + summaries/

What one row holds (one DECISION POINT): the slate (index, night, games, seed, padding, feed-lag scenario), the second t on
the night's grid, the feed lag and the feed cutoff te the features were read at, the feature vectors (redzone.featurestore
value_v3 per game, read causally at te; the show's own stick state), every option the planner had for a first jump within
QD_WINDOW seconds plus STAY, the strict Rule #1 mask verdict of each (with the read states it was judged under), Q of every
legal option, the best and second-best gap, and the DECISIVE and TIE flags.

The world. Track A section 4 names the engine: the PARK core's `run`, the show's own step function, with the plain planner
(the PARK planner's `Planner`, the search the show runs every tick). Each slate is run with that planner on the recorded truth of its
games (the core's Night of park_ceiling.truth_of, the arrays the replay's judge also reads). The padding is one of A1's
settings (QD_PADDINGS, the core's guard), and the feed lag is drawn per game from Gym v2's scenario sampler
(scenario.sample: league feed median 10.7 s or ESPN about 36 s, L111); the slate's planner knows the feed `know` seconds
ahead of its edge, as park_replay builds it for a late feed (lead = show_delay + 13 + 8 - lag, the worst game's lag).
The full Gym v2 episode (park_replay's Show on fake sticks) is NOT what is branched: it costs about 60 s of CPU per night
and has no action channel (GymV2.step(action) raises), while the core it plans with runs 1,200 s of a slate in about 0.05 s.

Decision points. The planner searches about every 2 s; a search whose own option list holds a first jump planned within
QD_WINDOW seconds (Plan.commit_s, the window inside which a scheduled jump is kept) is a moment a director could choose.
The episode is cut into `points` equal slots and the first such search in each slot is a decision point.

Options and Q. STAY (no jump for QD_WINDOW seconds) and each distinct first jump (game, route, planned second, landing) of
that search's rows, before the planner's own choice or its fall-back to banked landings. Each option is rolled out from the
state just before that tick for QD_HORIZON seconds (Plan.horizon): the chosen option is FORCED (the planner keeps that row
until its jump is made or its second passes; STAY vetoes every jump in the window), then the PLAIN PLANNER continues (the
default continuation policy). Q is the graded value seen in the rollout: the core's own `v2_seen` (value2's grade of each
play on screen with its 7 s lead-in, the `value` term of Gym v2's reward, which the replay judge counts the same way).
Must-see points, live seconds and the core's own Rule #1 counts ride along per option. Q is labelled with the planner's
continuation, not an oracle's: A4 (fitted-Q) iterates it with the learned policy's own.

The mask. gym2.mask.Rule1Mask at level "strict" on the recorded truth (grading.dead_at, clean_leave, leverage), with the
landing margin by READ state: the screen stream and the hidden stream are each READ with probability QD_P_READ (L112: places
read 14 of 20), drawn per decision point; a TUNE route lands on a fresh tune with its own draw. A masked option gets no Q;
it is listed with the mask's reason (the "mask-blocked intents" diagnostic). The core runs without position error
(engine_setting: it plans on what is believed), so believed and true seconds coincide there; the mask's margin is what
stands for the landing error.

Decisive and tie. Over the legal options' Q (rounded to QD_ROUND places): gap = best - second; DECISIVE when gap >= 0.10
(QD_DECISIVE, the EXP1b definition, on value2 points); TIE when gap <= QD_TIE_EPS. A point with one legal option has
neither (single = True) and is left out of the tie share.

Execution (task A-02b, `execution`, default "a02" = the A-02 rows byte for byte). A forced jump is not always made in its
rollout, and an option whose jump was not made carries the plain planner's Q, not its own: ties then look far more
common than they are. "account" runs the same rollouts and records per option `made`, `made_s` (seconds after the
decision), `exec`, `not_made_reason` and `not_made_class` (not_made_reason, from a read-only log of what stood in the
way each second), and per point best, gap, decisive and tie over EXECUTED options only (`exec_*`, `n_exec`; fewer than
two = `no_choice`, left out of the tie share). "fixed" (version qdata-v2) does the same with the two causes found on the
toy removed: the plain planner gets the show's own search knobs for an instant rig (no landing further than the step's
land_wait_max before its play, which the step always refuses; no 15 s skip route, which the show does not have), and
the forced row is kept when the search swaps its rows for banked landings (staying beat every reported jump).

Not modelled here (named, not hidden): stick failures and step-time jitter (Gym v2's FakeRig), feed rows arriving late
(the heavy tail), and the show's guard (the PARK facts module's refusal): the training mask is the strict grader mask, as in Gym v2.

Research only: nothing under redzone/ outside lab/gym2 imports it, and no config key reads it (ship OFF).
Why: WORKPLAN v2 A-02 and track A section 4 "Labels": Q for EVERY legal option, never an argmax, with the decisive flag and
the tie share per scenario as a standing diagnostic (EXP1b, L120: 88.9% of the oracle's states were ties).
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import os
import random
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

QD_FORMAT = "redzone-qdata/1"
QD_VERSION = "qdata-v1"
QD_PADDINGS = (49, 10, 8, 3)        # A1's padding settings, seconds (track A section 8, A1)
QD_HORIZON = 90                     # seconds of rollout per option (the planner's Plan.horizon)
QD_WINDOW = 10                      # a first jump planned within this many seconds is an option now (Plan.commit_s)
QD_DECISIVE = 0.10                  # best beats second by at least this (EXP1b, value2 points)
QD_TIE_EPS = 1e-6                   # top two within this are a tie
QD_ROUND = 6                        # decimals kept for every Q and feature in a row
QD_EPISODE_MIN = 10.0               # minutes of a slate that decision points are drawn from
QD_POINTS = 12                      # decision points per slate (at most one per equal slot of the episode)
QD_MAX_GAMES = 6                    # track A section 4: at most 6 games
QD_MAX_NFL = 1                      # RedZone's football scope is Monday and Thursday nights: one NFL game per slate
QD_TIP_SPREAD = 9000.0              # the games of a slate are scheduled to start within this many seconds of the first
QD_CUTOFF = "20251001"              # training slates use games before this night (track A section 4 "Splits")
QD_P_READ = 14 / 20                 # a stream's place was read (L112: 14 of 20)
QD_LEAGUES = ("nba", "cbb", "nfl", "cfb")
QD_SLATES = 60000
QD_PER_SHARD = 200
QD_MIN_ROWS = 60                    # a recorded game needs this many plays (park_replay.load_replay's rule)
QD_SOURCES = ("espn",)              # where the archive's games come from (datamanifest's archive kind)
QD_EXECUTION = ("a02", "account", "fixed")   # how forced jumps are run and accounted (task A-02b; build_slate)
QD_VERSION_OF = {"a02": QD_VERSION, "account": "qdata-v1-acct", "fixed": "qdata-v2"}


# ---------------------------------------------------------------------------------------------- small helpers
def qd_canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def qd_round(x: float) -> float:
    return round(float(x), QD_ROUND)


def qd_gzip(lines: list[str]) -> bytes:
    """The rows as gzip bytes with no clock and no file name in the header: the same rows give the same bytes."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=6) as z:
        for ln in lines:
            z.write(ln.encode("utf-8") + b"\n")
    return buf.getvalue()


def qd_read_rows(path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def qd_atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------------------------- decisive and tie
def decisive_of(qs: list[float], threshold: float = QD_DECISIVE, eps: float = QD_TIE_EPS) -> dict:
    """{best, second, gap, decisive, tie, single} of one decision point's legal Q values (best = index of the first max)."""
    if not qs:
        return {"best": None, "second": None, "gap": None, "decisive": False, "tie": False, "single": True}
    best = 0
    for k, q in enumerate(qs):
        if q > qs[best]:
            best = k
    if len(qs) < 2:
        return {"best": best, "second": None, "gap": None, "decisive": False, "tie": False, "single": True}
    second = max(q for k, q in enumerate(qs) if k != best)
    gap = qd_round(qs[best] - second)
    return {"best": best, "second": qd_round(second), "gap": gap, "decisive": gap >= threshold - 1e-12,
            "tie": gap <= eps, "single": False}


def mix_label(leagues: list[str]) -> str:
    """'nba:2+nfl:1' for a slate's leagues (the census's mix spelling)."""
    n: dict = {}
    for lg in leagues:
        n[lg] = n.get(lg, 0) + 1
    return "+".join(f"{k}:{n[k]}" for k in sorted(n))


def tie_table(rows: list[dict], eps: float | None = None, threshold: float = QD_DECISIVE) -> dict:
    """The tie-share table (A1's prerequisite): over decision points with two legal options or more, the share whose top
    two tie (within eps) and the share that is decisive, by padding, by scenario (feed source) and by sport mix. eps None =
    each row's own tie flag; a number recomputes from the stored Q values."""
    def flags(r):
        if eps is None:
            return r["tie"], r["decisive"], r["single"]
        d = decisive_of([o["q"] for o in r["options"] if o.get("q") is not None], threshold, eps)
        return d["tie"], d["decisive"], d["single"]

    out: dict = {"eps": QD_TIE_EPS if eps is None else eps, "decisive_at": threshold, "points": 0, "single": 0,
                 "by": {"all": {}, "pad": {}, "scenario": {}, "mix": {}}}
    for r in rows:
        tie, dec, single = flags(r)
        out["points"] += 1
        if single:
            out["single"] += 1
            continue
        for key, val in (("all", "all"), ("pad", str(r["pad"])), ("scenario", r["scenario"]), ("mix", r["mix"])):
            c = out["by"][key].setdefault(val, {"n": 0, "tie": 0, "decisive": 0, "options": 0})
            c["n"] += 1
            c["tie"] += int(tie)
            c["decisive"] += int(dec)
            c["options"] += sum(1 for o in r["options"] if o.get("q") is not None)
    for grp in out["by"].values():
        for c in grp.values():
            c["tie_share"] = round(c["tie"] / c["n"], 4)
            c["decisive_share"] = round(c["decisive"] / c["n"], 4)
            c["options_mean"] = round(c["options"] / c["n"], 3)
    return out


def tie_table_text(tab: dict) -> str:
    lines = [f"tie share (top two within {tab['eps']:g}) and decisive share (gap >= {tab['decisive_at']:g}); "
             f"{tab['points']} decision points, {tab['single']} with one legal option left out"]
    for key in ("all", "pad", "scenario", "mix"):
        lines.append(f"by {key}:")
        for val in sorted(tab["by"][key], key=lambda v: (len(v), v)):
            c = tab["by"][key][val]
            lines.append(f"  {val:<18} n={c['n']:<6} tie={c['tie_share']:.3f} decisive={c['decisive_share']:.3f} "
                         f"options={c['options_mean']:.2f}")
    return "\n".join(lines)


def option_executed(o: dict) -> bool:
    """Was this option carried out in its rollout: STAY always; a forced jump when its jump was made (an A-02 row's
    `done`, a later row's `exec`). An option that was not carried out has the plain planner's Q, not its own."""
    if "exec" in o:
        return bool(o["exec"])
    return o.get("k") == "stay" or o.get("done") is True


def exec_flags(legal: list, threshold: float = QD_DECISIVE, eps: float = QD_TIE_EPS) -> dict:
    """Best, gap, decisive and tie over the EXECUTED legal options only (the others keep their rows, flagged, and never
    enter): exec_best is an index into the legal options; no_choice when fewer than two were executed."""
    ix = [k for k, o in enumerate(legal) if option_executed(o) and o.get("q") is not None]
    d = decisive_of([legal[k]["q"] for k in ix], threshold, eps)
    return {"n_exec": len(ix), "no_choice": len(ix) < 2, "exec_best": None if d["best"] is None else ix[d["best"]],
            "exec_gap": d["gap"], "exec_decisive": d["decisive"], "exec_tie": d["tie"]}


def exec_tie_table(rows: list[dict], eps: float | None = None, threshold: float = QD_DECISIVE) -> dict:
    """Tie and decisive shares over EXECUTED options only (denominator: decision points with two executed options or
    more; 'no_choice' points are counted apart), with the A-02 definition (every legal option) side by side on the same
    rows, by padding, scenario and mix, and the share of forced jumps not made. Works on A-02 rows too (executed = STAY
    or `done`). eps None = QD_TIE_EPS."""
    e = QD_TIE_EPS if eps is None else eps
    out: dict = {"eps": e, "decisive_at": threshold, "by": {"all": {}, "pad": {}, "scenario": {}, "mix": {}}}
    for r in rows:
        legal = [o for o in r["options"] if o.get("q") is not None]
        new = exec_flags(legal, threshold, e)
        old = decisive_of([o["q"] for o in legal], threshold, e)
        jumps = [o for o in legal if o.get("k") != "stay"]
        for key, val in (("all", "all"), ("pad", str(r["pad"])), ("scenario", r["scenario"]), ("mix", r["mix"])):
            c = out["by"][key].setdefault(val, {"points": 0, "no_choice": 0, "n": 0, "tie": 0, "decisive": 0,
                                               "old_n": 0, "old_tie": 0, "old_decisive": 0, "jumps": 0, "jumps_made": 0})
            c["points"] += 1
            c["jumps"] += len(jumps)
            c["jumps_made"] += sum(1 for o in jumps if option_executed(o))
            if not old["single"]:
                c["old_n"] += 1
                c["old_tie"] += int(old["tie"])
                c["old_decisive"] += int(old["decisive"])
            if new["no_choice"]:
                c["no_choice"] += 1
                continue
            c["n"] += 1
            c["tie"] += int(new["exec_tie"])
            c["decisive"] += int(new["exec_decisive"])
    for grp in out["by"].values():
        for c in grp.values():
            c["no_choice_share"] = round(c["no_choice"] / c["points"], 4)
            c["tie_share"] = round(c["tie"] / c["n"], 4) if c["n"] else None
            c["decisive_share"] = round(c["decisive"] / c["n"], 4) if c["n"] else None
            c["old_tie_share"] = round(c["old_tie"] / c["old_n"], 4) if c["old_n"] else None
            c["old_decisive_share"] = round(c["old_decisive"] / c["old_n"], 4) if c["old_n"] else None
            c["not_made_share"] = round(1 - c["jumps_made"] / c["jumps"], 4) if c["jumps"] else None
    return out


def exec_tie_table_text(tab: dict) -> str:
    def f(x):
        return "  -  " if x is None else f"{x:.3f}"
    lines = [f"EXECUTED options only (tie within {tab['eps']:g}, decisive gap >= {tab['decisive_at']:g}); no_choice = "
             "fewer than two executed options, left out of the denominator; 'old' = every legal option (A-02)"]
    for key in ("all", "pad", "scenario", "mix"):
        lines.append(f"by {key}:")
        for val in sorted(tab["by"][key], key=lambda v: (len(v), v)):
            c = tab["by"][key][val]
            lines.append(f"  {val:<18} points={c['points']:<6} no_choice={f(c['no_choice_share'])} n={c['n']:<6} "
                         f"tie={f(c['tie_share'])} decisive={f(c['decisive_share'])} | old n={c['old_n']:<6} "
                         f"tie={f(c['old_tie_share'])} decisive={f(c['old_decisive_share'])} | jumps not made "
                         f"{f(c['not_made_share'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- the night pool (fence)
def night_refusal(night: str, cutoff: str = QD_CUTOFF) -> str | None:
    """Why this night may not be a training night (locked or tuning fence, or at/after the cutoff); None when it may."""
    from redzone.lab import fence
    k = fence.night_key(night)
    w = fence.fenced(k)
    if w is not None:
        return f"{k} is in the {w.name} window {w.start}..{w.end}"
    if k >= fence.night_key(cutoff):
        return f"{k} is at or after the training cutoff {fence.night_key(cutoff)}"
    return None


def assert_training_night(night: str, cutoff: str = QD_CUTOFF) -> None:
    from redzone.lab import fence
    why = night_refusal(night, cutoff)
    if why:
        raise fence.FenceError(f"REFUSED slate night: {why}")


def _start_of(ev: dict) -> float | None:
    s = str(ev.get("date") or "")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class NightPool:
    """Training nights and their games: {night: [(scheduled start, league, game id), ...]} (start order), what was refused."""
    nights: dict
    refused: dict
    cutoff: str
    leagues: tuple
    max_nfl: int

    @property
    def digest(self) -> str:
        """sha256 of the pool (nights and games): a shard says which pool it was drawn from."""
        return hashlib.sha256(qd_canon({"nights": self.nights, "cutoff": self.cutoff, "max_nfl": self.max_nfl}).encode()).hexdigest()

    def keys(self) -> list[str]:
        return sorted(self.nights)


def _viable(gl: list, max_nfl: int) -> list[int]:
    """Indexes of anchors around which a slate of two games or more can be drawn."""
    out = []
    for a, (sa, _, _) in enumerate(gl):
        near = [x for x in gl if abs(x[0] - sa) <= QD_TIP_SPREAD]
        nfl = sum(1 for x in near if x[1] == "nfl")
        if len(near) - nfl + min(nfl, max_nfl) >= 2:
            out.append(a)
    return out


def night_pool(root, *, leagues=QD_LEAGUES, cutoff: str = QD_CUTOFF, max_nfl: int = QD_MAX_NFL,
               nights: list | None = None) -> NightPool:
    """The training nights under root (a data/archive folder): each league's dev (fit) nights (splits.nights_for), minus
    every fenced night (fence.FENCES: locked 2025-12-12..2026-01-05, tuning Oct 2026) and every night at or after the
    cutoff; then only nights where a slate of two games or more can be drawn. `nights` (explicit list): each one must
    pass the fence and cutoff, or FenceError (never silently skipped)."""
    from redzone.lab import splits
    root = Path(root)
    if nights is not None:
        for n in nights:
            assert_training_night(n, cutoff)
    want = None if nights is None else {str(n) for n in nights}
    refused = {"fenced": 0, "after_cutoff": 0, "not_fit": 0, "too_few_games": 0}
    by: dict = {}
    for lg in leagues:
        d = root / lg
        if not d.is_dir():
            continue
        an = splits.archive_nights(d)
        fit = splits.nights_for(d, "fit") or set()
        for night in sorted(an):
            if want is not None and night not in want:
                continue
            why = night_refusal(night, cutoff)
            if why:
                refused["fenced" if "window" in why else "after_cutoff"] += 1
                continue
            if night not in fit:
                refused["not_fit"] += 1
                continue
            try:
                evs = json.loads((d / f"scoreboard_{night}.json").read_text()).get("events", [])
            except (OSError, ValueError):
                evs = []
            starts = {str(e.get("id")): _start_of(e) for e in evs}
            for gid in an[night]:
                s = starts.get(gid)
                if s is not None and (d / f"summary_{gid}.json").exists():
                    by.setdefault(night, []).append((s, lg, gid))
    out = {}
    for night in sorted(by):
        gl = sorted(by[night])
        if _viable(gl, max_nfl):
            out[night] = [list(x) for x in gl]
        else:
            refused["too_few_games"] += 1
    return NightPool(out, refused, cutoff, tuple(leagues), max_nfl)


# ---------------------------------------------------------------------------------------------- the slate sampler
@dataclass(frozen=True)
class SlateDraw:
    """One slate: drawn from (master seed, index) and the pool alone. u places the episode inside the slate's span."""
    index: int
    night: str
    leagues: tuple
    games: tuple
    u: float
    pad: int
    seed: int

    def to_dict(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}


def draw_slate(pool: NightPool, master_seed: int, index: int) -> SlateDraw:
    """Night uniform over the pool; an anchor game, then 2..6 games scheduled within QD_TIP_SPREAD of it (at most
    max_nfl NFL games); the episode's place u; the padding; the scenario seed."""
    keys = pool.keys()
    if not keys:
        raise ValueError("the night pool is empty: no training night can make a slate")
    rng = random.Random(f"qdata-slate:{int(master_seed)}:{int(index)}")
    night = keys[rng.randrange(len(keys))]
    gl = [tuple(x) for x in pool.nights[night]]
    ok = _viable(gl, pool.max_nfl)
    anchor = ok[rng.randrange(len(ok))]
    near = [x for x in gl if abs(x[0] - gl[anchor][0]) <= QD_TIP_SPREAD]
    nfl = sum(1 for x in near if x[1] == "nfl")
    top = len(near) - nfl + min(nfl, pool.max_nfl)
    if top > QD_MAX_GAMES:
        top = QD_MAX_GAMES
    k = rng.randint(2, top)
    order = list(range(len(near)))
    rng.shuffle(order)
    pick, n_nfl = [], 0
    for i in order:
        if len(pick) == k:
            break
        if near[i][1] == "nfl":
            if n_nfl >= pool.max_nfl:
                continue
            n_nfl += 1
        pick.append(near[i])
    pick.sort()
    return SlateDraw(index=int(index), night=night, leagues=tuple(x[1] for x in pick), games=tuple(x[2] for x in pick),
                     u=rng.random(), pad=int(QD_PADDINGS[rng.randrange(len(QD_PADDINGS))]), seed=rng.randrange(2 ** 31))


@dataclass(frozen=True)
class SlatePlan:
    """The whole job: `slates` draws from the pool, `per_shard` to a shard, plus the episode knobs."""
    pool: NightPool
    master_seed: int
    slates: int = QD_SLATES
    per_shard: int = QD_PER_SHARD
    episode_min: float = QD_EPISODE_MIN
    points: int = QD_POINTS
    horizon: int = QD_HORIZON
    mask_level: str = "strict"
    execution: str = "a02"

    @property
    def shards(self) -> int:
        return -(-self.slates // self.per_shard)

    def shard_slates(self, shard: int) -> range:
        if not 0 <= shard < self.shards:
            raise ValueError(f"shard {shard} is outside 0..{self.shards - 1}")
        return range(shard * self.per_shard, min(self.slates, (shard + 1) * self.per_shard))

    def knobs(self) -> dict:
        return {"master_seed": self.master_seed, "slates": self.slates, "per_shard": self.per_shard,
                "episode_min": self.episode_min, "points": self.points, "horizon": self.horizon, "window": QD_WINDOW,
                "mask_level": self.mask_level, "cutoff": self.pool.cutoff, "max_nfl": self.pool.max_nfl,
                "pool": self.pool.digest, "version": QD_VERSION_OF[self.execution],
                **({} if self.execution == "a02" else {"execution": self.execution})}

    def digest(self) -> str:
        return hashlib.sha256(qd_canon(self.knobs()).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- loading games
class QLoader:
    """Recorded games from an archive folder, loaded once per process: (Replay with late edits put back, its truth,
    its featurestore parse, its facts arrays)."""

    def __init__(self, root, cap: int = 256):
        self.root, self.cap, self._g = Path(root), int(cap), {}

    def game(self, league: str, gid: str):
        key = (league, gid)
        if key not in self._g:
            if len(self._g) >= self.cap:
                self._g.pop(next(iter(self._g)))
            self._g[key] = self._load(league, gid)
        return self._g[key]

    def _load(self, league: str, gid: str):
        from redzone import featurestore as fs
        from redzone.lab.backtest import Replay
        from redzone.lab.gym2.env import replay_module
        from redzone.models import League
        pr = replay_module()
        import park_ceiling as pc
        f = self.root / league / f"summary_{gid}.json"
        r = Replay.load(League(league), f)
        if r is None or len(r.plays) < QD_MIN_ROWS:
            return None
        r = pr.repair(r)
        truth = pc.truth_of(r)
        fsp = fs.fs_parse(league, r.data) if league in fs.fs_spec("value_v3").leagues else None
        return r, truth, fsp, fs.fs_facts(truth)


# ---------------------------------------------------------------------------------------------- the planner, recorded and forced
_QD_CLASSES: list = []


def _planner_classes():
    """(QRecorder, QForced), made once per process (they subclass the app's Planner, imported late)."""
    if _QD_CLASSES:
        return _QD_CLASSES[0], _QD_CLASSES[1]
    from redzone.noggin.park_plan import Planner

    class QRecorder(Planner):
        """The plain planner, also keeping each search's own jump rows (before its choice and before the fall-back to
        banked landings): `qd_seen` = [(t, rows)]. It decides exactly as Planner does (rows are only read)."""
        qd_seen = None
        qd_raw = ()

        def _options(self, f):
            raw = f.jumps(f.scr, f.cs, f.tw, f.m0s, f.live0, f.paid, f.lv, f.ld, self.p.depth, f.T)
            self.qd_raw = raw
            if self.qd_seen is not None:
                self.qd_seen.append((f.t, raw))
            return super()._options(f)

    class QForced(QRecorder):
        """A rollout's planner: the option is forced (a jump row kept until its jump is made or its second passes;
        STAY: every jump vetoed through the window), then the plain planner (the default continuation policy).
        qd_diag (a list) logs, each second of the forcing window, what stood in the forced jump's way (read only:
        it changes no decision). qd_exec (qdata-v2): the forced row is kept even when the search swaps its rows for
        banked landings (staying beat every reported jump), the one rollout artefact found (task A-02b)."""
        qd_force = None           # None | "stay" | (game, route, tau, landing)
        qd_until = -1
        qd_hops = 0
        qd_kept = 0
        qd_diag = None
        qd_exec = False

        def _forcing(self, t) -> bool:
            return isinstance(self.qd_force, tuple) and t <= self.qd_until and len(self.st.hops) == self.qd_hops

        def _is_forced(self, row) -> bool:
            fk, o = self.qd_force, row[7]
            return (not row[1] and o[0] == fk[0] and o[1] == fk[1] and abs(row[2] - fk[2]) <= self.params.plan_ok_s
                    and row[4] == fk[3])

        def _options(self, f):
            out = super()._options(f)
            if not self._forcing(f.t):
                return out
            v0, opts, best, landable = out
            raw = self.qd_raw
            in_raw = any(self._is_forced(r) for r in raw)
            in_opts = in_raw and any(self._is_forced(r) for r in opts)
            swap = in_raw and not in_opts
            if self.qd_diag is not None:          # (a swap undone by qd_exec leaves the row listed)
                self.qd_diag.append((f.t, "listed" if in_opts or (swap and self.qd_exec) else "bank_swap" if swap
                                     else _gone_kind(self, raw)))
            if self.qd_exec and swap:
                return v0, raw, self._best_option(raw), landable
            return out

        def _keep(self, f, v0, opts, best):
            if self._forcing(f.t):
                for row in opts:
                    if self._is_forced(row):
                        self.qd_kept += 1
                        return row
            return super()._keep(f, v0, opts, best)

        def _act(self, t, S):
            out = super()._act(t, S)
            if self.qd_force == "stay" and t <= self.qd_until and out[0]:
                out = (False,) + tuple(out[1:])
            elif self.qd_diag is not None and self._forcing(t):
                self.qd_diag.append((t, _act_event(self, t, S, out)))
            return out
    _QD_CLASSES.extend((QRecorder, QForced))
    return QRecorder, QForced


def _gone_kind(pl, raw) -> str:
    """A search whose rows no longer hold the forced jump: the same jump planned for another second ('tau_drift'),
    the same game and route with another landing ('landing_moved'), or no row for that game and route ('row_gone')."""
    fk = pl.qd_force
    same = [r for r in raw if not r[1] and r[7][0] == fk[0] and r[7][1] == fk[1]]
    if any(r[4] == fk[3] for r in same):
        return "tau_drift"
    return "landing_moved" if same else "row_gone"


def gate_refusal(t: int, S, A: list, s, *, guard: int, reach: int, flip: int, lead: int, know: int, n: int,
                 spot: int, long_stop: int = 20) -> str | None:
    """Why the core's step would NOT flip to the hidden stick this second although the planner asks for the jump
    (None = it would). A read-only copy of the step's own jump test, in its order, after the one pause key the step
    gives a stick that has reached its spot: the stick not ready, a flip in progress, the minimum stay, the stick past
    reach, the feed not yet reporting the padding, no play shown yet on screen, the screen in high leverage or mid play,
    a landing not in dead time or too long before its play."""
    live = s.mode == "live"
    if S.hg < 0 or t < S.h_ready:
        return "stick_not_ready"
    if t < S.black:
        return "flip_in_progress"
    if t - S.s_land < s.min_stay:
        return "min_stay"
    if not (live or S.hb - S.he <= reach + flip):
        return "past_reach"
    xh = A[S.hg]
    paused = S.h_paused
    hb_i = min(n - 1, max(0, int(t - S.hb + S.he)))
    if spot < 0 or hb_i < spot:
        paused = False
    elif not paused and xh.since[hb_i] > guard and xh.left[hb_i] >= guard and hb_i + guard <= t + know:
        paused = True
    xs = A[S.sg]
    i = min(n - 1, max(0, int(t - S.sb + S.se)))
    calm = xs.hlc[min(n, i + guard + 1)] - xs.hlc[max(0, i - guard)] == 0
    leave = (xs.since[i] > guard and xs.left[i] >= guard and calm) or (s.leave_rule == "low_leverage" and calm) \
        or t > xs.a1 + S.sb
    j = min(n - 1, max(0, int(t - S.hb + S.he) + (lead if paused else flip)))
    if i + guard > t + know or j + guard > t + know:
        return "feed_not_reported"
    if S.s_live < s.min_live_s and xs.left[i] < long_stop and t <= xs.a1 + S.sb:
        return "min_live"
    if not calm:
        return "screen_high_leverage"
    if not leave:
        return "screen_mid_play"
    if not (xh.since[j] > guard and xh.left[j] >= guard):
        return "landing_not_dead"
    if xh.left[j] > s.land_wait_max:
        return "landing_long_wait"
    return None


SEARCH_EVENTS = ("listed", "bank_swap", "tau_drift", "landing_moved", "row_gone")
# each not-made reason: is it a CONSTRAINT (the show's own step or timing could not carry the jump out either: the option
# was not really available) or an ARTEFACT of how the rollout forces it (the show could, the rollout did not)
NOT_MADE_CLASS = {"bank_swap": "artefact", "tau_drift": "artefact", "landing_moved": "artefact", "row_gone": "artefact",
                  "no_log": "artefact"}


def not_made_reason(log: list) -> str:
    """Why a forced jump was not made, from its rollout's forcing log [(second, event)]: the step refused it when asked
    ('core_<reason>', the last refusal: a constraint); else the first search had already swapped its rows for banked
    landings and no later search listed it ('bank_swap'); else a later search lost the row ('tau_drift',
    'landing_moved', 'row_gone', the last such loss after the last listing); else its second passed while the planner
    waited ('expired_<what it waited for>': a constraint of timing)."""
    if not log:
        return "no_log"
    core = [e for _, e in log if e.startswith("core:")]
    if core:
        return "core_" + core[-1][5:]
    search = [e for _, e in log if e in SEARCH_EVENTS]
    if "bank_swap" in search and "listed" not in search:
        return "bank_swap"
    k = max((n for n, e in enumerate(search) if e == "listed"), default=-1)
    lost = [e for e in search[k + 1:] if e in ("tau_drift", "landing_moved", "row_gone")]
    if lost:
        return lost[-1]
    acts = [e for _, e in log if e not in SEARCH_EVENTS]
    return "expired_" + (acts[-1] if acts else "no_second")


def not_made_class(reason: str | None) -> str | None:
    """'constraint' or 'artefact' for a not-made reason (None for a jump that was made)."""
    if reason is None:
        return None
    return NOT_MADE_CLASS.get(reason, "constraint" if reason.startswith(("core_", "expired_")) else "artefact")


def _act_event(pl, t: int, S, out) -> str:
    """What one second of the forcing window did for the forced jump: 'asked' (the jump is asked for and the step's
    test lets it through), 'core:<reason>' (asked, refused by the step), or why the planner did not ask: a stick
    command in progress ('stick_tune', 'stick_move'), no jump planned ('no_plan'), another jump planned ('plan_other'),
    the hidden stick not yet on the game or ready ('stick_loading', 'stick_not_ready'), a flip in progress, the screen
    not yet at its leave ('wait_leave') or past it ('leave_passed'), the landing not yet in its window ('wait_landing')
    or past it ('landing_passed')."""
    fk = pl.qd_force
    if out[0]:
        why = gate_refusal(t, S, pl.A, pl.s, guard=pl.G, reach=pl.reach, flip=pl.flip, lead=pl.lead, know=pl.know,
                           n=pl.n, spot=out[2], long_stop=pl.params.long_stop_s)
        return "asked" if why is None else "core:" + why
    if out[3]:
        return "stick_" + out[3]
    go = pl.go
    if go is None:
        return "no_plan"
    if go[3] != fk[0]:
        return "plan_other"
    if S.hg != go[3]:
        return "stick_loading"
    if t < S.h_ready:
        return "stick_not_ready"
    if t < S.black:
        return "flip_in_progress"
    cs = int(t - S.sb + S.se)
    if cs < go[1]:
        return "wait_leave"
    if cs > go[2]:
        return "leave_passed"
    hb = int(t - S.hb + S.he)
    j = hb + (pl.lead if S.h_paused else pl.flip)
    return "wait_landing" if j < go[4] else "landing_passed" if j > go[5] else "not_paused"


def _fork(S, st):
    """A copy of the sticks and of what the counters must remember (jumps made, valued moments already shown); the
    counted totals start at zero, so the rollout's own value is read straight off its Stats."""
    from redzone.noggin.park_core import State, Stats
    S2 = State()
    for k in State.__slots__:
        setattr(S2, k, getattr(S, k))
    S2.rng = random.Random()
    S2.rng.setstate(S.rng.getstate())
    S2.gd = list(S.gd)
    st2 = Stats()
    st2.hops = list(st.hops)
    st2.shown = set(st.shown)
    return S2, st2


def _row_key(row) -> tuple:
    o = row[7]
    return (int(o[0]), int(o[1]), int(row[2]), int(row[4]))


ROUTE_NAMES = ("hold", "roll", "skip", "tune")


# ---------------------------------------------------------------------------------------------- one slate
@dataclass
class SlateWorld:
    """A slate set up for rollouts: the core's night and setting, the recorded games in slate order, the scenario."""
    draw: SlateDraw
    night: object
    eng: object
    reps: list
    loaded: list
    leagues: list
    scenario: dict
    know: int
    lag: float
    a: int
    b: int


def slate_world(draw: SlateDraw, loader: QLoader) -> tuple[SlateWorld | None, str]:
    """(the world, '') or (None, why the slate is void). Every slate's night passes the fence again here."""
    from redzone import config
    from redzone.lab.gym2 import scenario as scn
    from redzone.lab.gym2.env import replay_module
    from redzone.noggin import park_core
    from redzone.noggin.park_show import ShowParams, engine_setting
    assert_training_night(draw.night)
    pr = replay_module()
    got = [(lg, gid, loader.game(lg, gid)) for lg, gid in zip(draw.leagues, draw.games)]
    got = [(lg, gid, x) for lg, gid, x in got if x is not None]
    if len(got) < 2:
        return None, "fewer than 2 games with enough plays"
    got.sort(key=lambda x: x[2][1]["t0"])
    night = park_core.Night([x[2][1] for x in got])
    a, b = night.span
    if b - a < 120:
        return None, "the games overlap for less than 2 minutes"
    sc = scn.sample(draw.seed, len(got))
    lag = float(max(sc.feed_lag_s))
    P = ShowParams.from_config(config.default)
    know = int(P.show_delay + pr.FEED_LEAD["tonight"] + scn.NOMINAL_FEED_LAG - lag)
    steps = P.steps_for(True)
    eng = engine_setting(int(draw.pad), know, late_cap=P.late_cap - P.show_delay - P.jump_slack,
                         tune_s=3.0 + (P.read if steps else 0.0), min_live=P.min_look, land_wait=P.land_wait,
                         flip=P.jump_real, pause_reach=P.pause_reach)
    srcs = sorted(set(sc.feed_src))
    scenario = {"feed": srcs[0] if len(srcs) == 1 else "mixed", "feed_src": list(sc.feed_src),
                "feed_lag_s": list(sc.feed_lag_s), "sampler": scn.SAMPLER}
    return SlateWorld(draw, night, eng, [x[2][0] for x in got], [x[2] for x in got], [x[0] for x in got], scenario,
                      know, lag, a, b), ""


def _features(w: SlateWorld, t: int, S) -> dict:
    """The decision point's features, read causally: each game's value_v3 row at now = te + lag with the sampled lag
    (rows stamped at or before te only; te = the grid second the planner's feed has reached), and the show's stick state."""
    from redzone import featurestore as fs
    names = fs.fs_spec("value_v3").names
    te = float(w.night.base + t + w.know)
    per = []
    for lg, (_, _, fsp, facts) in zip(w.leagues, w.loaded):
        if fsp is None:
            per.append([fs.FS_MISSING] * len(names))
            continue
        row = fs.fs_rows("value_v3", lg, fsp, [te + w.lag], facts, 0, w.lag)[0]
        per.append([qd_round(v) for v in row])
    state = [S.sg, S.hg, int(S.s_live), int(t - S.s_land), qd_round(S.sb), int(bool(S.h_paused)), int(max(0, S.h_ready - t))]
    return {"set": fs.fs_tag("value_v3"), "te": te, "games": per,
            "state_names": ["screen", "hidden", "screen_live_s", "since_landing_s", "screen_behind_s", "hidden_paused",
                            "hidden_wait_s"], "state": state}


def _rollout(w: SlateWorld, S, st, pl, t: int, force, until: int, horizon: int, brain, execution: str = "a02") -> dict:
    from redzone.noggin import park_core
    QRecorder, QForced = _planner_classes()
    S2, st2 = _fork(S, st)
    p2 = copy.copy(pl)
    p2.__class__ = QForced
    p2.qd_seen, p2.qd_force, p2.qd_until, p2.qd_hops, p2.qd_kept = [], force, until, len(st.hops), 0
    acct = execution != "a02"
    if acct:
        p2.qd_diag, p2.qd_exec = [], execution == "fixed"
    st2, S2 = park_core.run(w.night, brain, w.eng, t0=t, t1=t + horizon, state=S2, stats=st2, planner=p2)
    first = p2.qd_seen[0] if p2.qd_seen else (None, [])
    done = None
    if isinstance(force, tuple):
        done = any(fl[1] == force[0] and t <= fl[0] <= until for fl in st2.flips)
    out = {"q": qd_round(st2.v2_seen), "must": qd_round(sum(st2.seen.values())), "live": int(st2.live_s),
           "rule1": int(st2.mid_entry + st2.mid_leave + st2.hl_leave), "jumps": len(st2.flips), "done": done,
           "_first": first}
    if acct and isinstance(force, tuple):
        at = next((fl[0] for fl in st2.flips if fl[1] == force[0] and t <= fl[0] <= until), None)
        out["made"] = bool(done)
        out["made_s"] = None if at is None else int(at - t)
        out["_log"] = p2.qd_diag
    return out


def build_slate(draw: SlateDraw, loader: QLoader, *, episode_min: float = QD_EPISODE_MIN, points: int = QD_POINTS,
                horizon: int = QD_HORIZON, mask_level: str = "strict", master_seed: int = 0,
                execution: str = "a02") -> tuple[list, dict]:
    """(decision-point rows, the slate's summary). Deterministic in the draw and the archive. `execution` (QD_EXECUTION):
    "a02" the A-02 rows byte for byte; "account" the same rollouts with each forced jump's execution recorded and the
    executable-option flags; "fixed" the same accounting with the bank-swap artefact fixed (qdata-v2)."""
    if execution not in QD_EXECUTION:
        raise ValueError(f"execution must be one of {QD_EXECUTION}, not {execution!r}")
    from redzone.lab.gym2.env import replay_module
    from redzone.lab.gym2.mask import Rule1Mask
    from redzone.noggin import park_core
    from redzone.noggin.park_plan import FAR, Plan
    pr = replay_module()
    summary = {"slate": draw.index, "night": draw.night, "draw": draw.to_dict(), "points": 0, "void": ""}
    w, why = slate_world(draw, loader)
    if w is None:
        summary["void"] = why
        return [], summary
    from redzone import config
    from redzone.noggin.park_show import ShowParams
    P = ShowParams.from_config(config.default)
    QRecorder, _ = _planner_classes()
    if execution == "fixed":
        # the show's own search knobs for an instant rig (park_show's planner): no 15 s skip route, and no landing
        # further than the step's land_wait_max before its play (the step refuses those, so the show never lists them)
        lw = w.eng.land_wait_max
        pl = QRecorder(Plan(jump_bonus=P.bonus), skip=False, max_wait=int(lw) if lw < 900 else FAR)
    else:
        pl = QRecorder(Plan(jump_bonus=P.bonus))
    pl.qd_seen = []
    brain = pr._Hindsight()
    mask = Rule1Mask(w.reps, w.night.base, level=mask_level)
    ep = int(episode_min * 60)
    start = w.a + int(draw.u * max(0, (w.b - w.a) - ep))
    end = min(w.b, start + ep)
    slot = max(1, -(-(end - start) // max(1, points)))
    used: set = set()
    rrng = random.Random(f"qdata-reads:{int(master_seed)}:{draw.index}")
    S, st = None, None
    rows: list = []
    gids = [r.gid for r in w.reps]
    mix = mix_label(w.leagues)
    for t in range(start, end):
        snap = None if S is None else (_fork(S, st), copy.copy(pl))
        n_seen = len(pl.qd_seen)              # (a snapshot's planner shares this list; it is replaced, never edited)
        st, S = park_core.run(w.night, brain, w.eng, t0=t, t1=t + 1, state=S, stats=st, planner=pl)
        if snap is None or len(pl.qd_seen) == n_seen:
            continue
        last, pl.qd_seen = pl.qd_seen[-1], []
        k = (t - start) // slot
        if k in used:
            continue
        cand = [row for row in last[1] if not row[1] and row[2] - t <= QD_WINDOW]
        if not cand:
            continue
        used.add(k)
        (S0, st0), pl0 = snap
        pl0.qd_seen = None
        rows.append(_point(w, S0, st0, pl0, t, cand, mask, rrng, horizon, brain, draw, gids, mix, master_seed, execution))
    summary["points"] = len(rows)
    summary.update(start=start, end=end, span=[w.a, w.b], know=w.know, lag=w.lag, games=gids, leagues=w.leagues)
    return rows, summary


def _point(w, S, st, pl, t, cand, mask, rrng, horizon, brain, draw, gids, mix, master_seed, execution="a02") -> dict:
    """One decision point: the options (STAY and each distinct first jump), the mask, Q of each legal one."""
    from redzone.noggin.park_plan import TUNE
    scr_read, hid_read, tune_read = (rrng.random() < QD_P_READ for _ in range(3))
    seen: dict = {}
    for row in cand:
        key = _row_key(row)
        if key not in seen or row[0] > seen[key][0]:
            seen[key] = row
    opts = [{"k": "stay", "g": int(S.sg), "route": "stay", "tau": 0, "i": None, "j": None, "plan": None, "va": None}]
    for key in sorted(seen):
        row = seen[key]
        opts.append({"k": list(key), "g": key[0], "route": ROUTE_NAMES[key[1]], "tau": int(row[2] - t), "i": int(row[3]),
                     "j": int(row[4]), "plan": qd_round(row[0]), "va": qd_round(row[8])})
    legal, masked = [], []
    for o, key in zip(opts, [None] + sorted(seen)):
        if key is None:
            legal.append(o)
            continue
        join_read = tune_read if key[1] == TUNE else hid_read
        v = mask.allows(int(S.sg), o["i"], key[0], o["j"], leave_read=scr_read, join_read=join_read)
        if v.ok:
            legal.append(o)
        else:
            masked.append({"k": o["k"], "g": o["g"], "route": o["route"], "why": v.why})
    for o in legal:
        if o["k"] == "stay":
            r = _rollout(w, S, st, pl, t, "stay", t + QD_WINDOW, horizon, brain, execution)
        else:
            k = tuple(o["k"])
            r = _rollout(w, S, st, pl, t, k, t + o["tau"] + pl.params.plan_ok_s, horizon, brain, execution)
        first = r.pop("_first")
        keys = sorted({_row_key(x) for x in first[1] if not x[1] and x[2] - t <= QD_WINDOW})
        if first[0] != t or keys != sorted(seen):
            raise RuntimeError(f"slate {draw.index} t={t}: the rollout did not start from the decision's own search")
        log = r.pop("_log", None)
        if execution != "a02":
            if o["k"] == "stay":
                r.update(exec=True, not_made_reason=None, not_made_class=None)
            else:
                why = None if r["made"] else not_made_reason(log)
                r.update(exec=bool(r["made"]), not_made_reason=why, not_made_class=not_made_class(why))
        o.update(r)
    d = decisive_of([o["q"] for o in legal])
    extra = {} if execution == "a02" else {"execution": execution, **exec_flags(legal)}
    return {"fmt": QD_FORMAT, "version": QD_VERSION_OF[execution], "master_seed": int(master_seed), "slate": draw.index,
            "night": draw.night, "games": gids, "leagues": list(w.leagues), "mix": mix, "seed": draw.seed,
            "pad": int(draw.pad), "padding": int(draw.pad), "scenario": w.scenario["feed"], "feed": w.scenario,
            "lag": qd_round(w.lag), "know": int(w.know), "t": int(t), "t_wall": int(w.night.base + t),
            "horizon": int(horizon), "window": QD_WINDOW, "mask_level": mask.level,
            "reads": {"screen": scr_read, "hidden": hid_read, "tune": tune_read},
            "features": _features(w, t, S), "options": legal, "masked": masked, "n_legal": len(legal),
            "n_masked": len(masked), "n_candidates": len(seen), **{k: d[k] for k in ("best", "gap", "decisive", "tie", "single")},
            "q_best": None if d["best"] is None else legal[d["best"]]["q"], "q_second": d["second"], **extra}


# ---------------------------------------------------------------------------------------------- shards and manifests
def build_shard(plan: SlatePlan, shard: int, loader: QLoader) -> tuple[list, dict]:
    """(rows of every slate of the shard, the shard's summary). The summary has no clock: same shard, same bytes."""
    rows, slates = [], []
    for i in plan.shard_slates(shard):
        d = draw_slate(plan.pool, plan.master_seed, i)
        r, s = build_slate(d, loader, episode_min=plan.episode_min, points=plan.points, horizon=plan.horizon,
                           mask_level=plan.mask_level, master_seed=plan.master_seed, execution=plan.execution)
        for x in r:
            x["shard"] = int(shard)
        rows.extend(r)
        slates.append(s)
    tab = tie_table(rows)
    summary = {"fmt": QD_FORMAT, "shard": int(shard), "plan": plan.knobs(), "plan_sha256": plan.digest(),
               "slates": slates, "rows": len(rows), "void": sum(1 for s in slates if s["void"]), "ties": tab}
    if plan.execution != "a02":
        summary["exec_ties"] = exec_tie_table(rows)
    return rows, summary


def shard_files(rows: list) -> dict:
    """{relative path: gzip bytes}: one file per (shard, night), rows in slate and time order."""
    by: dict = {}
    for r in rows:
        by.setdefault((r["shard"], r["night"]), []).append(r)
    return {f"shards/qdata_s{s:05d}_{n}.jsonl.gz": qd_gzip([qd_canon(x) for x in rs]) for (s, n), rs in sorted(by.items())}


def write_shard(out_dir, shard: int, rows: list, summary: dict) -> list[str]:
    """Write a shard's files (atomic each) and its summary last (the summary is the shard's done mark)."""
    out = Path(out_dir)
    written = []
    for rel, data in shard_files(rows).items():
        qd_atomic_write(out / rel, data)
        written.append(rel)
    summary = dict(summary, files=sorted(written))
    qd_atomic_write(out / f"summaries/qdata_s{shard:05d}.json", (qd_canon(summary) + "\n").encode())
    return written


def shard_done(out_dir, shard: int, plan: SlatePlan) -> bool:
    """A shard is done when its summary names this plan and every file it lists is there (manifest re-hashes them)."""
    p = Path(out_dir) / f"summaries/qdata_s{shard:05d}.json"
    try:
        s = json.loads(p.read_text())
    except (OSError, ValueError):
        return False
    return s.get("plan_sha256") == plan.digest() and all((Path(out_dir) / f).is_file() for f in s.get("files", []))


def qd_licences() -> dict:
    """The licence entries of the archive's sources, from the data-lake CLI's own lookup (scripts/data_manifest.py)."""
    from scripts.data_manifest import licence_entry
    return {s: licence_entry(s) for s in QD_SOURCES}


def manifest_doc(out_dir, tag: str, licences: dict | None = None) -> dict:
    """The F-05 manifest (redzone-data-manifest/1, kind history: derived shard files) of every shard file under out_dir:
    path, sha256, bytes, rows (lines), night and its fence status, league, source and licence line per file.
    `python scripts/data_manifest.py verify <manifest.json> --root <out_dir>` re-hashes it."""
    from redzone.lab import datamanifest as dm
    dm.check_tag(tag)
    out = Path(out_dir)
    licences = licences if licences is not None else qd_licences()
    block = {s: {k: str(licences[s].get(k) or "") for k in dm.LICENCE_KEYS} for s in QD_SOURCES}
    lic = "; ".join(sorted({block[s]["tag"] or block[s]["licence"] for s in QD_SOURCES}))
    files = []
    for p in sorted((out / "shards").glob("qdata_s*_*.jsonl.gz")):
        rel = p.relative_to(out).as_posix()
        night = p.name.split("_")[2].split(".")[0]
        data = p.read_bytes()
        rows = qd_read_rows(p)
        lgs = sorted({lg for r in rows for lg in r["leagues"]})
        files.append({"bytes": len(data), "fence": dm.fence_status(night), "league": "+".join(lgs), "license": lic,
                      "night": night, "path": rel, "rows": len(rows),
                      "sha256": hashlib.sha256(data).hexdigest(), "sources": list(QD_SOURCES)})
    if not files:
        raise ValueError("no shard files to pin")
    doc = {"format": dm.MANIFEST_FORMAT, "tag": tag, "kind": "history", "private": False, "license": lic, "licences": block,
           "selection": {"end": None, "leagues": None, "start": None},
           "notes": [f"derived: {QD_VERSION} decision-point rows (redzone/lab/gym2/qdata.py); rows = decision points"],
           **dm._derive(files), "files": files}
    doc["manifest_sha256"] = dm.manifest_hash(doc)
    bad = dm.scrub_problems(doc)
    if bad:
        raise ValueError("the manifest would hold something that must never be committed: " + "; ".join(bad[:3]))
    return doc


def all_rows(out_dir) -> list[dict]:
    rows = []
    for p in sorted((Path(out_dir) / "shards").glob("qdata_s*_*.jsonl.gz")):
        rows.extend(qd_read_rows(p))
    rows.sort(key=lambda r: (r["slate"], r["t"]))
    return rows


def run_shards(plan: SlatePlan, a: int, b: int, out_dir, loader: QLoader, *, log=None) -> list[int]:
    """Shards [a, b) into out_dir, each checkpointed by its summary; a shard already done for this plan is skipped
    (resume). Returns the shards built now."""
    built = []
    for k in range(a, b):
        if shard_done(out_dir, k, plan):
            continue
        rows, summary = build_shard(plan, k, loader)
        write_shard(out_dir, k, rows, summary)
        built.append(k)
        if log:
            log(f"shard {k}: {len(summary['slates'])} slates, {summary['rows']} decision points, {summary['void']} void")
    return built

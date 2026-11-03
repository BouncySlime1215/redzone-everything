"""Auto-tune the decider's hand-picked knobs against the scorecards (CMA-ES, numpy only).

    python scripts/tune.py --gens 24          # tune on the TRAIN half of the nights, write data/lab/tune.json
    python scripts/tune.py --report           # the winner vs today on the held-out TEST half

What is tuned (SPACE): switch margin, break-visit floor, return/chase/override leads, must-see,
the crunch rules, the break-visit minimum, entry timing (doubt margins, rewind reach) and, per
league, which kind of entry timing it gets (full / wait-for-a-whistle on the way in / none).

The objective is the scorecards themselves, summed over NFL, NBA and NHL (NFL weighted up):
    crunch live + big plays live and seen + your team's plays + NFL drives live
    + switches that landed on a dead ball - dull seconds (dead air, the wait after landing)
Hard constraints, as a barrier no reward can buy back: 0 mid-play cuts; mid-play entries, plays
missed on entry and dead air not up; big plays seen and your team's plays not down.

No overfitting: the project's held-out slice (docs/architecture.md section 7: NFL `eval.py --n 2`,
NBA/NHL Oct 29 - Nov 1, the Nov 1 demo) is never read while tuning. The remaining nights split in
two by date (alternating). The optimizer only ever sees TRAIN; `report` grades the winner on TEST
and on the HOLDOUT. The demo day is graded separately (python -m redzone.demo 20261101).

Every run is a plain backtest.run on a config overlay, so nothing touches your live settings.
The live app (two Fire TV sticks, hidden-stick standby) is not modelled by the backtest, so the
two_stick.* knobs are not in SPACE: tuning them here would fit noise.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from redzone.config import Config
from redzone.lab import sportsgrade
from redzone.lab.backtest import run
from redzone.lab.optimize import _Overlay
from redzone.models import League

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "data/lab/tune.json"
LEAGUES = ("nfl", "nba", "nhl")
WEIGHT = {"nfl": 1.5, "nba": 1.0, "nhl": 1.0}   # NFL priority
PENALTY = 5.0   # search ranking only: one unit of constraint violation costs this much reward


@dataclass(frozen=True)
class Knob:
    key: str
    lo: float
    hi: float
    kind: str = "float"   # float | int | entry (per-league entry mode, `key` is the league)


# Entry timing per league: 0 = none (enter live), 1 = wait for a whistle on the way in only
# (decider.entry.hold_only), 2 = full cut timing (decider.entry.leagues: hold, rewind, stretch).
ENTRY_MODES = ("live", "hold_only", "full")

SPACE: tuple[Knob, ...] = (
    Knob("decider.switch_margin", 3, 25, "int"),
    Knob("decider.break_destination_min", 5, 50, "int"),
    Knob("decider.return_tolerance", 0, 15, "int"),
    Knob("decider.chase_max_deficit", 5, 40, "int"),
    Knob("decider.locked_override_lead", 8, 35, "int"),
    Knob("decider.must_see_score", 80, 99, "int"),
    Knob("decider.must_see_lead", 8, 35, "int"),
    Knob("decider.crunch.hold_personal", 1, 15, "int"),
    Knob("decider.crunch.personal_lead", 1, 15, "int"),
    Knob("decider.crunch.hop_min_break_s", 5, 60, "int"),
    Knob("decider.entry.visit_min_s", 30, 240, "int"),
    Knob("decider.entry.relaxed_margin_s", 2, 15),
    Knob("decider.entry.urgent_margin_s", 0.5, 6),
    Knob("decider.entry.max_rewind_s", 30, 180, "int"),
    Knob("nba", 0, 2.999, "entry"),
    Knob("nhl", 0, 2.999, "entry"),
)


def defaults(base: Config) -> dict:
    """Today's values for every knob (constants in decider.py where the config has none)."""
    from redzone.decider import decider as D
    const = {"decider.entry.visit_min_s": D.VISIT_MIN_S, "decider.entry.relaxed_margin_s": D.ENTRY_RELAXED_S,
             "decider.entry.urgent_margin_s": D.ENTRY_URGENT_S, "decider.entry.max_rewind_s": D.ENTRY_MAX_REWIND_S,
             "decider.crunch.hold_personal": 5, "decider.crunch.personal_lead": 5,
             "decider.crunch.hop_min_break_s": 20}
    out = {}
    for k in SPACE:
        if k.kind == "entry":
            full = base.get("decider.entry.leagues") or ["nfl"]
            hold = base.get("decider.entry.hold_only") or []
            out[k.key] = 2 if k.key in full else 1 if k.key in hold else 0
        else:
            v = base.get(k.key)
            out[k.key] = const.get(k.key) if v is None else v
    return out


def to_overrides(p: dict) -> dict:
    """Knob values -> dotted config overrides (entry modes become the two league lists)."""
    out = {k: v for k, v in p.items() if k not in LEAGUES}
    full, hold = ["nfl"], []
    for lg in ("nba", "nhl"):
        if lg in p:
            m = ENTRY_MODES[int(p[lg])]
            (full if m == "full" else hold if m == "hold_only" else []).append(lg)
    out["decider.entry.leagues"] = full
    out["decider.entry.hold_only"] = hold
    return out


def decode(x: np.ndarray) -> dict:
    p = {}
    for k, u in zip(SPACE, np.clip(x, 0.0, 1.0)):
        v = k.lo + float(u) * (k.hi - k.lo)
        p[k.key] = int(v) if k.kind == "entry" else int(round(v)) if k.kind == "int" else round(v, 2)
    return p


def encode(p: dict) -> np.ndarray:
    return np.array([((p[k.key] + (0.5 if k.kind == "entry" else 0.0)) - k.lo) / (k.hi - k.lo) for k in SPACE])


# ----- the nights, split by date --------------------------------------------------------------

def _night(reps) -> str:
    """The ET calendar night a slate belongs to (a 9 PM kickoff ends after midnight UTC)."""
    t = min(r.wall[0] for r in reps) - 5 * 3600
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y%m%d")


# The project's held-out slice (docs/architecture.md section 7): never read while tuning.
HOLDOUT_FROM = {"nba": "20261029", "nhl": "20261029"}   # these nights and later
HOLDOUT_NFL_N = 2                                        # scripts/eval.py --n 2's slates
TUNE_NIGHTS = 30                                         # NBA/NHL nights loaded before the holdout


def split(items: list, night_of, held=lambda i: False) -> dict[str, list]:
    """TRAIN / TEST by night, after taking out the project's HOLDOUT: the remaining sorted nights
    alternate, so each half spans the window and a night (an NFL 1 PM and 4 PM slate, say) never
    sits on both sides. HOLDOUT is only ever graded by `report`."""
    # A held-out slate's whole night stays out of tuning (an NFL 4 PM slate shares its 1 PM night).
    held_nights = {night_of(i) for i in items if held(i)}
    pool = [i for i in items if night_of(i) not in held_nights]
    nights = sorted({night_of(i) for i in pool})
    side = {n: ("train" if j % 2 == 0 else "test") for j, n in enumerate(nights)}
    out = {"train": [], "test": [], "holdout": [i for i in items if held(i)], "all": list(items)}
    for i in pool:
        out[side[night_of(i)]].append(i)
    return out


def load_units() -> dict[str, list]:
    """(night, replays, targets, held_out) per slate/night. NFL: the scorecard's 16 slates, the two
    of `eval.py --n 2` held out. NBA/NHL: up to TUNE_NIGHTS archived nights through EVAL_END, the
    nights from HOLDOUT_FROM on held out."""
    import importlib.util
    units = {}
    for name in ("eval", "eval_sports"):
        spec = importlib.util.spec_from_file_location(f"_rz_{name}", ROOT / f"scripts/{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if name == "eval":
            held = {frozenset(r.gid for r in reps) for reps in mod.eval_set(HOLDOUT_NFL_N)}
            units["nfl"] = [(_night(reps), reps, None, frozenset(r.gid for r in reps) in held)
                            for reps in mod.eval_set(16)]
        else:
            for lg in ("nba", "nhl"):
                units[lg] = [(day, reps, targets, day >= HOLDOUT_FROM[lg])
                             for day, reps, targets in mod.nights(League(lg), TUNE_NIGHTS, mod.EVAL_END)]
    return units


# ----- grading one unit (worker side) ---------------------------------------------------------

_UNITS: dict[str, list] = {}
_BASE: Config | None = None


def _pinned() -> Config:
    pinned = ROOT / "data/eval_config.yaml"
    return Config(user_path=pinned if pinned.exists() else ROOT / "data/config.yaml")


def grade_unit(job: tuple[dict, str, int]) -> dict:
    """Raw counts for one slate / night under params (summed later; percentages at the end)."""
    params, lg, i = job
    cfg = _Overlay(_BASE, to_overrides(params))
    _, reps, targets, _held = _UNITS[lg][i]
    for r in reps:
        if len(r._cache) > 5000:
            r._cache.clear()
    if lg == "nfl":
        mine = set(cfg.get("teams.nfl") or [])
        r = run(reps, cfg, mine)
        c = r.cut
        return dict(big=r.big_plays, seen=r.big_plays_caught, live=r.big_plays_live, cuts=len(r.mid_play_cuts),
                    dead=r.dead_air_s, watched=r.watched_s, crunch=r.crunch, crunch_live=r.crunch_live,
                    my_crunch=r.my_crunch, my_crunch_live=r.my_crunch_live, my_big=r.my_big_plays,
                    my_seen=r.my_big_plays_live, drive=r.drive_plays, drive_live=r.drive_plays_live,
                    switches=len(r.switches), hours=r.hours, entries=c.entries, mid_entries=c.entries_mid_play,
                    wait=c.entry_wait_s, missed=c.plays_missed_entry)
    mine = set(cfg.get(f"teams.{lg}") or ["WSH"])
    rep = run(reps, cfg, mine, targets=targets)
    k = sportsgrade.grade(reps, rep.switches, mine, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
    return dict(big=k.big, seen=k.big_seen, live=k.big_live, cuts=len(k.cuts), dead=k.dead, watched=k.watched,
                crunch=k.crunch, crunch_live=k.crunch_live, my_crunch=k.my_crunch, my_crunch_live=k.my_crunch_live,
                my_big=0, my_seen=0, drive=0, drive_live=0, switches=k.switches, hours=k.hours,
                entries=k.cut.entries, mid_entries=k.cut.entries_mid_play, wait=k.cut.entry_wait_s,
                missed=k.cut.plays_missed_entry)


def metrics(rows: list[dict]) -> dict:
    t = {k: sum(r[k] for r in rows) for k in rows[0]}
    pct = lambda a, b: round(100 * a / b, 2) if b else 100.0  # noqa: E731
    return {"mid_play_cuts": t["cuts"], "entries_mid_play": t["mid_entries"], "entries": t["entries"],
            "landed_dead_pct": pct(t["entries"] - t["mid_entries"], t["entries"]),
            "crunch_live_pct": pct(t["crunch_live"], t["crunch"]), "big_live_pct": pct(t["live"], t["big"]),
            "big_seen_pct": pct(t["seen"], t["big"]),
            "your_team_crunch_live_pct": pct(t["my_crunch_live"], t["my_crunch"]),
            "your_team_big_seen_pct": pct(t["my_seen"], t["my_big"]),
            "nfl_drive_live_pct": pct(t["drive_live"], t["drive"]),
            "dead_air_pct": pct(t["dead"], t["watched"]),
            "plays_missed_entry": t["missed"],
            "entry_wait_s": round(t["wait"] / max(1, t["entries"]), 1),
            "switches_per_hour": round(t["switches"] / max(0.1, t["hours"]), 1)}


# ----- the objective --------------------------------------------------------------------------

def reward(m: dict, lg: str) -> float:
    """Higher is better. Everything the user asked for, in scorecard points."""
    r = (m["crunch_live_pct"] + 0.5 * m["big_live_pct"] + 0.5 * m["big_seen_pct"]
         + 0.5 * m["your_team_crunch_live_pct"] + 0.5 * m["landed_dead_pct"]
         - m["dead_air_pct"] - 0.1 * m["entry_wait_s"])
    if lg == "nfl":
        r += 0.5 * m["your_team_big_seen_pct"] + 0.3 * m["nfl_drive_live_pct"]
    return r


def violation(m: dict, base: dict) -> float:
    """How far past the hard constraints (0 = feasible). Never traded against reward."""
    v = 10.0 * m["mid_play_cuts"]
    v += max(0, m["entries_mid_play"] - base["entries_mid_play"])
    v += max(0, m["plays_missed_entry"] - base["plays_missed_entry"])
    v += 2.0 * max(0.0, m["dead_air_pct"] - base["dead_air_pct"])
    v += max(0.0, base["big_seen_pct"] - 0.5 - m["big_seen_pct"])
    v += max(0.0, base["your_team_crunch_live_pct"] - m["your_team_crunch_live_pct"])
    v += max(0.0, base["your_team_big_seen_pct"] - m["your_team_big_seen_pct"])
    return v


def fitness(per: dict, base: dict) -> tuple[float, float]:
    """(total violation, -weighted reward gain): lexicographic, feasibility first."""
    gain = sum(WEIGHT[lg] * (reward(per[lg], lg) - reward(base[lg], lg)) for lg in per)
    viol = sum(violation(per[lg], base[lg]) for lg in per)
    return viol, -gain


# ----- CMA-ES (Hansen's (mu/mu_w, lambda), box [0,1] by clipping) -------------------------------

class CMA:
    def __init__(self, x0: np.ndarray, sigma: float, lam: int, seed: int = 7):
        n = len(x0)
        self.n, self.lam, self.mean, self.sigma = n, lam, x0.astype(float), sigma
        self.rng = np.random.default_rng(seed)
        mu = lam // 2
        w = np.log(mu + 0.5) - np.log(np.arange(1, mu + 1))
        self.w = w / w.sum()
        self.mu, self.mueff = mu, 1.0 / (self.w ** 2).sum()
        self.cc = (4 + self.mueff / n) / (n + 4 + 2 * self.mueff / n)
        self.cs = (self.mueff + 2) / (n + self.mueff + 5)
        self.c1 = 2 / ((n + 1.3) ** 2 + self.mueff)
        self.cmu = min(1 - self.c1, 2 * (self.mueff - 2 + 1 / self.mueff) / ((n + 2) ** 2 + self.mueff))
        self.damps = 1 + 2 * max(0, math.sqrt((self.mueff - 1) / (n + 1)) - 1) + self.cs
        self.pc, self.ps, self.C = np.zeros(n), np.zeros(n), np.eye(n)
        self.chin = math.sqrt(n) * (1 - 1 / (4 * n) + 1 / (21 * n * n))
        self.gen = 0

    def state(self) -> dict:
        return {"mean": self.mean.tolist(), "sigma": self.sigma, "C": self.C.tolist(), "pc": self.pc.tolist(),
                "ps": self.ps.tolist(), "gen": self.gen, "rng": self.rng.bit_generator.state}

    def load(self, st: dict) -> None:
        self.mean, self.sigma, self.gen = np.array(st["mean"]), float(st["sigma"]), int(st["gen"])
        self.C, self.pc, self.ps = np.array(st["C"]), np.array(st["pc"]), np.array(st["ps"])
        self.rng.bit_generator.state = st["rng"]

    def ask(self) -> list[np.ndarray]:
        vals, vecs = np.linalg.eigh(self.C)
        self._B, self._D = vecs, np.sqrt(np.maximum(vals, 1e-20))
        z = self.rng.standard_normal((self.lam, self.n))
        return [self.mean + self.sigma * (self._B @ (self._D * zi)) for zi in z]

    def tell(self, xs: list[np.ndarray], order: list[int]) -> None:
        old = self.mean
        X = np.array([xs[i] for i in order[: self.mu]])
        self.mean = self.w @ X
        y = (self.mean - old) / self.sigma
        Cinv_half = self._B @ np.diag(1 / self._D) @ self._B.T
        self.ps = (1 - self.cs) * self.ps + math.sqrt(self.cs * (2 - self.cs) * self.mueff) * (Cinv_half @ y)
        self.gen += 1
        hs = np.linalg.norm(self.ps) / math.sqrt(1 - (1 - self.cs) ** (2 * self.gen)) / self.chin < 1.4 + 2 / (self.n + 1)
        self.pc = (1 - self.cc) * self.pc + hs * math.sqrt(self.cc * (2 - self.cc) * self.mueff) * y
        Y = (X - old) / self.sigma
        self.C = ((1 - self.c1 - self.cmu) * self.C + self.c1 * (np.outer(self.pc, self.pc)
                  + (1 - hs) * self.cc * (2 - self.cc) * self.C) + self.cmu * (Y.T * self.w) @ Y)
        self.sigma *= math.exp((self.cs / self.damps) * (np.linalg.norm(self.ps) / self.chin - 1))
        self.sigma = min(self.sigma, 0.5)


# ----- driver ---------------------------------------------------------------------------------

def _pool(workers: int):
    import multiprocessing as mp
    return mp.get_context("fork").Pool(workers)   # forked after loading: the archive is shared


def evaluate_many(pool, params_list: list[dict], side: str, halves: dict) -> list[dict]:
    jobs, where = [], []
    for c, p in enumerate(params_list):
        for lg in LEAGUES:
            for i in halves[lg][side]:
                jobs.append((p, lg, i))
                where.append((c, lg))
    rows = pool.map(grade_unit, jobs, chunksize=1)
    out = [{lg: [] for lg in LEAGUES} for _ in params_list]
    for (c, lg), r in zip(where, rows):
        out[c][lg].append(r)
    return [{lg: metrics(v) for lg, v in o.items()} for o in out]


def setup() -> tuple[dict, dict]:
    global _BASE, _UNITS
    _BASE = _pinned()
    _UNITS = load_units()
    halves = {lg: split(list(range(len(u))), lambda i, u=u: u[i][0], lambda i, u=u: u[i][3])
              for lg, u in _UNITS.items()}
    return _UNITS, halves


def tune(gens: int = 24, lam: int = 12, sigma: float = 0.1, workers: int | None = None, seed: int = 7,
         log=print, resume: bool = False) -> dict:
    """`resume`: pick up from data/lab/tune.json. With a saved CMA state it continues exactly; an
    older checkpoint that only has its best point restarts the search centred there."""
    units, halves = setup()
    workers = workers or os.cpu_count() or 2
    today = defaults(_BASE)
    with _pool(workers) as pool:
        base = evaluate_many(pool, [today], "train", halves)[0]
        log("baseline (train): " + json.dumps({lg: base[lg] for lg in LEAGUES}))
        es = CMA(encode(today), sigma, lam, seed)
        best = {"params": today, "fit": (0.0, 0.0), "train": base}
        history = []
        saved = json.loads(OUT.read_text()) if resume and OUT.exists() else {}
        if saved.get("best") and saved.get("today") == today and saved.get("baseline_train") != base:
            # The code under test moved (a merge changed the decider): scores against the old
            # baseline mean nothing now. Keep only where the search was, as a starting point.
            es.mean = encode(saved["best"]["params"])
            log("baseline moved since the checkpoint: restarting the search centred on its best point")
        elif saved.get("best") and saved.get("today") == today:
            best = saved["best"] | {"fit": tuple(saved["best"]["fit"])}
            history = saved.get("history", [])
            if saved.get("cma"):
                es.load(saved["cma"])
            else:
                es.mean = encode(best["params"])
            log(f"resumed at gen {len(history)}: best gain {-best['fit'][1]:+.2f} (viol {best['fit'][0]:.2f})")
        t0 = time.time()
        for g in range(len(history), len(history) + gens):
            xs = es.ask()
            ps = [decode(x) for x in xs]
            res = evaluate_many(pool, ps, "train", halves)
            fits = [fitness(r, base) for r in res]
            # CMA ranks on a blend so it can walk back into the feasible region along a gradient;
            # the winner is still picked feasibility-first (best["fit"] is lexicographic).
            order = sorted(range(len(xs)), key=lambda i: fits[i][1] + PENALTY * fits[i][0])
            es.tell([np.clip(x, 0, 1) for x in xs], order)
            i = order[0]
            for j in range(len(xs)):
                if fits[j] < best["fit"]:
                    best = {"params": ps[j], "fit": fits[j], "train": res[j]}
            why = {lg: round(violation(res[i][lg], base[lg]), 2) for lg in LEAGUES}
            history.append({"gen": g, "best_fit": fits[i], "sigma": round(es.sigma, 4),
                            "feasible": sum(f[0] == 0 for f in fits)})
            log(f"gen {g:2d}  {time.time() - t0:5.0f}s  best viol {fits[i][0]:.2f} gain {-fits[i][1]:+.2f}"
                f" {why}  overall gain {-best['fit'][1]:+.2f} (viol {best['fit'][0]:.2f})  sigma {es.sigma:.3f}")
            _save({"today": today, "baseline_train": base, "best": best, "history": history, "cma": es.state(),
                   "halves": {lg: {k: [units[lg][j][0] for j in v] for k, v in h.items() if k != "all"}
                              for lg, h in halves.items()}})
    return best


def report(params: dict | None = None, workers: int | None = None) -> dict:
    """Today vs the winner on TRAIN, on TEST, and on the project's HOLDOUT nights."""
    units, halves = setup()
    saved = json.loads(OUT.read_text()) if OUT.exists() else {}
    params = params or saved["best"]["params"]
    today = defaults(_BASE)
    with _pool(workers or os.cpu_count() or 2) as pool:
        out = {}
        for side in ("train", "test", "holdout"):
            a, b = evaluate_many(pool, [today, params], side, halves)
            out[side] = {"today": a, "tuned": b, "fit": fitness(b, a)}
    saved["report"] = out
    saved["best"] = saved.get("best") or {"params": params}
    _save(saved)
    return out


def _save(obj: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    tmp.replace(OUT)

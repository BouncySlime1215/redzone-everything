"""The arms the evaluation harness can run: each is the show's own planner with at most one hook changed.

Every arm runs inside the show (`park_show.Show` on fake sticks, `scripts/park_replay.py`): the planner, the Rule #1
mask (leave, land, ready, guard, high-leverage seconds) and the stick moves are always the show's own. An arm may only
change WHICH legal move the planner takes, through one of the planner's two existing seams:

    weight  show.pref(g) is multiplied by weight(show, g, t), clamped to ARMEVAL_CLAMP (Director v1 enters the same seam)
    rate    Planner.unknown: an object with bind(arrs, setting) and rate(game, last reported second, now), the worth of a
            second the feed has not reported (stage 6's ModelRate entered here, ledger L105)

LOCAL arms (cheap on the Mac):
    planner        the show as it is (every flag at its default): the baseline
    director_v1    park.seen + park.director_v1 on (noggin/director.py, ledger L114)
    leverage_only  a one-feature scorer: each game weighted by its leverage at the last reported second, over the mean
                   of the games in progress (V2-PLAN 8.3 "one-feature scorer")
    random_legal   each game weighted log-uniformly in the clamp, redrawn every ARMEVAL_RANDOM_BLOCK seconds from
                   sha256(seed, night, block, game): random among the moves the mask allows, the same on every run
    noop_rate      the rate seam filled with the planner's own guess (unk_w x leverage): the no-op control; it must
                   decide byte for byte as `planner` (tests/test_armeval.py)

HEAVY arms (plug-ins; the model is trained and judged at scale on Kaggle / Colab, never on the Mac):
    stage6_rerank  rate seam;   --hook MODULE:FACTORY  --model FILE   (A-03 brings ModelRate into the repo)
    q_learner      weight seam; --hook MODULE:FACTORY  --model FILE   (A-02 / A-06 learned director)
A factory is called as factory(HookContext) and returns the hook object. The model file must pass the leak fence
(redzone/lab/fence.py: a training cutoff before the first scored night, no fenced night trained on); its sha256 goes
into the verdict.
Why: WORKPLAN v2 F-04 / A-03: one plug-in shape for every baseline and learned arm, so all are judged the same way.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

ARMEVAL_CLAMP = (0.1, 4.0)          # the weight seam's clamp: stage 6's 0.1x .. 4x of the planner's own guess (L105)
ARMEVAL_RANDOM_BLOCK = 60           # random_legal redraws its weights once a show minute


class HeavyArmUnavailable(RuntimeError):
    """A heavy arm was asked for without its hook or its model file (they live on Kaggle / Colab until A-03 / A-06)."""


def clamp(w: float) -> float:
    lo, hi = ARMEVAL_CLAMP
    return min(hi, max(lo, float(w)))


@dataclass
class HookContext:
    """What a heavy arm's factory is given: the night's recorded games and the model's metadata (fence-checked)."""
    night: str
    leagues: list
    gids: list
    seed: int
    model_path: str = ""
    model_meta: dict = field(default_factory=dict)
    unk_w: float = 0.0          # the planner's own weight for an unreported second (its guess is unk_w x leverage)


class Arm:
    """The planner as it is. Subclasses change one seam."""
    name = "planner"
    heavy = False
    seam = "none"

    def __init__(self, seed: int = 0):
        self.seed = int(seed)

    def replay_kw(self) -> dict:
        """Extra keyword arguments for park_replay.build (flags this arm turns on)."""
        return {}

    def describe(self) -> dict:
        """Everything that defines this arm (goes into the config hash)."""
        return {"name": self.name, "seam": self.seam, "heavy": self.heavy, **self.replay_kw()}

    def attach(self, show, night: str) -> None:
        """Install the arm's hook on a built show, before the first tick."""


class DirectorArm(Arm):
    name = "director_v1"

    def replay_kw(self) -> dict:
        return {"seen": True, "director": True}


class WeightArm(Arm):
    """The weight seam: show.pref(g) times this arm's weight for game g at the show's current second, clamped."""
    seam = "weight"

    def attach(self, show, night: str) -> None:
        own = show.pref
        self.night = str(night)

        def pref(g: int) -> float:
            t = show.last_t if show.last_t is not None else 0
            return own(g) * clamp(self.weight(show, g, t))
        show.pref = pref

    def weight(self, show, g: int, t: int) -> float:
        return 1.0


def _reported(show, g: int, t: int) -> int:
    """The last second of game g the feed has reported to the show at clock second t (never the future)."""
    x = show.A[g]
    return min(x.n - 1, max(0, min(t + show.know, show.feed.edge(g))))


class LeverageOnlyArm(WeightArm):
    name = "leverage_only"

    def weight(self, show, g: int, t: int) -> float:
        on = [h for h, x in enumerate(show.A) if x.a0 <= t <= x.a1]
        if g not in on:
            return 1.0
        lv = {h: float(show.A[h].lev[_reported(show, h, t)]) for h in on}
        mean = sum(lv.values()) / len(lv)
        return lv[g] / mean if mean > 0 else 1.0


class RandomLegalArm(WeightArm):
    name = "random_legal"

    def describe(self) -> dict:
        return {**super().describe(), "block": ARMEVAL_RANDOM_BLOCK, "seed": self.seed}

    def weight(self, show, g: int, t: int) -> float:
        lo, hi = ARMEVAL_CLAMP
        h = hashlib.sha256(f"{self.seed}|{self.night}|{int(t) // ARMEVAL_RANDOM_BLOCK}|{g}".encode()).digest()
        u = int.from_bytes(h[:8], "big") / 2.0 ** 64
        return lo * (hi / lo) ** u


class PlannerGuessRate:
    """The rate seam's no-op: exactly the planner's own guess for an unreported second (Plan.unk_w x leverage)."""

    def __init__(self, unk_w: float):
        self.unk_w = float(unk_w)
        self.A = None

    def bind(self, arrs, setting) -> None:
        self.A = arrs

    def rate(self, g: int, K: int, t: int) -> float:
        return self.unk_w * self.A[g].lev[K]


def planner_guess_hook(ctx: HookContext) -> PlannerGuessRate:
    """The template factory for a heavy rate arm (`--hook redzone.lab.armeval.arms:planner_guess_hook`): it returns
    the planner's own guess, so a heavy arm built on it must decide exactly as `planner` (the plug-in's own no-op)."""
    return PlannerGuessRate(ctx.unk_w)


class NoopRateArm(Arm):
    name = "noop_rate"
    seam = "rate"

    def attach(self, show, night: str) -> None:
        show.pl.unknown = PlannerGuessRate(show.pl.p.unk_w)


class HookArm(Arm):
    """A heavy arm: a factory from a module (MODULE:FACTORY or path/to/file.py:FACTORY) builds the hook for each night."""
    heavy = True

    def __init__(self, seed: int = 0, hook: str = "", model: str = ""):
        super().__init__(seed)
        self.hook, self.model = hook, model
        self.model_meta: dict = {}
        self.model_sha256 = ""

    def describe(self) -> dict:
        return {**super().describe(), "hook": self.hook, "model_sha256": self.model_sha256}

    def check(self, nights: list) -> dict:
        """Refuse unless the hook and a fence-clean model are given. Returns the fence block for the verdict."""
        from redzone.lab import fence
        if not self.hook or not self.model:
            raise HeavyArmUnavailable(
                f"{self.name} is a heavy arm: give --hook MODULE:FACTORY and --model FILE. Its model is trained and "
                f"scored at scale on Kaggle / Colab (WORKPLAN A-03 / A-06); the Mac only runs it on a few nights.")
        p = Path(self.model)
        if not p.exists():
            raise HeavyArmUnavailable(f"{self.name}: model file {self.model} does not exist")
        self.model_meta = fence.load_guarded(p, nights)          # FenceError ("REFUSED ...") when it may not be scored here
        self.model_sha256 = fence.file_sha256(p)
        return {"model_sha256": self.model_sha256, "training_cutoff": fence.cutoff_of(self.model_meta),
                "model_fence": "clean"}

    def factory(self):
        mod, _, attr = self.hook.partition(":")
        if mod.endswith(".py"):
            spec = importlib.util.spec_from_file_location(f"_armeval_hook_{Path(mod).stem}", mod)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
        else:
            m = importlib.import_module(mod)
        return getattr(m, attr or "make_hook")

    def attach(self, show, night: str) -> None:
        ctx = HookContext(str(night), [str(r.league.value) for r in show.replays], [r.gid for r in show.replays],
                          self.seed, self.model, dict(self.model_meta), float(show.pl.p.unk_w))
        hook = self.factory()(ctx)
        if self.seam == "rate":
            show.pl.unknown = hook
        else:
            own = show.pref

            def pref(g: int) -> float:
                t = show.last_t if show.last_t is not None else 0
                return own(g) * clamp(hook.weight(show, g, t))
            show.pref = pref


class Stage6Arm(HookArm):
    name = "stage6_rerank"
    seam = "rate"


class QLearnerArm(HookArm):
    name = "q_learner"
    seam = "weight"


ARMEVAL_ARMS = {a.name: a for a in (Arm, DirectorArm, LeverageOnlyArm, RandomLegalArm, NoopRateArm, Stage6Arm, QLearnerArm)}


def make_arm(name: str, seed: int = 0, hook: str = "", model: str = "") -> Arm:
    if name not in ARMEVAL_ARMS:
        raise KeyError(f"unknown arm {name!r}; known: {', '.join(sorted(ARMEVAL_ARMS))}")
    cls = ARMEVAL_ARMS[name]
    return cls(seed, hook, model) if issubclass(cls, HookArm) else cls(seed)

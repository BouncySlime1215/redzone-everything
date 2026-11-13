"""Stage 6 reranker as a plug-in arm: the four-feature value model priced into the planner's `unknown.rate` hook.

    python scripts/arm_verdict.py run --arm stage6_rerank --hook redzone.lab.armeval.stage6:factory \
        --model packed.npz --nights 20261021 --minutes 15
    python scripts/arm_verdict.py l105 data/armeval/<run>        # L105's metric on that run (high-leverage points)
    python -m redzone.lab.armeval.stage6 pack --json stage4_state_only.json --npz stage4_state_only.npz \
        --training-cutoff 2025-09-30 --out packed.npz                       # one fenced file from the Kaggle export

A port of `ModelRate` from research/brain/notebooks/stage6_rerank.py (Kaggle `redzone-brain-stage6-rerank` v2, ledger
L105). The planner keeps every decision about what is legal, ready and warranted; the model only replaces the worth of
a second the feed has not reported (the planner's own guess: unk_w x leverage). Per LEAGUE the model's number is
rescaled so its causal running mean equals the guess's running mean in that league, then clamped to 0.1x .. 4x the
guess for that second (arms.ARMEVAL_CLAMP). A game whose league the model does not hold keeps the planner's guess.
The model is the STATE-ONLY export (gbm4: lev_now, since_stop, last_kind, since_to), asked once per game per 10 s of
its own clock from rows stamped at or before that moment (causal); the full v3 model (60 features) is not ported.

Loading: one packed .npz (the tree arrays plus a `meta` JSON array) read ONCE as bytes; the bytes must hash to what the
harness fence-checked, the meta must pass redzone/lab/fence.py for the night (a training_cutoff before it, no fenced
night trained on) and the trees must reproduce the export's own check rows. The show itself may only take a model
through the F-02 registry (`promoted`): research arms run unpromoted models by design, never the app.
Why: WORKPLAN v2 A-03: stage 6 (L105) must run as an arm of the F-04 harness so every learned director is judged the
same way, and its L105 result can be rerun at scale from the repo.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from redzone.lab import fence
from redzone.lab.armeval.arms import ARMEVAL_CLAMP

STAGE6_STEP = 10.0                     # the model is asked once per game per 10 s of its clock (stage6_rerank.STEP_S)
STAGE6_MISSING = -999.0                # a feature the export reads as unknown
STAGE6_REG = {"nba": 4, "cbb": 2}      # regulation periods (the half is reg // 2)
STAGE6_WHISTLE = ("foul", "violation", "outofbounds", "substitution", "kickedball", "delay", "technical", "flagrant",
                  "jumpball", "freethrow")
STAGE6_CHECK_TOL = 1e-4                # the packed trees must reproduce the export's stored check rows this closely
# L105's league weights for high-leverage value (stage5_policy.WEIGHTS = default_config.yaml league weights, Nov 11)
STAGE6_LEAGUE_W = {"nfl": 1.15, "cfb": 1.10, "nba": 1.05, "cbb": 1.0, "nhl": 0.95, "mlb": 0.9, "wcbb": 0.9}
STAGE6_BOOT, STAGE6_BOOT_SEED = 400, 5  # stage5_policy.boot: 400 resampled nights, numpy default_rng(5)
STAGE6_HL_BAR = 5.0                     # the pre-registered bar (STAGE3-DESIGN 'Stage 6 after its review')


class Stage6ModelRefused(ValueError):
    """The model file may not be used: changed bytes, a fence problem, not the state-only export, or bad trees."""


# ---- the model file ----------------------------------------------------------------------------------------------------
def stage6_from_bytes(data: bytes, eval_nights=(), want_sha256: str = "") -> dict:
    """{"J": export meta, "T": tree arrays, "sha256"} from the packed bytes, after every check (Stage6ModelRefused)."""
    sha = hashlib.sha256(data).hexdigest()
    if want_sha256 and sha != want_sha256:
        raise Stage6ModelRefused(f"model bytes {sha[:12]} are not the file the harness fence-checked ({want_sha256[:12]})")
    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        if "meta" not in z.files:
            raise Stage6ModelRefused("not a packed stage 6 model (no meta array): pack the export first")
        J = json.loads(z["meta"].tobytes().decode())
        T = {k: z[k] for k in z.files if k != "meta"}
    try:
        fence.guard_model(J, eval_nights, name="stage 6 model")
    except fence.FenceError as e:
        raise Stage6ModelRefused(str(e)) from e
    if J.get("stage6_kind") != "state_only":
        raise Stage6ModelRefused(f"stage6_kind {J.get('stage6_kind')!r}: only the state-only export is ported")
    model = {"J": J, "T": T, "sha256": sha}
    worst = stage6_check(model)
    if not worst <= STAGE6_CHECK_TOL:
        raise Stage6ModelRefused(f"the trees miss the export's own check rows by {worst:.3g}")
    return model


def stage6_load(path, eval_nights=(), want_sha256: str = "") -> dict:
    """The packed model at `path`, read once (the bytes checked are the bytes used)."""
    return stage6_from_bytes(Path(path).read_bytes(), eval_nights, want_sha256)


def stage6_promoted(run_id: str, eval_nights=(), base=None, root=None) -> dict:
    """The only road for the SHOW: a promoted F-02 registry run (UnpromotedModelError otherwise), then every check."""
    from redzone.models_rt import registry
    return stage6_from_bytes(registry.read_promoted(run_id, base=base, root=root), eval_nights)


def stage6_pack(json_path, npz_path, training_cutoff: str, out) -> dict:
    """One packed file from the Kaggle export pair, stamped with its training cutoff. The cutoff may not be earlier than
    the day before the export's first test night (that would understate what the model saw). Returns the meta."""
    J = json.loads(Path(json_path).read_text())
    firsts = [s.get("first_test") for s in (J.get("split") or {}).values() if isinstance(s, dict) and s.get("first_test")]
    cut = fence.night_key(training_cutoff)
    if firsts:
        f = fence.night_key(max(firsts))
        last_train = (date(int(f[:4]), int(f[4:6]), int(f[6:])) - timedelta(days=1)).strftime("%Y%m%d")
        if cut < last_train:
            raise Stage6ModelRefused(f"training_cutoff {cut} is before {last_train}, the day before the export's first "
                                     f"test night: the model saw nights up to then")
    with np.load(npz_path, allow_pickle=False) as z:
        T = {k: z[k] for k in z.files}
    meta = {**J, "training_cutoff": f"{cut[:4]}-{cut[4:6]}-{cut[6:]}", "stage6_kind": "state_only",
            "stage6_source": {"json_sha256": fence.file_sha256(Path(json_path)), "npz_sha256": fence.file_sha256(Path(npz_path))}}
    np.savez(out, meta=np.frombuffer(json.dumps(meta, sort_keys=True).encode(), dtype=np.uint8), **T)
    return meta


def stage6_walk(T: dict, key: str, X) -> np.ndarray:
    """The summed tree output for each row of X (left when x <= threshold; X read at float32 as it was fitted)."""
    X = np.atleast_2d(np.asarray(X, dtype=np.float32)).astype(np.float64)
    f, thr, lo, hi, val = (T[key + s] for s in ("_f", "_t", "_l", "_r", "_v"))
    n = np.broadcast_to(T[key + "_roots"].astype(np.int64), (len(X), len(T[key + "_roots"]))).copy()
    r = np.arange(len(X))[:, None]
    for _ in range(96):
        ff = f[n]
        act = ff >= 0
        if not act.any():
            break
        n = np.where(act, np.where(X[r, np.where(act, ff, 0)] <= thr[n], lo[n], hi[n]), n)
    return val[n].sum(axis=1)


def stage6_predict(league: str, X, model: dict, targets=None) -> dict:
    """{target: calibrated forecast per row} for one league (stage4_state_only_reader.predict)."""
    out = {}
    for tg, spec in model["J"]["models"].get(league, {}).items():
        if targets is None or tg in targets:
            out[tg] = np.interp(stage6_walk(model["T"], f"{league}_{tg}", X), spec["iso"][0], spec["iso"][1])
    return out


def stage6_check(model: dict) -> float:
    """The largest miss against the rows the export stored with each tree (0 for a faithful file)."""
    worst = 0.0
    for lg, d in model["J"]["models"].items():
        for tg, spec in d.items():
            p = stage6_predict(lg, spec["check_rows"], model, (tg,))[tg]
            worst = max(worst, float(np.max(np.abs(p - np.asarray(spec["check_out"], dtype=float)))))
    return worst


# ---- the four state features, from the rows the feed has shown -------------------------------------------------------
def stage6_kind(type_text: str, period: int, half: int) -> int:
    """What a row says about play (stage4_reader.kind_of): 0 action, 1 whistle, 2 review, 3 timeout, 4 period end,
    5 halftime, 6 end of game."""
    tn = type_text.lower().replace(" ", "").replace("-", "").replace("\n", "")
    if "timeout" in tn:
        return 3
    if tn in ("endgame", "endofgame"):
        return 6
    if tn == "endperiod":
        return 5 if period == half else 4
    if "challenge" in tn or "review" in tn:
        return 2
    return 1 if any(w in tn for w in STAGE6_WHISTLE) else 0


class Stage6Rows:
    """One game's rows as the state features read them: wall stamps (the show's own), kind, last stoppage, last timeout."""

    def __init__(self, league: str, plays: list, wall: list):
        half, per = STAGE6_REG.get(league, 4) // 2, 0
        self.wall = [float(w) for w in wall]
        self.kind, self.last_stop, self.last_to = [], [], []
        ls = lt = -1
        for i, p in enumerate(plays):
            try:
                per = int(float(((p.get("period") or {}).get("number")))) or per or 1
            except (TypeError, ValueError):
                per = per or 1
            k = stage6_kind(str((p.get("type") or {}).get("text") or ""), per, half)
            ls = i if k > 0 else ls
            lt = i if k == 3 else lt
            self.kind.append(k)
            self.last_stop.append(ls)
            self.last_to.append(lt)

    def features(self, te: float, lev: float) -> dict:
        """lev_now, since_stop, last_kind, since_to at wall second te (rows stamped at or before te only)."""
        j = min(max(bisect_right(self.wall, te) - 1, 0), len(self.wall) - 1)

        def since(k: int) -> float:
            return min(te - self.wall[k], 9999.0) if k >= 0 else 9999.0
        return {"lev_now": lev, "since_stop": since(self.last_stop[j]), "last_kind": float(self.kind[j]),
                "since_to": since(self.last_to[j])}


# ---- the hook -----------------------------------------------------------------------------------------------------------
class Stage6Rate:
    """The planner's `unknown` hook (ModelRate): per league, causal running means of the planner's guess and of the
    model's number; rate = model x (guess mean / model mean), clamped to ARMEVAL_CLAMP x the guess for that second."""

    def __init__(self, model: dict, leagues: list, rows: list, base: int, shifts: list, unk_w: float):
        self.m, self.lg, self.rows, self.base, self.unk_w = model, list(leagues), list(rows), int(base), float(unk_w)
        self.shifts = [float(x) for x in shifts] if shifts else [0.0] * len(self.lg)
        self.sb, self.sm = defaultdict(float), defaultdict(float)
        self.calls = self.clamped = self.searches = self.top_differs = 0
        self.cache: dict = {}
        self._k, self._r, self._b = None, {}, {}
        self.A = None

    def bind(self, A, s) -> None:
        self.A = A

    def value(self, g: int, K: int):
        """The model's number for game g at the last 10 s step at or before its reported second K (None: no model)."""
        rows, lg = self.rows[g], self.lg[g]
        if rows is None or lg not in self.m["J"]["models"]:
            return None
        x = self.A[g]
        t0 = self.base + x.a0 - self.shifts[g]                       # the game's first second, in its feed's wall time
        i = max(0, int((self.base + K - self.shifts[g] - t0) // STAGE6_STEP))
        if (g, i) not in self.cache:
            te = t0 + i * STAGE6_STEP
            p = min(max(int(te + self.shifts[g] - self.base), 0), len(x.lev) - 1)
            f = rows.features(te, float(x.lev[p]))
            J = self.m["J"]
            row = [[float(f[k]) if f.get(k) is not None else J.get("missing", STAGE6_MISSING) for k in J["features"]]]
            tg = J["rank_target"].get(lg, "v60")
            pr = stage6_predict(lg, row, self.m, (tg,)).get(tg)
            self.cache[(g, i)] = None if pr is None else max(0.0, float(pr[0]))
        return self.cache[(g, i)]

    def rate(self, g: int, K: int, t: int) -> float:
        base = self.unk_w * self.A[g].lev[K]
        v = self.value(g, K)
        if v is None:
            self._note(g, K, t, base, base)
            return base
        lg = self.lg[g]
        self.sb[lg] += base
        self.sm[lg] += v
        r = v * (self.sb[lg] / self.sm[lg]) if self.sm[lg] > 0 else base
        lo, hi = ARMEVAL_CLAMP[0] * base, ARMEVAL_CLAMP[1] * base
        self.calls += 1
        if r < lo or r > hi:
            self.clamped += 1
        out = min(max(r, lo), hi)
        self._note(g, K, t, out, base)
        return out

    def _note(self, g, K, t, r, base) -> None:
        if (K, t) != self._k:
            self._flush()
            self._k = (K, t)
        self._r[g], self._b[g] = r, base

    def _flush(self) -> None:
        if len(self._r) >= 2:
            self.searches += 1
            if max(self._r, key=self._r.get) != max(self._b, key=self._b.get):
                self.top_differs += 1
        self._r, self._b = {}, {}

    def stats(self) -> dict:
        self._flush()
        return {"rate_calls": self.calls, "rate_clamped": self.clamped, "searches": self.searches,
                "top_differs": self.top_differs, "games_with_model": sum(1 for r in self.rows if r is not None)}


def factory(ctx) -> Stage6Rate:
    """The harness factory (`--hook redzone.lab.armeval.stage6:factory --model FILE`): the fence-checked model, re-read
    and re-checked from the same bytes, over the night's recorded games."""
    model = stage6_load(ctx.model_path, [ctx.night], getattr(ctx, "model_sha256", ""))
    reps = list(getattr(ctx, "replays", None) or [])
    rows = [Stage6Rows(lg, reps[i].plays, reps[i].wall) if i < len(reps) and lg in model["J"]["models"] else None
            for i, lg in enumerate(ctx.leagues)]
    return Stage6Rate(model, ctx.leagues, rows, getattr(ctx, "base", 0), getattr(ctx, "shifts", None) or [], ctx.unk_w)


# ---- L105's metric on a harness run ------------------------------------------------------------------------------------
def stage6_hl_card(raw: dict, facts: list) -> dict:
    """High-leverage value (stage5_policy.show_metrics): graded value2 in the grader's high-leverage stretches, league
    weighted; seen = the seconds the judge counted as shown. Plus all value2 seen / total (the 'all value' gate)."""
    h = raw["header"]
    base, (a, b) = h["base"], h["span"]
    shown = {(g, ti) for g, ti, _m, _v in raw["shown"]}
    hl_seen = hl_tot = v2_seen = 0.0
    for g, f in enumerate(facts):
        lg = h["games"][g]["league"]
        w = STAGE6_LEAGUE_W.get(lg, 1.0)
        per: dict = defaultdict(float)
        for t, v, *_ in f.get("v2", ()):
            per[int(t - base)] += v
        hls = sorted(f.get("hl", ()))
        for sec, v in per.items():
            if v <= 0 or not (a <= sec < b):
                continue
            if (g, sec) in shown:
                v2_seen += v
            t = base + sec
            if any(p <= t <= q for p, q in hls):
                hl_tot += v * w
                hl_seen += v * w if (g, sec) in shown else 0.0
    return {"hl_seen": round(hl_seen, 4), "hl_total": round(hl_tot, 4), "v2_seen": round(v2_seen, 4),
            "v2_total": round(float(h["v2_total"]), 4)}


def stage6_boot(rows: list, num: str, den: str, seed: int = STAGE6_BOOT_SEED) -> tuple:
    """(points, [5th, 95th percentile]): 100 x (arm's pooled num/den - baseline's), nights resampled (stage5_policy.boot)."""
    def f(rr, k):
        return sum(r[k][num] for r in rr) / max(1e-9, sum(r[k][den] for r in rr))
    d = 100 * (f(rows, "x") - f(rows, "b"))
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(STAGE6_BOOT):
        pick = [rows[i] for i in rng.integers(0, len(rows), len(rows))]
        bs.append(100 * (f(pick, "x") - f(pick, "b")))
    return round(d, 2), [round(float(np.percentile(bs, 5)), 2), round(float(np.percentile(bs, 95)), 2)]


def stage6_l105(pairs: list) -> dict:
    """L105's gates from per-night (mix, arm card, baseline card) triples: pooled and per-mix high-leverage points
    (bar +5 with the interval's low end above 0) and all value (no worse than -2)."""
    rows = [{"mix": m, "x": x, "b": b} for m, x, b in pairs]
    if not rows:
        return {"nights": 0}
    d, iv = stage6_boot(rows, "hl_seen", "hl_total")
    dv, ivv = stage6_boot(rows, "v2_seen", "v2_total")
    per_mix = {}
    for mx in sorted({r["mix"] for r in rows}):
        q = [r for r in rows if r["mix"] == mx]
        pd_, piv = stage6_boot(q, "hl_seen", "hl_total")
        per_mix[mx] = {"nights": len(q), "hl_points": pd_, "interval90": piv, "ok": pd_ >= STAGE6_HL_BAR and piv[0] > 0}
    return {"nights": len(rows), "hl_pooled": {"points": d, "interval90": iv, "ok": d >= STAGE6_HL_BAR and iv[0] > 0},
            "hl_per_mix": per_mix, "all_value": {"points": dv, "interval90": ivv, "ok": dv >= -2.0},
            "note": "L105 metric on this run's nights; L105 itself is 140 Kaggle nights on another engine (docs/stage6-arm.md)"}


def stage6_main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stage 6 reranker: pack a Kaggle export into one fenced model file.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--json", required=True)
    p.add_argument("--npz", required=True)
    p.add_argument("--training-cutoff", required=True)
    p.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("model")
    c.add_argument("--nights", nargs="*", default=[])
    a = ap.parse_args(argv)
    if a.cmd == "pack":
        meta = stage6_pack(a.json, a.npz, a.training_cutoff, a.out)
        print(f"packed {a.out}: training_cutoff {meta['training_cutoff']}, sha256 {fence.file_sha256(Path(a.out))}")
        return 0
    m = stage6_load(a.model, a.nights)
    print(f"ok {a.model}: sha256 {m['sha256']}, cutoff {fence.cutoff_of(m['J'])}, check miss {stage6_check(m):.2g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(stage6_main())

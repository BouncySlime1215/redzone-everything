#!/usr/bin/env python3
"""[EXP-110] FIM-PP (ICLR 2026, in-context foundation model for marked temporal point processes; MIT code + weights,
huggingface FIM4Science/fim-pp, ~16M params, up to 22 marks) as a free baseline for break timing.

    python scripts/exp110_fimpp.py --tokens SHARDS --leagues nfl,nba,mlb --out OUT [--context 500] [--max-test 300]

Serves: North Star "dead air %" (knowing when the next break comes); owner: lane 1. Pre-registered in
docs/experiments.md (EXP-110).

Each game becomes a marked point process: one event per stoppage (mark = the stoppage type, 12 kinds), time = wall
seconds since the game's first event (summed v1 DT buckets). Context = train games of the same league (in-context,
zero-shot); targets = test games. For every break in a test game the model sees the game's history up to that break
and predicts the gap to the next break as E[gap] = integral of the survival exp(-integral of the total intensity),
evaluated on a time grid. Baseline: the per-mark train median gap. Same per-league split as EXP-103/107.

The checkpoint's input names come from the OpenFIM crash course (context_event_times / _types / _seq_lengths,
inference_event_times / _types / _seq_lengths, intensity_evaluation_times); shapes are probed on the first call and
printed, so a mismatch fails loudly with the tensors it sent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import noggin_tokens as T  # noqa: E402

PERSONAL = "personal-use, not for commercial release"
MAX_MARKS = 22
GRID = np.concatenate([np.linspace(0, 120, 61)[1:], np.linspace(130, 1800, 60)])   # seconds after a break


def breaks_of(tokens: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(times s, marks) of the stoppages in one v1-tokenized game."""
    import world_model_pilot as W
    k, dpos, s = W.positions(tokens)
    if not len(k):
        return np.zeros(0), np.zeros(0, int)
    dt = np.array([0.0 if tokens[i] == T.UNK_DT else W.DT_MID[int(tokens[i]) - T.DT0] for i in dpos])
    t = np.cumsum(dt)
    st = tokens[s].astype(int) - T.STOP0
    keep = (st > 0) & (st < len(T.STOPS))                 # 0 = none
    return t[keep], st[keep]


def median_baseline(train_seqs) -> dict[int, float]:
    gaps: dict[int, list] = {}
    for t, m in train_seqs:
        for i in range(len(t) - 1):
            gaps.setdefault(int(m[i]), []).append(float(t[i + 1] - t[i]))
    return {k: float(np.median(v)) for k, v in gaps.items() if v}


def expected_gap(lam_total: np.ndarray, grid: np.ndarray = GRID) -> float:
    """E[gap] from total intensity on a grid of times after the last event (survival integral, tail beyond the grid
    approximated with the last intensity)."""
    dg = np.diff(np.concatenate([[0.0], grid]))
    cum = np.cumsum(lam_total * dg)
    surv = np.exp(-cum)
    tail = surv[-1] / max(lam_total[-1], 1e-6)
    return float((surv * dg).sum() + tail)


def pad(seqs, max_len: int):
    n = len(seqs)
    tt = np.zeros((n, max_len), np.float32)
    mm = np.zeros((n, max_len), np.int64)
    ln = np.zeros(n, np.int64)
    for i, (t, m) in enumerate(seqs):
        t, m = t[:max_len], m[:max_len]
        tt[i, :len(t)], mm[i, :len(m)], ln[i] = t, m, len(t)
    return tt, mm, ln


def fim_gaps(model, ctx_seqs, hist_list, dev) -> list[float]:
    """One FIM-PP call per history: total intensity on GRID after the history's last event -> expected gap."""
    import torch
    L = max(2, max(len(t) for t, _ in ctx_seqs))
    ct, cm, cl = pad(ctx_seqs, L)
    out = []
    for t, m in hist_list:
        it, im, il = pad([(t, m)], max(2, len(t)))
        ev = (t[-1] + GRID).astype(np.float32)[None]
        batch = {"context_event_times": torch.tensor(ct)[None, ..., None], "context_event_types": torch.tensor(cm)[None, ..., None],
                 "context_seq_lengths": torch.tensor(cl)[None],
                 "inference_event_times": torch.tensor(it)[..., None], "inference_event_types": torch.tensor(im)[..., None],
                 "inference_seq_lengths": torch.tensor(il),
                 "intensity_evaluation_times": torch.tensor(ev)}
        batch = {k: v.to(dev) for k, v in batch.items()}
        with torch.no_grad():
            try:
                r = model(batch)
            except Exception:
                print("FIM-PP call failed; sent:", {k: tuple(v.shape) for k, v in batch.items()}, flush=True)
                raise
        lam = None
        for key in ("predicted_intensity_values", "intensity_values", "predicted_intensities", "intensities"):
            if isinstance(r, dict) and key in r:
                lam = r[key]
                break
        if lam is None:
            raise SystemExit(f"unknown FIM-PP output keys: {list(r) if isinstance(r, dict) else type(r)}")
        lam = lam.float().cpu().numpy().reshape(-1, len(GRID)) if lam.shape[-1] == len(GRID) else \
            lam.float().cpu().numpy().reshape(len(GRID), -1).T
        out.append(expected_gap(lam.sum(0)))
    return out


def main(argv=None) -> int:
    import world_model_pilot as W
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", type=Path, required=True, help="vocab v1 shards")
    ap.add_argument("--leagues", default="nfl,nba,mlb")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--context", type=int, default=500, help="train games as in-context sequences (<= 2000)")
    ap.add_argument("--max-test", type=int, default=300, help="test games per league")
    ap.add_argument("--max-breaks", type=int, default=150)
    ap.add_argument("--baseline-only", action="store_true")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    games, lic = W.load_shards(a.tokens, a.leagues.split(","))
    sp = W.split(games)
    model = dev = None
    if not a.baseline_only:
        import torch
        from transformers import AutoModel
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        model = AutoModel.from_pretrained("FIM4Science/fim-pp", trust_remote_code=True).to(dev).eval()
    rng = np.random.default_rng(1234)
    res = {}
    for lg in sorted({g["league"] for g in sp["test"]}):
        tr = [breaks_of(g["tokens"]) for g in sp["train"] if g["league"] == lg]
        tr = [(t[: a.max_breaks], m[: a.max_breaks]) for t, m in tr if len(t) >= 3]
        if not tr or max(float(t[-1]) for t, _ in tr) <= 0:
            res[lg] = {"skipped": "no event clock (Retrosheet MLB has no wall or game clock)"}
            print(lg, res[lg], flush=True)
            continue
        base = median_baseline(tr)
        ctx = [tr[i] for i in rng.choice(len(tr), min(a.context, len(tr)), replace=False)]
        err_b, err_f, per_game = [], [], []
        for g in [g for g in sp["test"] if g["league"] == lg][: a.max_test]:
            t, m = breaks_of(g["tokens"])
            t, m = t[: a.max_breaks], m[: a.max_breaks]
            if len(t) < 3:
                continue
            truth = np.diff(t)
            b = np.array([base.get(int(x), np.median(list(base.values()))) for x in m[:-1]])
            eb = np.abs(b - truth)
            row = {"b": eb.tolist()}
            if model is not None:
                f = np.array(fim_gaps(model, ctx, [(t[: i + 1], m[: i + 1]) for i in range(len(t) - 1)], dev))
                row["f"] = np.abs(f - truth).tolist()
                err_f += row["f"]
            err_b += row["b"]
            per_game.append(row)
        mean = lambda pick, key: float(np.mean([v for r in pick for v in r.get(key, [])])) if pick else None  # noqa: E731
        res[lg] = {"games": len(per_game), "breaks": len(err_b),
                   "gap_mae_s_median_baseline": {"value": round(float(np.mean(err_b)), 2) if err_b else None,
                                                 "ci95": W.boot(per_game, lambda p: mean(p, "b"))},
                   "gap_mae_s_fimpp_zero_shot": None if not err_f else
                   {"value": round(float(np.mean(err_f)), 2), "ci95": W.boot(per_game, lambda p: mean(p, "f"))}}
        print(lg, json.dumps(res[lg]), flush=True)
    g = {"exp": "EXP-110", "model": "FIM4Science/fim-pp (MIT)", "context_games": a.context, "by_league": res,
         "license": PERSONAL if any(x != "open" and not str(x).startswith("open") for x in lic) else "open",
         "data_sha256_16": W.data_hash(games)}
    (a.out / "grades.json").write_text(json.dumps(g, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

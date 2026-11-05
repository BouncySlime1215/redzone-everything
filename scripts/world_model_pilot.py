#!/usr/bin/env python3
"""World model pilot (EXP-100): event tokens -> a small transformer -> held-out-night grades, end to end.

    python scripts/world_model_pilot.py --out /content/wm [--leagues nfl] [--minutes 60] [--smoke]

Serves: North Star "% of hindsight-oracle value achieved" (the world model the planner searches, master plan v2);
owner: lane 1 (Noggin training). Plumbing proof for Saturday's ~100M run (Nick, Nov 4 8:00 PM), not the product.

1. Data: public play-by-play from GitHub releases only (nflverse NFL 2001-2024 by default; hoopR NBA only with --allow-espn-derived; see
   docs/data-sources.md), tokenized by scripts/noggin_tokens.py (vocab v1: 6 tokens per event = kind, dt, clock,
   margin, points, stoppage; one BOS per game + both teams). The factory's EVAL_FIRST_NIGHT cut drops every game
   on or after the scorecard window, so no locked or scorecard night is ever downloaded or tokenized.
2. Split by whole night (time order): the last 10% of nights = test, the 10% before = valid, the rest = train.
3. Model: a decoder-only transformer (~25-30M params), next-token loss; the checkpoint is chosen on VALID only
   (early stop), with a checkpoint every 30 min.
4. Grades on the held-out test nights, each with a 95% game-bootstrap CI, beside count baselines fit on train:
   next-event kind accuracy (vs a bigram on the previous kind), next-stoppage calibration (ECE, Brier, vs the
   frequency after the previous kind) and break-timing MAE (the seconds from a break to the next event, vs the
   train median per stoppage type). Plus training/inference throughput (tokens/s) to size the 100M run.
Writes OUT/grades.json and prints it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import noggin_tokens as T  # noqa: E402

SEED = 7
# NFL 2025 sits past the factory cut (scorecard + locked window), so it is never fetched
SEASONS = {"nba": list(range(2019, 2027)), "nfl": list(range(2001, 2025))}
# NFL 1999-2000 lack play wallclocks in nflverse (71% / 17% of gaps read 0 s, vs ~2.5% from 2003 on), so their
# time-to-next labels are wrong; they start in 2001 (3.7%).
# hoopR NBA is an ESPN-derived release: Wave 5, default OFF until Nick decides (docs/datasources/sources-catalog.md;
# Disney's terms bar AI training). It is read only with --allow-espn-derived.
ESPN_DERIVED = {"nba"}
BREAKS = ("timeout", "two_minute_warning", "end_period", "halftime", "intermission", "review", "injury",
          "pitching_change", "score_break")
# dt bucket b covers [2^(b/2.6) - 1, 2^((b+1)/2.6) - 1) seconds (noggin_tokens.dt_token); its midpoint in seconds
DT_MID = np.array([(2 ** (b / 2.6) - 1 + 2 ** ((b + 1) / 2.6) - 1) / 2 for b in range(T.DT_BUCKETS)])


# ---------------------------------------------------------------------------------------------- data
def fetch(league: str, year: int, raw: Path) -> Path | None:
    from redzone.noggin import factory as F
    url = F.SOURCES[league]["url"].format(y=year)
    dst = raw / league / f"{year}.parquet"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(url, dst.with_suffix(".tmp"))
        except Exception as e:  # noqa: BLE001 - a missing season is skipped, and the ledger says so
            print(f"{league} {year}: not downloaded ({type(e).__name__})", flush=True)
            return None
        os.replace(dst.with_suffix(".tmp"), dst)
    return dst


def build_games(leagues, out: Path, smoke: bool) -> list[dict]:
    """[{league, gid, night, tokens}] for every tokenized game."""
    games = []
    for lg in leagues:
        years = SEASONS[lg][-1:] if smoke else SEASONS[lg]
        for y in years:
            raw = fetch(lg, y, out / "raw")
            if raw is None:
                continue
            info = T.build(lg, raw, y, out / "tokens")
            arr = np.load(out / "tokens" / lg / f"{y}.npy")
            for gid, night, off, n, _ in info["index"]:
                games.append({"league": lg, "gid": gid, "night": night, "tokens": arr[off:off + n]})
            print(f"{lg} {y}: {info['games']} games, {info['tokens']:,} tokens", flush=True)
            if smoke:
                games = games[:120]
    return games


def split(games: list[dict]) -> dict[str, list[dict]]:
    nights = sorted({g["night"] for g in games})
    n = len(nights)
    va0, te0 = nights[int(n * 0.8)], nights[int(n * 0.9)]
    out = {"train": [], "valid": [], "test": []}
    for g in games:
        out["test" if g["night"] >= te0 else "valid" if g["night"] >= va0 else "train"].append(g)
    return out


def data_hash(games) -> str:
    h = hashlib.sha256()
    for g in sorted(games, key=lambda g: (g["league"], g["gid"])):
        h.update(g["gid"].encode())
        h.update(g["tokens"].tobytes())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------------------------- model
def make_model(vocab: int, d: int, layers: int, heads: int, ctx: int):
    import torch
    import torch.nn as nn

    class Block(nn.Module):
        def __init__(self):
            super().__init__()
            self.ln1, self.ln2 = nn.LayerNorm(d), nn.LayerNorm(d)
            self.qkv, self.proj = nn.Linear(d, 3 * d), nn.Linear(d, d)
            self.mlp = nn.Sequential(nn.Linear(d, 4 * d), nn.GELU(), nn.Linear(4 * d, d))

        def forward(self, x):
            b, t, _ = x.shape
            q, k, v = self.qkv(self.ln1(x)).view(b, t, 3, heads, d // heads).permute(2, 0, 3, 1, 4)
            a = torch.nn.functional.scaled_dot_product_attention(q, k, v, is_causal=True)
            x = x + self.proj(a.transpose(1, 2).reshape(b, t, d))
            return x + self.mlp(self.ln2(x))

    class WM(nn.Module):
        def __init__(self):
            super().__init__()
            self.tok, self.pos = nn.Embedding(vocab, d), nn.Embedding(ctx, d)
            self.blocks = nn.ModuleList(Block() for _ in range(layers))
            self.ln = nn.LayerNorm(d)
            self.head = nn.Linear(d, vocab, bias=False)
            self.head.weight = self.tok.weight          # tied
            self.apply(self._init)

        @staticmethod
        def _init(m):                                   # GPT-2 init: N(0, 0.02), zero bias
            if isinstance(m, (nn.Linear, nn.Embedding)):
                nn.init.normal_(m.weight, 0.0, 0.02)
            if isinstance(m, nn.Linear) and m.bias is not None:
                nn.init.zeros_(m.bias)

        def forward(self, idx):
            x = self.tok(idx) + self.pos(torch.arange(idx.shape[1], device=idx.device))
            for blk in self.blocks:
                x = blk(x)
            return self.head(self.ln(x))

    return WM()


def windows(games, ctx: int, rng) -> np.ndarray:
    """Every game cut into ctx+1 windows at multiples of a whole number of events (the same cut as grade())."""
    out = []
    step = (ctx // T.TOKENS_PER_EVENT) * T.TOKENS_PER_EVENT
    for g in games:
        t = g["tokens"].astype(np.int64)
        for s in range(0, max(1, len(t) - 1), step):
            w = t[s:s + ctx + 1]
            if len(w) < 2:
                continue
            out.append(np.pad(w, (0, ctx + 1 - len(w)), constant_values=T.PAD))
    a = np.stack(out)
    rng.shuffle(a)
    return a


# ---------------------------------------------------------------------------------------------- grades
def positions(t: np.ndarray):
    """Indices (into a game's tokens) of each event's kind, dt and stop token."""
    n_ev = (len(t) - 4) // T.TOKENS_PER_EVENT
    base = 3 + T.TOKENS_PER_EVENT * np.arange(n_ev)
    return base, base + 1, base + 5


def kind_of(tok: np.ndarray) -> np.ndarray:
    return tok.astype(np.int64)


def stop_name(tok: int) -> str:
    i = int(tok) - T.STOP0
    return T.STOPS[i] if 0 <= i < len(T.STOPS) else "unknown"


def baselines(train) -> dict:
    """Count models on train: next kind given the previous kind, P(stoppage) given the kind, break dt per stop."""
    from collections import Counter, defaultdict
    nxt, stop, dts = defaultdict(Counter), defaultdict(lambda: [0, 0]), defaultdict(list)
    for g in train:
        t = g["tokens"]
        k, dpos, s = positions(t)
        ks = t[k]
        for a, b in zip(ks[:-1], ks[1:]):
            nxt[int(a)][int(b)] += 1
        for kk, ss in zip(ks, t[s]):
            st = stop[int(kk)]
            st[0] += stop_name(ss) != "none"
            st[1] += 1
        for i in range(1, len(k)):
            nm = stop_name(t[s[i - 1]])
            if nm in BREAKS and t[dpos[i]] != T.UNK_DT:
                dts[nm].append(DT_MID[int(t[dpos[i]]) - T.DT0])
    prior = sum(v[0] for v in stop.values()) / max(1, sum(v[1] for v in stop.values()))
    return {"next": {a: c.most_common(1)[0][0] for a, c in nxt.items()},
            "p_stop": {a: (v[0] + prior) / (v[1] + 1) for a, v in stop.items()}, "p_prior": prior,
            "dt_med": {k: float(np.median(v)) for k, v in dts.items() if v}}


def ece(p, y, bins=10):
    p, y = np.asarray(p, float), np.asarray(y, float)
    if not len(p):
        return None
    idx = np.clip((p * bins).astype(int), 0, bins - 1)
    return float(sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).sum() for b in range(bins)
                     if (idx == b).any()) / len(p))


def boot(per_game: list[tuple], fn, n=300, seed=SEED):
    """95% CI of fn over games resampled with replacement (grading rule 4)."""
    if len(per_game) < 2:
        return None
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        pick = [per_game[i] for i in rng.integers(0, len(per_game), len(per_game))]
        v = fn(pick)
        if v is not None and math.isfinite(v):
            vals.append(v)
    return [round(float(np.percentile(vals, 2.5)), 4), round(float(np.percentile(vals, 97.5)), 4)] if vals else None


def grade(model, games, base, ctx, dev) -> dict:
    """Per game: kind hits (model, bigram), stop probs (model, freq) + labels, break dt errors (model, median)."""
    import torch
    rows = []
    model.eval()
    stop_none = T.STOP0 + T.STOPS.index("none")
    stop_ids = torch.arange(T.STOP0, T.STOP0 + len(T.STOPS), device=dev)
    dt_ids = torch.arange(T.DT0, T.DT0 + T.DT_BUCKETS, device=dev)
    kind_ids = torch.arange(T.KIND0, T.DT0, device=dev)
    mid = torch.tensor(DT_MID, device=dev, dtype=torch.float32)
    with torch.no_grad():
        for g in games:
            t = g["tokens"].astype(np.int64)
            k, dpos, s = positions(t)
            if len(k) < 3:
                continue
            logits = []
            step = (ctx // T.TOKENS_PER_EVENT) * T.TOKENS_PER_EVENT
            for a in range(0, len(t) - 1, step):       # non-overlapping windows on event boundaries
                x = torch.tensor(t[a:a + ctx], device=dev)[None]
                with torch.autocast(device_type=dev, dtype=torch.bfloat16, enabled=dev == "cuda"):
                    logits.append(model(x)[0].float())
            lg = torch.cat(logits)                      # lg[i] predicts token i+1
            r = {"kind": [], "kind_b": [], "p": [], "p_b": [], "y": [], "dt": [], "dt_b": []}
            for i in range(1, len(k)):
                pk = T.KIND0 + int(lg[k[i] - 1][kind_ids].argmax())
                r["kind"].append(pk == t[k[i]])
                r["kind_b"].append(base["next"].get(int(t[k[i - 1]]), -1) == t[k[i]])
                ps = torch.softmax(lg[s[i] - 1][stop_ids], 0)
                r["p"].append(float(1 - ps[stop_none - T.STOP0]))
                r["p_b"].append(base["p_stop"].get(int(t[k[i]]), base["p_prior"]))
                r["y"].append(stop_name(t[s[i]]) != "none")
                nm = stop_name(t[s[i - 1]])
                if nm in BREAKS and t[dpos[i]] != T.UNK_DT:
                    true = DT_MID[int(t[dpos[i]]) - T.DT0]
                    pd_ = torch.softmax(lg[dpos[i] - 1][dt_ids], 0)
                    r["dt"].append(abs(float((pd_ * mid).sum()) - true))
                    r["dt_b"].append(abs(base["dt_med"].get(nm, true) - true))
            rows.append(r)
    cat = lambda pick, key: [v for r in pick for v in r[key]]   # noqa: E731
    mean = lambda pick, key: float(np.mean(cat(pick, key))) if cat(pick, key) else None   # noqa: E731
    out = {}
    for name, key in (("next_kind_acc", "kind"), ("next_kind_acc_bigram", "kind_b"),
                      ("break_dt_mae_s", "dt"), ("break_dt_mae_s_baseline", "dt_b")):
        out[name] = {"value": None if mean(rows, key) is None else round(mean(rows, key), 4),
                     "ci95": boot(rows, lambda p, key=key: mean(p, key)), "n": len(cat(rows, key))}
    for name, key in (("stop_ece", "p"), ("stop_ece_baseline", "p_b")):
        out[name] = {"value": None if not cat(rows, key) else round(ece(cat(rows, key), cat(rows, "y")), 4),
                     "ci95": boot(rows, lambda p, key=key: ece(cat(p, key), cat(p, "y")))}
    y = np.array(cat(rows, "y"), float)
    for name, key in (("stop_brier", "p"), ("stop_brier_baseline", "p_b")):
        out[name] = round(float(np.mean((np.array(cat(rows, key)) - y) ** 2)), 4) if len(y) else None
    out["games"] = len(rows)
    return out


# ---------------------------------------------------------------------------------------------- train
def main(argv=None) -> int:
    import torch
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--allow-espn-derived", action="store_true", help="only once Nick says yes (Wave 5)")
    ap.add_argument("--minutes", type=float, default=60.0, help="training wall-clock budget")
    ap.add_argument("--d", type=int, default=512)
    ap.add_argument("--layers", type=int, default=8)
    ap.add_argument("--heads", type=int, default=8)
    ap.add_argument("--ctx", type=int, default=1020)          # 170 events
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=6e-4)
    ap.add_argument("--smoke", action="store_true", help="CPU test: one season, a tiny model, a few steps")
    a = ap.parse_args(argv)
    if a.smoke:
        a.d, a.layers, a.heads, a.ctx, a.batch, a.minutes = 64, 2, 2, 120, 8, 0.3
    t_all = time.time()
    torch.manual_seed(SEED)
    rng = np.random.default_rng(SEED)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    a.out.mkdir(parents=True, exist_ok=True)
    blocked = ESPN_DERIVED & set(a.leagues.split(",")) if not a.allow_espn_derived else set()
    if blocked:
        raise SystemExit(f"{sorted(blocked)}: ESPN-derived release, default OFF until Nick decides (Wave 5)")
    games = build_games(a.leagues.split(","), a.out, a.smoke)
    sp = split(games)
    counts = {k: {"games": len(v), "tokens": int(sum(len(g["tokens"]) for g in v)),
                  "nights": len({g["night"] for g in v}),
                  "first": min((g["night"] for g in v), default=None), "last": max((g["night"] for g in v), default=None)}
              for k, v in sp.items()}
    print("split:", json.dumps(counts), flush=True)
    model = make_model(T.VOCAB_SIZE, a.d, a.layers, a.heads, a.ctx).to(dev)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"model: {n_params / 1e6:.1f}M params on {dev}", flush=True)
    tr = windows(sp["train"], a.ctx, rng)
    va = windows(sp["valid"], a.ctx, rng)[:256]
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, betas=(0.9, 0.95), weight_decay=0.1)
    est_steps = max(50, int(a.minutes * 60 * 2))            # refined after the first 20 steps
    amp = dict(device_type=dev, dtype=torch.bfloat16, enabled=dev == "cuda")
    loss_fn = torch.nn.CrossEntropyLoss(ignore_index=T.PAD)

    def lr_at(s):
        return a.lr * min(1.0, (s + 1) / 100) * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * min(1.0, s / est_steps))))

    def val_loss():
        model.eval()
        tot, n = 0.0, 0
        with torch.no_grad(), torch.autocast(**amp):
            for i in range(0, len(va), a.batch):
                x = torch.tensor(va[i:i + a.batch], device=dev)
                tot += float(loss_fn(model(x[:, :-1]).float().reshape(-1, T.VOCAB_SIZE), x[:, 1:].reshape(-1)))
                n += 1
        model.train()
        return round(tot / max(1, n), 4)

    ck, best_path = a.out / "ckpt.pt", a.out / "best.pt"
    step, ep, best, curve, last_ck, bad = 0, 0, float("inf"), [], time.time(), 0
    if ck.exists():                                        # resume (rerun on the same runtime)
        st = torch.load(ck, map_location=dev, weights_only=False)
        model.load_state_dict(st["model"])
        opt.load_state_dict(st["opt"])
        step, ep, best, curve = st["step"], st["ep"], st["best"], st["curve"]
        print(f"resumed at step {step}", flush=True)
    t0, toks, stop = time.time(), 0, False
    model.train()
    while not stop:
        for i in range(0, len(tr) - a.batch + 1, a.batch):
            x = torch.tensor(tr[i:i + a.batch], device=dev)
            for gr in opt.param_groups:
                gr["lr"] = lr_at(step)
            with torch.autocast(**amp):
                loss = loss_fn(model(x[:, :-1]).float().reshape(-1, T.VOCAB_SIZE), x[:, 1:].reshape(-1))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            step += 1
            toks += x[:, :-1].numel()
            if step == 20:
                el = time.time() - t0
                est_steps = max(50, int(a.minutes * 60 / (el / 20)))
                print(f"{toks / el:,.0f} train tokens/s -> ~{est_steps} steps in {a.minutes:.0f} min", flush=True)
            if step % 100 == 0 or a.smoke and step % 10 == 0:
                v = val_loss()
                curve.append({"step": step, "epoch": ep, "train_loss": round(loss.item(), 4), "valid_loss": v})
                print(curve[-1], flush=True)
                if v < best:
                    best, bad = v, 0
                    torch.save(model.state_dict(), best_path)
                else:
                    bad += 1
                if bad >= 5:                                # early stop on valid
                    print("early stop on valid", flush=True)
                    stop = True
                    break
            if time.time() - last_ck > 1800:
                torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "step": step, "ep": ep,
                            "best": best, "curve": curve}, ck)
                last_ck = time.time()
            if time.time() - t0 > a.minutes * 60 or step >= est_steps:
                stop = True
                break
        ep += 1
    train_s = time.time() - t0
    v = val_loss()
    curve.append({"step": step, "epoch": ep, "train_loss": round(loss.item(), 4), "valid_loss": v})
    if v < best or not best_path.exists():
        torch.save(model.state_dict(), best_path)
    model.load_state_dict(torch.load(best_path, map_location=dev))         # chosen on VALID
    ti = time.time()
    base = baselines(sp["train"])
    test = grade(model, sp["test"], base, a.ctx, dev)
    inf_toks = sum(len(g["tokens"]) for g in sp["test"])
    inf_s = time.time() - ti
    trn = grade(model, sp["train"][:200], base, a.ctx, dev)       # the train side of the gap
    g = {"exp": "EXP-100", "seed": SEED, "data_sha256_16": data_hash(games), "vocab": T.VOCAB_VERSION,
         "vocab_size": T.VOCAB_SIZE, "leagues": a.leagues, "split": counts, "selection": "valid loss (early stop)",
         "recipe": {"d": a.d, "layers": a.layers, "heads": a.heads, "ctx": a.ctx, "batch": a.batch, "lr": a.lr,
                    "params_m": round(n_params / 1e6, 1), "steps": step, "epochs_seen": round(step * a.batch / max(1, len(tr)), 2)},
         "learning_curve": curve, "best_valid_loss": best,
         "test": test, "train_slice": {k: trn[k] for k in ("next_kind_acc", "stop_ece", "break_dt_mae_s")},
         "throughput": {"device": torch.cuda.get_device_name(0) if dev == "cuda" else "cpu",
                        "train_tokens_per_s": round(toks / max(1e-9, train_s)),
                        "eval_tokens_per_s": round(inf_toks / max(1e-9, inf_s)),
                        "train_s": round(train_s), "total_s": round(time.time() - t_all)},
         "locked": "never downloaded (factory EVAL_FIRST_NIGHT cut); test = the last 10% of pre-cut nights"}
    tp = g["throughput"]["train_tokens_per_s"]
    g["sizing_100m"] = {"tokens_per_s_scaled": round(tp * n_params / 100e6),
                        "hours_per_1b_tokens": round(1e9 / max(1, tp * n_params / 100e6) / 3600, 2)}
    (a.out / "grades.json").write_text(json.dumps(g, indent=1))
    print("===== grades.json =====")
    print(json.dumps(g, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

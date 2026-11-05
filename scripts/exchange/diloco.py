#!/usr/bin/env python3
"""DiLoCo across sites (compute exchange item f): several sites push ONE model. Each worker runs H local AdamW
steps on its own data stream, uploads its pseudo-gradient (global - local) to the exchange store, and worker 0 applies
the averaged pseudo-gradient with an outer Nesterov SGD step (Douillard et al. 2023: lr 0.7, momentum 0.9) and
publishes the next global model. Workers that lag can rejoin from the newest global.

    # one site of a K-site run (shards and checkpoints on the private HF repos; HF_TOKEN from Nick)
    python scripts/exchange/diloco.py worker --store hf:BouncySlime1215/rz-ckpts --run dl1 --worker 0 --workers 3 \
        --tokens SHARDS --preset 60m --rounds 40 --inner 500
    # EXP-111: DiLoCo vs single site at equal compute, simulated in one process (CPU/GPU)
    python scripts/exchange/diloco.py simulate --tokens SHARDS --preset tiny --workers 4 --rounds 6 --inner 50

Pre-registered: docs/experiments.md EXP-111. Owner: lane 1.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))

PERSONAL = "personal-use, not for commercial release"


def flat(model):
    import torch
    return torch.cat([p.detach().float().reshape(-1).cpu() for p in model.parameters()])


def load_flat(model, vec) -> None:
    import torch
    i = 0
    with torch.no_grad():
        for p in model.parameters():
            n = p.numel()
            p.copy_(vec[i:i + n].view_as(p).to(p.dtype))
            i += n


class Outer:
    """Nesterov SGD on the averaged pseudo-gradient (DiLoCo's outer optimizer)."""

    def __init__(self, n: int, lr: float = 0.7, momentum: float = 0.9):
        import torch
        self.m, self.lr, self.mu = torch.zeros(n), lr, momentum

    def step(self, theta, delta):
        self.m = self.mu * self.m + delta
        return theta - self.lr * (delta + self.mu * self.m)


def inner(model, opt, batches, start: int, steps: int, vocab: int, dev) -> float:
    import torch
    model.train()
    lo = 0.0
    for s in range(start, start + steps):
        x = torch.from_numpy(batches.get(s)).to(dev)
        out = model(x[:, :-1])
        loss = torch.nn.functional.cross_entropy(out.float().reshape(-1, vocab), x[:, 1:].reshape(-1))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        lo = float(loss)
    return lo


def valid_loss(model, vb, vocab: int, dev, n: int = 8) -> float:
    import torch
    model.eval()
    tot = 0.0
    with torch.no_grad():
        for i in range(n):
            x = torch.from_numpy(vb.get(i)).to(dev)
            tot += float(torch.nn.functional.cross_entropy(model(x[:, :-1]).float().reshape(-1, vocab),
                                                           x[:, 1:].reshape(-1)))
    return tot / n


def setup(a):
    import torch

    import nfm_train as N
    import world_model_pilot as W
    games, lic = W.load_shards(Path(a.tokens), a.leagues.split(","))
    sp = W.split(games)
    vocab = int(math.ceil(max(1 + max(int(g["tokens"].max()) for g in games if len(g["tokens"])), 64) / 128) * 128)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    train, valid = N.pack(sp["train"]), N.pack(sp["valid"])
    lic_tag = PERSONAL if any(x != "open" and not str(x).startswith("open") for x in lic) else "open"
    return N, sp, vocab, dev, train, valid, lic_tag


def simulate(a) -> dict:
    """K workers x H inner steps x R rounds vs one site with K x H x R steps (equal compute, same batch size and
    seed); each worker reads a disjoint slice of the packed train stream (sites hold different shards)."""
    import torch
    N, sp, vocab, dev, train, valid, lic = setup(a)
    cfg = N.PRESETS[a.preset]
    vb = N.Batches(valid, a.ctx, a.batch, seed=99)
    torch.manual_seed(0)
    single = N.build_model(vocab, **cfg).to(dev)
    init = flat(single)
    opt = torch.optim.AdamW(single.parameters(), lr=a.lr, betas=(0.9, 0.95), weight_decay=0.1)
    tb = N.Batches(train, a.ctx, a.batch, seed=7)
    t0 = time.time()
    curve_s = []
    for r in range(a.rounds):
        inner(single, opt, tb, r * a.workers * a.inner, a.workers * a.inner, vocab, dev)
        curve_s.append(round(valid_loss(single, vb, vocab, dev), 4))
    t_single = time.time() - t0
    # DiLoCo
    cut = np.linspace(0, len(train), a.workers + 1).astype(int)
    shards = [N.Batches(train[cut[w]:cut[w + 1]], a.ctx, a.batch, seed=100 + w) for w in range(a.workers)]
    workers = [N.build_model(vocab, **cfg).to(dev) for _ in range(a.workers)]
    opts = [torch.optim.AdamW(m.parameters(), lr=a.lr, betas=(0.9, 0.95), weight_decay=0.1) for m in workers]
    theta, outer = init.clone(), Outer(len(init), a.outer_lr, a.outer_momentum)
    curve_d = []
    t0 = time.time()
    for r in range(a.rounds):
        deltas = []
        for w, (m, o) in enumerate(zip(workers, opts)):
            load_flat(m, theta)
            inner(m, o, shards[w], r * a.inner, a.inner, vocab, dev)
            deltas.append(theta - flat(m))
        theta = outer.step(theta, torch.stack(deltas).mean(0))
        load_flat(workers[0], theta)
        curve_d.append(round(valid_loss(workers[0], vb, vocab, dev), 4))
    t_dl = time.time() - t0
    return {"exp": "EXP-111", "preset": a.preset, "workers": a.workers, "inner_steps": a.inner, "rounds": a.rounds,
            "steps_each_arm": a.workers * a.inner * a.rounds, "single_site": {"valid_curve": curve_s, "final": curve_s[-1],
                                                                              "seconds": round(t_single, 1)},
            "diloco": {"valid_curve": curve_d, "final": curve_d[-1], "seconds_serial": round(t_dl, 1),
                       "syncs": a.rounds, "outer_lr": a.outer_lr, "outer_momentum": a.outer_momentum},
            "gap_nats": round(curve_d[-1] - curve_s[-1], 4), "license": lic}


def worker(a) -> int:
    """One site of a real multi-site run. Store layout: diloco/<run>/global_r<r>.pt (+ outer state, worker 0 only),
    diloco/<run>/r<r>/w<k>.pt (pseudo-gradients)."""
    import torch

    from store import open_store
    N, sp, vocab, dev, train, valid, lic = setup(a)
    st = open_store(a.store, "model")
    cfg = N.PRESETS[a.preset]
    torch.manual_seed(0)
    model = N.build_model(vocab, **cfg).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, betas=(0.9, 0.95), weight_decay=0.1)
    cut = np.linspace(0, len(train), a.workers + 1).astype(int)
    tb = N.Batches(train[cut[a.worker]:cut[a.worker + 1]], a.ctx, a.batch, seed=100 + a.worker)
    vb = N.Batches(valid, a.ctx, a.batch, seed=99)
    tmp = Path(a.scratch)
    tmp.mkdir(parents=True, exist_ok=True)
    root = f"diloco/{a.run}"
    have = sorted(int(f.split("global_r")[1].split(".")[0]) for f in st.list(root) if "/global_r" in f)
    r0 = have[-1] if have else 0
    if not have:
        if a.worker == 0:
            torch.save(flat(model), tmp / "g.pt")
            st.put(tmp / "g.pt", f"{root}/global_r0.pt", f"{a.run} init")
        else:
            while not st.exists(f"{root}/global_r0.pt"):
                time.sleep(a.poll)
    outer = Outer(len(flat(model)), a.outer_lr, a.outer_momentum)
    if a.worker == 0 and st.get(f"{root}/outer.pt", tmp / "outer.pt"):
        outer.m = torch.load(tmp / "outer.pt")
    for r in range(r0, a.rounds):
        st.get(f"{root}/global_r{r}.pt", tmp / "g.pt")
        theta = torch.load(tmp / "g.pt")
        load_flat(model, theta)
        lo = inner(model, opt, tb, r * a.inner, a.inner, vocab, dev)
        torch.save(theta - flat(model), tmp / "d.pt")
        st.put(tmp / "d.pt", f"{root}/r{r}/w{a.worker}.pt", f"{a.run} r{r} w{a.worker} loss {lo:.4f}")
        print(f"round {r}: worker {a.worker} inner loss {lo:.4f}", flush=True)
        if a.worker == 0:
            t0 = time.time()
            while True:                                 # wait for every worker, or proceed with those in after timeout
                got = [f for f in st.list(f"{root}/r{r}/") if f.endswith(".pt")]
                if len(got) >= a.workers or time.time() - t0 > a.timeout:
                    break
                time.sleep(a.poll)
            ds = []
            for f in got:
                st.get(f, tmp / "x.pt")
                ds.append(torch.load(tmp / "x.pt"))
            theta = outer.step(theta, torch.stack(ds).mean(0))
            torch.save(theta, tmp / "g.pt")
            torch.save(outer.m, tmp / "outer.pt")
            st.put(tmp / "outer.pt", f"{root}/outer.pt", f"{a.run} outer r{r}")
            st.put(tmp / "g.pt", f"{root}/global_r{r + 1}.pt", f"{a.run} global r{r + 1} ({len(ds)} workers)")
            load_flat(model, theta)
            print(f"round {r}: {len(ds)}/{a.workers} workers, valid {valid_loss(model, vb, vocab, dev):.4f}", flush=True)
        else:
            while not st.exists(f"{root}/global_r{r + 1}.pt"):
                time.sleep(a.poll)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("simulate", "worker"))
    ap.add_argument("--tokens", required=True)
    ap.add_argument("--leagues", default="nfl,nba,mlb")
    ap.add_argument("--preset", default="tiny")
    ap.add_argument("--ctx", type=int, default=512)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=6e-4)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--worker", type=int, default=0)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--inner", type=int, default=500)
    ap.add_argument("--outer-lr", type=float, default=0.7)
    ap.add_argument("--outer-momentum", type=float, default=0.9)
    ap.add_argument("--store", default="")
    ap.add_argument("--run", default="dl1")
    ap.add_argument("--scratch", default="/tmp/rz_diloco")
    ap.add_argument("--poll", type=float, default=20.0)
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "simulate":
        g = simulate(a)
        print(json.dumps(g, indent=1))
        if a.out:
            a.out.mkdir(parents=True, exist_ok=True)
            (a.out / "grades.json").write_text(json.dumps(g, indent=1))
        return 0
    return worker(a)


if __name__ == "__main__":
    raise SystemExit(main())

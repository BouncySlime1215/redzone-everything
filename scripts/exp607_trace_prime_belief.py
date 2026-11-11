#!/usr/bin/env python3
"""[EXP-607, info only] Trace believed vs true delay for Prime targets at each switch decision (EXP-605 resync).
usage: python scripts/exp607_trace_prime_belief.py OUT.json <eval_sports args...>
Why: EXP-607, info only: traces believed vs true Prime delay at each switch (docstring)."""
import contextlib, io, json, runpy, sys
sys.path.insert(0, ".")
from redzone.lab import backtest
out, args = sys.argv[1], sys.argv[2:]
rows = []
_run = backtest.run
def run(replays, cfg, my, **kw):
    B = float(kw.get("behind_live_s") or 0.0)
    targets = kw.get("targets") or {}
    state = {}
    def on_tick(t, dec):
        state["dec"] = dec
    kw["on_tick"] = on_tick
    # wrap decide to see each switch decision
    from redzone.decider.decider import Decider
    rep = None
    orig = Decider.decide
    def decide(self, cands, *a, **k):
        d = orig(self, cands, *a, **k)
        tg = targets.get(getattr(d, "target", None) or "", "")
        if getattr(d, "action", None) == "switch" and tg.startswith("prime:"):
            bk = self.delays.get(tg)
            rows.append({"gid": d.target, "target": tg,
                         "believed_mu": round(bk.mu, 1), "behind": bk.behind, "edge_true": None, "B": B})
        return d
    Decider.decide = decide
    try:
        rep = _run(replays, cfg, my, **kw)
    finally:
        Decider.decide = orig
    lt = rep.lag_true
    for r in rows:
        if r["edge_true"] is None:
            gid = r["gid"]
            r["edge_true"] = round(backtest.lag_of(lt, gid) - B, 1)
    return rep
backtest.run = run
sys.argv = ["scripts/eval_sports.py"] + args
with contextlib.redirect_stdout(io.StringIO()):
    try:
        runpy.run_path("scripts/eval_sports.py", run_name="__main__")
    except SystemExit:
        pass
json.dump(rows, open(out, "w"))
print(len(rows), rows[:8])

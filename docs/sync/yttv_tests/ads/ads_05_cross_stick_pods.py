#!/usr/bin/env python3
"""ads_05 cross-stick pods: same channel on both sticks -> the D difference to ~0.1 s.

Hypothesis: on the same channel both sticks get the same network pod (DAI decisions may differ, but
the spot GRID is the network's). The same spot boundary therefore fires media_session 'updated' on
both sticks, offset by exactly D_150 - D_181. Recordings (11-04) show ESPN pods on .181 a steady
~60.2 s after .150 across 5 pods hours apart. If one stick's D is known (from another test or the
visible stick's calibrated sync), the other follows with ~0.1 s error.

Procedure (read-only by default): poll media_session on BOTH sticks every --poll s; detect pods per
stick; group pods by the pair of tunes; inside a group take every cross-stick boundary difference within
+-180 s and score each candidate delta by how many boundaries agree within 0.3 s (delta = wall_181 -
wall_150 of the same spot boundary). Also record
dE = E_181 - E_150 (E = wall - position) to test whether the position timeline is shared
(dE == delta) or per-tune (dE != delta).
Optional --tune-hidden: tune ONLY the hidden stick to --channel first (refused if the app says it is on
screen or if the state is unknown). Never used in dry-run.

Pass rule: some tune pair has >=3 boundaries agreeing on one delta (sd <= 0.5 s) and no grid alias
(+-10/15/30 s) scoring >= 80% as well (pods with irregular spot gaps break the alias).
"""
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_05_cross_stick_pods"


def best_delta(diffs, tol=0.3):
    """Score every candidate delta by how many boundary pairs agree within tol. Returns
    (delta_list_of_best, score, alias_scores) where aliases are the runner-up candidates >1 s away
    (15 s spot grids alias at +-15/30 s; irregular 10-12 s spots break the tie)."""
    ds = sorted(d for d, _ in diffs)
    scored = []
    for d in ds:
        near = [x for x in ds if abs(x - d) <= tol]
        scored.append((len(near), -abs(st.median(near) - d), d, near))
    scored.sort(reverse=True)
    if not scored:
        return None, 0, []
    top = scored[0]
    aliases = []
    for sc in scored[1:]:
        if abs(sc[2] - top[2]) > 1 and all(abs(sc[2] - a[0]) > 1 for a in aliases) and sc[0] > 0:
            aliases.append((round(sc[2], 2), sc[0]))
        if len(aliases) >= 3:
            break
    return top[3], top[0], aliases


def analyse(ev, logs, plays, args, out, ctx):
    h150, h181 = L.HOSTS["hdmi1"], L.HOSTS["hdmi2"]
    pods = L.find_pods(ev)
    idx, _ = L.seg_index(ev, jump=120.0)
    # every cross-stick boundary difference on the same channel within +-180 s, grouped by the pair of
    # tunes (D is constant inside a tune, so the true D_181 - D_150 recurs across that pair's pods)
    groups = {}
    for p1 in (p for p in pods if p["host"] == h150):
        for p2 in (p for p in pods if p["host"] == h181 and p["sub"] == p1["sub"]):
            if abs(p2["start"] - p1["start"]) > 180:
                continue
            k = (idx.get((h150, p1["start"])), idx.get((h181, p2["start"])), p1["sub"])
            g = groups.setdefault(k, dict(diffs=[], pods=set(), dE=[]))
            g["pods"].add(p1["start"])
            g["dE"].append(p2["E"] - p1["E"])
            # p2 side: every 181 update on that channel near the pod (single updates are spot
            # boundaries too; the 3.5 s poll can split one pod into a pod + singles)
            singles = [e["w"] for e in ev if e["host"] == h181 and e["sub"] == p1["sub"]
                       and p2["start"] - 60 <= e["w"] <= p2["end_est"] + 60]
            for t1 in p1["bounds"]:
                for t2 in sorted(set(p2["bounds"]) | set(singles)):
                    if abs(t2 - t1) <= 180:
                        g["diffs"].append((t2 - t1, t1))
    rows = []
    for k, g in groups.items():
        near, score, aliases = best_delta(g["diffs"])
        if not near:
            continue
        sd = L.robust(near)
        amb = bool(aliases) and aliases[0][1] >= max(2, 0.8 * score)
        rows.append(out.w("tune_pair", channel=k[2], first=L.hhmmss(min(g["pods"])), pods=len(g["pods"]),
                          delta=sd, score=score, aliases=aliases, ambiguous=amb,
                          dE=round(st.median(g["dE"]), 2), delta_minus_dE=round(sd["median"] - st.median(g["dE"]), 2)))
    good = [r for r in rows if r["score"] >= 3 and not r["ambiguous"] and (r["delta"]["sd"] or 0) <= 0.5]
    passed = bool(good)
    out.summary(passed, tune_pairs=len(rows), good=len(good),
                best=max(good, key=lambda r: r["score"])["delta"] if good else None,
                deltas=[(r["channel"], r["first"], r["delta"]["median"], r["score"], r["ambiguous"]) for r in rows][:10],
                note="delta = D_181 - D_150 (picture lag difference); delta != dE means position is per-tune")


def extra(p):
    p.add_argument("--tune-hidden", action="store_true",
                   help="tune the HIDDEN stick to --channel before measuring (guarded; never in dry-run)")


if __name__ == "__main__":
    if "--tune-hidden" in sys.argv and "--dry-run" not in sys.argv:
        ch = sys.argv[sys.argv.index("--channel") + 1] if "--channel" in sys.argv else None
        vis, why = L.visible_hosts()
        hidden = [h for h in L.HOSTS.values() if h not in vis]
        if not ch or ch not in L.CODES or len(hidden) != 1:
            print(f"refusing --tune-hidden: channel={ch} hidden={hidden} ({why})")
            sys.exit(2)
        ok, msg = L.guard_hidden(hidden[0])
        print(msg)
        if not ok:
            sys.exit(2)
        print(L.t1_retune.tune(hidden[0], ch) if ch in L.t1_retune.CODES else
              L.sh(hidden[0], f"am start -a android.intent.action.VIEW -d https://tv.youtube.com/watch/{L.CODES[ch]} {L.PKG}"))
    sys.exit(L.main(ID, __doc__, analyse,
                    "poll media_session on BOTH sticks (same channel), pair DAI pods by spot-gap pattern and "
                    "measure delta = boundary_181 - boundary_150 (= D_181 - D_150)", hosts_mode="both",
                    extra_args=extra))

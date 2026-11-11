"""Export briefs + hindsight targets for the game-reasoner language model (LoRA on the shared base).

    python scripts/train_reasoner.py nfl nba nhl         # first: OOF head odds (the brief shows them)
    python scripts/export_reasoner_llm.py --out data/training/reasoner_llm

Writes <out>/rows_{train,valid,test}.jsonl (one row per sampled brief, schema below) and
<out>/chat_{train,valid,test}.jsonl (mlx_lm chat format: system + brief -> target JSON).

Whole nights are split together. test = the nights the scorecards grade (NFL: scripts/eval.py's 16
slates; NBA/NHL: fold 0 of the head's 3 folds), valid = 1 in 10 of the remaining nights.

Row schema:
  id, game_id, night, split, league, i (play index), brief (exactly what the model sees live),
  target (the JSON the model should write), and the hindsight facts behind it: y, big_next,
  crunch_next, t_big, t_break, next_text, garbage, crunch, late, mine, head_p (out-of-fold).
Why: Exports briefs and hindsight targets for the reasoner LoRA on the shared base (docstring).
"""
import argparse
import gzip
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab.reasoner_data import HORIZON_S, fold_of  # noqa: E402
from redzone.reasoner.reasoner import SYSTEM  # noqa: E402


def eval_nights() -> set[str]:
    import importlib.util
    spec = importlib.util.spec_from_file_location("ev", ROOT / "scripts/eval.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    from redzone.lab.reasoner_data import nights_of
    nmap = nights_of(ROOT / "data/archive/nfl")
    out = set()
    for reps in ev.eval_set(16):
        for r in reps:
            if r.gid in nmap:
                out.add(nmap[r.gid])
    return out


def _sec(x: float) -> str:
    x = max(15, int(round(x / 15.0)) * 15)
    return f"{x // 60}:{x % 60:02d}" if x >= 60 else f"{x} s"


def storyline(r: dict) -> str:
    f = r["facts"]
    b = r["brief"].splitlines()
    head = b[0] if b else ""
    lg = r["league"]
    # "NFL PHI 7 @ WSH 10 | Q3 13:20 | live"
    try:
        teams, when, state = [x.strip() for x in head.split("|")]
        _, away, a_s, _, home, h_s = teams.split()
        a_s, h_s = int(a_s), int(h_s)
    except ValueError:
        return head[:100]
    lead = home if h_s > a_s else away if a_s > h_s else None
    d = abs(h_s - a_s)
    score = f"tied {h_s}-{a_s}" if lead is None else f"{lead} up {d}"
    bits = [score, when]
    if r["garbage"]:
        bits.append("decided")
    elif r["crunch"]:
        bits.append("crunch time")
    if lg == "nfl" and len(b) > 1 and b[1].startswith("ball "):
        poss = b[1].split()[1]
        if f.get("red_zone"):
            bits.append(f"{poss} in the red zone")
        elif f.get("two_min"):
            bits.append(f"{poss} two-minute drive")
        elif (f.get("drive_plays") or 0) >= 5:
            bits.append(f"{poss} on a long drive")
    if lg == "nhl" and f.get("pp"):
        bits.append("5-on-3" if f.get("pp_5v3") else "power play")
    if lg == "nhl" and f.get("empty_net"):
        bits.append("empty net")
    if (f.get("run_max") or 0) >= (10 if lg == "nba" else 14 if lg == "nfl" else 2):
        bits.append("on a run")
    if (f.get("lead_changes") or 0) >= 4:
        bits.append(f"{int(f['lead_changes'])} lead changes")
    return ", ".join(bits)[:110]


def target(r: dict, head_p: float) -> dict:
    lg = r["league"]
    H = HORIZON_S[lg]
    tb = r["t_big"] if r["t_big"] is not None else math.inf
    tk = r["t_break"] if r["t_break"] is not None else math.inf
    f = r["facts"]
    if r["garbage"]:
        lock = "none"
    elif r["crunch"] or (r["y"] and tb <= H) or r["crunch_next"]:
        lock = "must_see"
    elif tb <= 2 * H or (r["late"] and (f.get("abs_diff") or 99) <= {"nfl": 8, "nba": 6, "nhl": 1}.get(lg, 2)):
        lock = "watch"
    else:
        lock = "none"
    why = []
    if r["crunch"]:
        why.append("crunch time")
    if lg == "nfl" and f.get("red_zone"):
        why.append("red zone")
    if lg == "nhl" and f.get("pp"):
        why.append("power play")
    if r["mine"]:
        why.append("your team")
    if (f.get("brain_key") or 0) >= 0.5:
        why.append("brain sees a key moment coming")
    if r["garbage"]:
        why = ["garbage time: the game is decided"]
    if not why:
        why.append("close game" if (f.get("abs_diff") or 99) <= {"nfl": 8, "nba": 6, "nhl": 1}.get(lg, 2)
                   else "nothing big brewing")
    if lock != "none" and tb <= H and r["next_text"]:
        nxt = f"{r['next_text'].lower()} likely within {_sec(tb)}"
    elif tk <= 90:
        nxt = f"a break in about {_sec(tk)}"
    elif lock == "must_see":
        nxt = "crunch-time snaps every play" if r["crunch"] else "a big play could come any moment"
    else:
        nxt = "routine play for now"
    stay = 0
    if lock == "must_see":
        stay = int(min(900, max(60, tk if math.isfinite(tk) else 300)) // 15 * 15)
    elif lock == "watch":
        stay = 60
    # Ship gate (Nov 3, PR #13): "zero missed big plays first": yes only when no big play followed
    # within H in hindsight (and never in crunch time or a must_see), even if that means staying longer.
    unsafe = lock == "must_see" or r["crunch"] or (tb <= H and not r["garbage"])
    return {"lock_in": lock, "safe_to_leave": "no" if unsafe else "yes",
            "confidence": round(head_p if lock != "none" else 1 - head_p, 2),
            "storyline": storyline(r), "why": ", ".join(why[:3]), "next": nxt, "stay_s": stay}


def brief_with(r: dict, head_p: float, on_screen: bool) -> str:
    lines = r["brief"].splitlines()
    out = []
    for ln in lines:
        if ln.startswith("you: "):
            out.append(f"lock-in odds {round(100 * head_p)}%")
            if on_screen:
                ln = ln.replace("not on screen", "ON SCREEN")
        out.append(ln)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data/training/reasoner_llm"))
    ap.add_argument("--per-league", type=int, default=4000, help="sampled briefs per league (all splits)")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    test_nfl = eval_nights()
    files = {s: (open(out / f"rows_{s}.jsonl", "w"), open(out / f"chat_{s}.jsonl", "w"))
             for s in ("train", "valid", "test")}
    counts = {}
    for lg in ("nfl", "nba", "nhl"):
        oof = json.loads((ROOT / f"data/training/reasoner_{lg}_oof.json").read_text())["oof"]
        with gzip.open(ROOT / f"data/training/reasoner_{lg}.jsonl.gz", "rt") as fh:
            rows = [json.loads(x) for x in fh]
        # balance: every must_see/watch-worthy row is kept with higher odds than quiet ones
        pos = [r for r in rows if r["y"] or r["crunch"] or r["mine"]]
        neg = [r for r in rows if r not in pos] if len(rows) < 2000 else \
            [r for r in rows if not (r["y"] or r["crunch"] or r["mine"])]
        n_pos = min(len(pos), a.per_league // 2)
        pick = rng.sample(pos, n_pos) + rng.sample(neg, min(len(neg), a.per_league - n_pos))
        pick.sort(key=lambda r: (r["night"], r["gid"], r["i"]))
        for r in pick:
            n = r["night"]
            if lg == "nfl":
                split = "test" if n in test_nfl else ("valid" if fold_of(n, 10) == 1 else "train")
            else:
                split = "test" if fold_of(n, 3) == 0 else ("valid" if fold_of(n + "v", 5) == 0 else "train")
            p = float(oof.get(f"{r['gid']}:{r['i']}", 0.0))
            tgt = target(r, p)
            b = brief_with(r, p, on_screen=rng.random() < 0.3)
            row = {"id": f"{lg}:{r['gid']}:{r['i']}", "game_id": r["gid"], "night": n, "split": split,
                   "league": lg, "i": r["i"], "brief": b, "target": tgt,
                   **{k: r[k] for k in ("y", "big_next", "crunch_next", "t_big", "t_break", "next_text",
                                        "garbage", "crunch", "late", "mine")}, "head_p": round(p, 4)}
            fr, fc = files[split]
            fr.write(json.dumps(row) + "\n")
            fc.write(json.dumps({"messages": [{"role": "system", "content": SYSTEM},
                                              {"role": "user", "content": b},
                                              {"role": "assistant", "content": json.dumps(tgt)}]}) + "\n")
            counts[(lg, split)] = counts.get((lg, split), 0) + 1
    for fr, fc in files.values():
        fr.close(), fc.close()
    print(json.dumps({f"{k[0]}/{k[1]}": v for k, v in sorted(counts.items())}))


if __name__ == "__main__":
    main()

"""Gold labels: Nick's rules applied exactly, written by Claude, for lock-in and jump-or-stay.

Lock-in gold starts from the reasoner export's hindsight target (garbage never must_see; crunch or a
big play within the horizon -> must_see; close-late or a big play within 2H -> watch) and adds the
rules it leaves out:

  your team, late           -> must_see       ("your team")
  NFL red zone / 2-min drive -> must_see       ("NFL drives first"), unless garbage time
  NHL power play             -> at least watch
  garbage time               -> none, always   (wins over everything)

safe_to_leave is "no" whenever gold lock_in is must_see or a big play / crunch followed in hindsight
("never miss a big play"); stay_s is at least 60 s for must_see.

Jump-or-stay gold is agent/distill/jump.py's label (never mid-play, crunch first, NFL drives first,
your team, value of the reel).

``pick_hard`` ranks moments by how hard and how important they are, for the hand-reviewed gold set
and for the order the teacher works through its queue.
"""
from __future__ import annotations

import re

from agent.distill.jump import live_drive

LOCK_RANK = {"none": 0, "watch": 1, "must_see": 2}


def nhl_pp(brief: str) -> bool:
    return bool(re.search(r"^power play ", brief, re.M))


def gold_target(r: dict) -> tuple[dict, list[str]]:
    """(gold target, rules that changed the export's target)."""
    t = dict(r["target"])
    rules = []

    def raise_to(level, rule):
        if LOCK_RANK[level] > LOCK_RANK[t["lock_in"]]:
            t["lock_in"] = level
            rules.append(rule)

    if r.get("garbage"):
        if t["lock_in"] != "none":
            t["lock_in"] = "none"
            rules.append("garbage_never_must_see")
    else:
        if r.get("mine") and r.get("late"):
            raise_to("must_see", "your_team_late")
        if live_drive(r):
            raise_to("must_see", "nfl_drives_first")
        if r["league"] == "nhl" and nhl_pp(r["brief"]):
            raise_to("watch", "nhl_power_play")
    hindsight_risk = bool(r.get("y") or r.get("crunch_next"))
    safe = "no" if (t["lock_in"] == "must_see" or hindsight_risk) else t["safe_to_leave"]
    if safe != t["safe_to_leave"]:
        rules.append("never_miss_big")
        t["safe_to_leave"] = safe
    if t["lock_in"] == "must_see" and int(t.get("stay_s") or 0) < 60:
        t["stay_s"] = 60
    return t, rules


def hardness(r: dict, gold: dict) -> float:
    """Higher = harder and more important (teacher queue order, gold-set pick)."""
    h = 0.0
    p = float(r.get("head_p") or 0.0)
    head_lock = "must_see" if p >= 0.5 else "watch" if p >= 0.15 else "none"
    if head_lock != gold["lock_in"]:
        h += 2.0                                   # the fast head gets it wrong
    h += 1.0 - min(1.0, abs(p - 0.5) * 2)          # near the head's decision boundary
    if gold["lock_in"] == "must_see":
        h += 1.0
    if r.get("y"):
        h += 1.5                                   # a real big play followed: the costly misses live here
    if r.get("crunch") or r.get("crunch_next"):
        h += 1.0
    if r.get("mine"):
        h += 1.0
    if r.get("garbage") and r.get("late"):
        h += 0.5                                   # the "it's decided" edge
    if gold["safe_to_leave"] != r["target"]["safe_to_leave"] or gold["lock_in"] != r["target"]["lock_in"]:
        h += 1.0                                   # a rule the export left out
    return round(h, 3)


def apply(rows: list[dict]) -> dict:
    """Replace each row's target with gold in place; returns rule counts."""
    counts: dict[str, int] = {}
    for r in rows:
        g, rules = gold_target(r)
        r["export_target"] = r["target"]
        r["target"] = g
        r["hard"] = hardness(r, g)
        for k in rules:
            counts[k] = counts.get(k, 0) + 1
    return counts


def pick_hard(rows: list[dict], n: int, key=lambda r: r["hard"], cls=lambda r: r["target"]["lock_in"],
              share: dict | None = None) -> list[dict]:
    """The n hardest rows, spread over splits the way the rows are (so test nights get their share)
    and over classes by ``share`` (default: must_see 40%, watch 30%, none 30%)."""
    share = share or {"must_see": 0.4, "watch": 0.3, "none": 0.3}
    by: dict[tuple, list[dict]] = {}
    for r in rows:
        by.setdefault((r.get("split", "train"), cls(r)), []).append(r)
    split_n: dict[str, int] = {}
    for (s, _), rs in by.items():
        split_n[s] = split_n.get(s, 0) + len(rs)
    total = sum(split_n.values()) or 1
    out = []
    for (s, c), rs in by.items():
        k = max(1, round(n * split_n[s] / total * share.get(c, 0.0)))
        out += sorted(rs, key=lambda r: (-key(r), r["id"]))[:k]
    return out

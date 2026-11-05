"""Match on-screen text (captions, Key Plays rows, stats-panel changes) to feed plays with real walls.

match_text(line, t_seen, plays) -> best play (or None) using player surnames + event keywords, restricted
to plays whose wall is 5..150 s before t_seen. D_raw = t_seen - play.wall.
synth_*() build synthetic observations from a recorded plays list with a KNOWN delay, so --dry-run can
verify the estimator recovers it.
"""
from __future__ import annotations

import random
import re

STOP = {"The", "And", "With", "From", "Foul", "Turnover", "Timeout", "Shot", "Jump", "Layup", "Dunk", "Free",
        "Throw", "Three", "Point", "Pointer", "Makes", "Misses", "Missed", "Made", "Defensive", "Offensive",
        "Rebound", "Personal", "Shooting", "Steal", "Block", "Assist", "Assists", "Bad", "Pass", "Lost", "Ball",
        "Team", "Official", "End", "Start", "Period", "Quarter", "Half", "Game", "Driving", "Pullup", "Running",
        "Step", "Back", "Floating", "Tip", "Hook", "Fadeaway", "Turnaround", "Alley", "Oop", "Cutting", "Putback",
        "Violation", "Substitution", "Enters", "For", "Technical", "Flagrant", "Kicked", "Out", "Bounds"}
KEYWORDS = {
    "timeout": ("timeout", "time out", "time-out"),
    "foul": ("foul",),
    "three": ("three", "3-pt", "3pt", "three point", "from downtown", "from deep", "triple"),
    "dunk": ("dunk", "slam", "jam"),
    "free throw": ("free throw", "line", "charity stripe"),
    "steal": ("steal", "picked off", "stolen"),
    "block": ("block", "rejected", "swat", "denied"),
    "turnover": ("turnover", "travel", "traveling", "out of bounds", "giveaway"),
    "layup": ("layup", "lay-up", "finger roll"),
    "rebound": ("rebound", "board"),
    "challenge": ("challenge", "review"),
    "jumper": ("jumper", "jump shot", "pull-up", "pullup"),
}
WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")


def play_tokens(p: dict) -> tuple[set[str], set[str]]:
    txt = f"{p.get('text', '')} {p.get('type', '')}"
    names = {w.lower() for w in WORD.findall(p.get("text", "")) if w[0].isupper() and len(w) >= 4 and w not in STOP}
    low = txt.lower()
    kws = {k for k, alts in KEYWORDS.items() if any(a in low for a in alts)}
    if "3-pt" in low or "three point" in low or "3pt" in low:
        kws.add("three")
    return names, kws


def line_tokens(line: str) -> tuple[set[str], set[str]]:
    low = line.lower()
    words = {w.lower().strip("'") for w in WORD.findall(line)}
    kws = {k for k, alts in KEYWORDS.items() if any(a in low for a in alts)}
    return words, kws


def match_text(line: str, t_seen: float, plays: list[dict], lo: float = 5.0, hi: float = 150.0,
               min_score: float = 2.0) -> tuple[dict | None, float]:
    """Best play for a text line. The same event logged by two feeds (nba_cdn + espn_summary, walls a few
    s apart) is one candidate: ties within 3 s collapse, preferring nba_cdn (exact timeActual)."""
    words, kws = line_tokens(line)
    cands = []
    for p in plays:
        d = t_seen - p["wall"]
        if not (lo <= d <= hi):
            continue
        names, pk = play_tokens(p)
        sc = 2.0 * len(names & words) + 1.0 * len(pk & kws)
        if sc > 0:
            cands.append((sc, p))
    if not cands:
        return None, 0.0
    top = max(c[0] for c in cands)
    best = [p for sc, p in cands if sc == top]
    anchor = min(best, key=lambda p: (p.get("source") != "nba_cdn", p["wall"]))
    if any(abs(p["wall"] - anchor["wall"]) > 3.0 for p in best):
        return None, top                       # two different events tie
    rivals = [sc for sc, p in cands if abs(p["wall"] - anchor["wall"]) > 3.0]
    if top < min_score or (rivals and top - max(rivals) < 1.0):
        return None, top
    return anchor, top


def caption_for(p: dict) -> str:
    """What a captioner might type for a play (uppercase, partial)."""
    names, kws = play_tokens(p)
    bits = [n.upper() for n in sorted(names)[:1]]
    bits += [random.choice(KEYWORDS[k]).upper() for k in sorted(kws)[:1]]
    return (" ".join(bits) + " ON THE PLAY").strip()


def synth_captions(plays: list[dict], D: float, cc_lag: float, noise: float, seed: int = 7):
    random.seed(seed)
    obs = []
    for p in plays:
        if random.random() < 0.35:
            obs.append(dict(t=p["wall"] + D + cc_lag + random.gauss(0, noise), text=caption_for(p), truth=p["play"]))
    # distractor chatter
    t0, t1 = plays[0]["wall"], plays[-1]["wall"]
    for _ in range(len(obs)):
        obs.append(dict(t=random.uniform(t0, t1) + D, text=random.choice(
            ["WHAT A NIGHT HERE", "BACK TO YOU", "HE HAS BEEN GREAT", "LOOK AT THAT CROWD"]), truth=None))
    return sorted(obs, key=lambda o: o["t"])

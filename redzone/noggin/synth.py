"""[Data division E] Synthetic and teacher tokens for the NFM, with the 3x-real cap enforced in code.

Serves: the world model's data scale (EXP-097: synthetic <= ~3x real, counted apart from real) and the Teacher's
per-play labels (EXP-086 answers landed as tokens). Owner: Data planner E (EXP-1400..1499); lane 1 reads the shards.

Three products, all tagged synthetic and "personal-use, not for commercial release":

* **Mirror** (`mirror_game`): home <-> away on a vocab v1 game: the HOME/AWAY team tokens swap, MARGIN flips sign,
  PTS moves to the other side. KIND, CLOCK, DT and STOP are side-free in v1, so they are unchanged. Known bias:
  it erases home advantage on the mirrored copy (EXP-1401 measures whether that hurts on real nights).
* **Tempo re-time** (`retime_game`): one factor per game in [1 - j, 1 + j] scales the wall time of LIVE gaps only
  (the gap after an event whose STOP is "none"); the gap across a stoppage (break length, what EXP-022 and the
  timing heads learn) is left exactly as recorded, and so are CLOCK, score and kinds.
* **Teacher tokens** (`teacher_tokens`): the EXP-086 Teacher's JSON answer for one moment as 6 ids in a SEPARATE
  vocab (`TEACHER_VOCAB`), keyed by (league, game id, moment). This is a side channel: train it as an auxiliary
  target, never feed it as context. Grounded answers were written with hindsight (they saw what came next), so
  as context they would leak the future; the MODE token says which kind each one is. E4's causal per-play
  commentary (`agent/kaggle/commentary_queue.py`, mode 2) saw only the brief at that play, so it may be context.

Only nights before `train_before` are ever augmented, so no valid, test, scorecard or locked night gets a copy.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

import numpy as np

from redzone.contracts import events as V

LICENSE = "personal-use, not for commercial release"
CAP_X = 3.0                                # synthetic tokens <= CAP_X x real tokens, per league (EXP-097)
HEADER = 3                                 # BOS, HOME, AWAY
PTS_HOME = range(1, 8)                     # PTS0 + 1..7 = home scored k; PTS0 + 9..15 = away scored k (8 + k)


# ----------------------------------------------------------------------------------------------- augmentations
def _events(t: np.ndarray) -> np.ndarray:
    n = (len(t) - HEADER - 1) // V.TOKENS_PER_EVENT
    return t[HEADER:HEADER + n * V.TOKENS_PER_EVENT].reshape(n, V.TOKENS_PER_EVENT)


def mirror_game(t: np.ndarray) -> np.ndarray:
    """Home <-> away on one tokenized game (BOS HOME AWAY, 6 per event, EOS). Applying it twice is the identity."""
    out = np.array(t, dtype=np.uint16, copy=True)
    out[1], out[2] = t[2], t[1]
    ev = _events(out)
    m = ev[:, 3].astype(np.int32)
    ev[:, 3] = (2 * (V.MARGIN0 + V.MARGIN_CAP) - m).astype(np.uint16)
    p = ev[:, 4].astype(np.int32) - V.PTS0
    p = np.where((p >= 1) & (p <= 7), p + 8, np.where((p >= 9) & (p <= 15), p - 8, p))
    ev[:, 4] = (V.PTS0 + p).astype(np.uint16)
    return out


def _dt_bounds(b: int) -> tuple[float, float]:
    """Wall seconds [lo, hi) that bucket b (0..31) covers under events.dt_token."""
    lo = 2 ** (b / 2.6) - 1.0
    hi = 2 ** ((b + 1) / 2.6) - 1.0 if b < V.DT_BUCKETS - 1 else lo * 1.3 + 1.0
    return max(0.0, lo), hi


def event_starts(t: np.ndarray) -> np.ndarray:
    """Positions of every event's KIND id. In v1 and v2 alike an event opens with the 6 v1 ids (KIND DT CLOCK
    MARGIN PTS STOP); v2's (FIELD VALUE) pairs and pregame context use ids >= the v1 vocab, so a KIND id can only
    be an event start."""
    a = np.asarray(t)
    idx = np.nonzero((a >= V.KIND0) & (a < V.DT0))[0]
    return idx[idx + 5 < len(a)]


def retime_game(t: np.ndarray, rng: np.random.Generator, jitter: float = 0.10) -> np.ndarray:
    """Scale live gaps by one per-game factor; gaps that follow a stoppage keep their recorded bucket. Works on
    vocab v1 and v2 games (only the DT id of each event changes)."""
    out = np.array(t, dtype=np.uint16, copy=True)
    starts = event_starts(out)
    if not len(starts):
        return out
    f = float(rng.uniform(1.0 - jitter, 1.0 + jitter))
    live_stop = V.STOP0 + V.STOPS.index("none")
    stops = out[starts + 5]
    prev_stop = np.concatenate([[live_stop], stops[:-1]])        # the game's first gap counts as live
    for p in starts[prev_stop == live_stop]:
        d = int(out[p + 1])
        if d == V.UNK_DT or not (V.DT0 <= d < V.DT0 + V.DT_BUCKETS):
            continue
        lo, hi = _dt_bounds(d - V.DT0)
        out[p + 1] = V.dt_token(rng.uniform(lo, hi) * f)
    return out


# ------------------------------------------------------------------------------------------- teacher channel
TEACHER_VOCAB_VERSION = "teacher-v1"
LOCKS = ("none", "watch", "must_see")
CONF_BUCKETS, STAY_BUCKETS = 10, 16
T_PAD, T_BOS, T_EOS = 0, 1, 2
T_MODE0 = 3                                 # 0 grounded (hindsight: aux target only), 1 blind, 2 causal commentary
T_LOCK0 = T_MODE0 + 3
T_SAFE0 = T_LOCK0 + len(LOCKS) + 1           # yes, no, missing
T_CONF0 = T_SAFE0 + 3
T_STAY0 = T_CONF0 + CONF_BUCKETS + 1         # +1 = missing
T_LEAGUE0 = T_STAY0 + STAY_BUCKETS + 1
NEXTS = ("score", "turnover", "stoppage", "big_play", "routine")
IMP_BUCKETS = 11                             # importance 0..10
T_IMP0 = T_LEAGUE0 + len(V.BOS_LEAGUES)
T_NEXT0 = T_IMP0 + IMP_BUCKETS + 1           # +1 = missing
TEACHER_VOCAB = T_NEXT0 + len(NEXTS) + 1
TEACHER_TOKENS = 6                           # reasoner: LEAGUE MODE LOCK SAFE CONF STAY; commentary: LEAGUE MODE LOCK IMP NEXT CONF
_ID = re.compile(r"^(?P<lg>[a-z]+):(?P<gid>[^:|]+):(?P<m>[^|]+)\|?(?P<mode>.*)$")


def _json(text: str) -> dict | None:
    """The first {...} object in a Teacher answer (it may wrap it in prose or <think>)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    i = text.find("{")
    while i != -1:
        depth = 0
        for j in range(i, len(text)):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                try:
                    v = json.loads(text[i:j + 1])
                    return v if isinstance(v, dict) else None
                except ValueError:
                    break
        i = text.find("{", i + 1)
    return None


def stay_bucket(s: float) -> int:
    return min(STAY_BUCKETS - 1, int(math.log2(1.0 + max(0.0, s)) * 1.6))    # 0 .. 900 s


@dataclass
class TeacherRow:
    league: str
    game_id: str
    moment: str
    hindsight: bool
    tokens: tuple[int, ...]
    text: dict = field(default_factory=dict)        # storyline / why / next, for the Mind (never tokenized)


def informative(r: TeacherRow) -> bool:
    """False when every labelled slot is "missing" (e.g. EXP-086 grounded answers carry only storyline / why /
    next): such a row is text for the Mind, and its 6 ids would be filler that inflates the synthetic count."""
    missing = {T_LOCK0 + len(LOCKS), T_SAFE0 + 2, T_CONF0 + CONF_BUCKETS, T_STAY0 + STAY_BUCKETS,
               T_IMP0 + IMP_BUCKETS, T_NEXT0 + len(NEXTS)}
    return any(t not in missing for t in r.tokens[2:])


def teacher_tokens(answer: dict) -> TeacherRow | None:
    """One teacher_out.jsonl row -> its 6 teacher-v1 ids, or None if its id or JSON can't be read."""
    m = _ID.match(str(answer.get("id", "")))
    if not m or m["lg"] not in V.BOS_LEAGUES or m["mode"] not in ("blind", "grounded", "commentary", ""):
        return None                                 # jump answers etc. have another schema
    j = _json(answer.get("text") or "")
    if j is None:
        return None
    blind = m["mode"] == "blind" or answer.get("task") == "reasoner_blind"
    causal = m["mode"] == "commentary"
    lock = str(j.get("lock_in", "")).strip().lower()
    safe = str(j.get("safe_to_leave", "")).strip().lower()
    try:
        conf = float(j.get("confidence"))
        conf_t = T_CONF0 + min(CONF_BUCKETS - 1, int(max(0.0, min(1.0, conf)) * CONF_BUCKETS))
    except (TypeError, ValueError):
        conf_t = T_CONF0 + CONF_BUCKETS
    try:
        stay_t = T_STAY0 + stay_bucket(float(j.get("stay_s")))
    except (TypeError, ValueError):
        stay_t = T_STAY0 + STAY_BUCKETS
    league_t, lock_t = T_LEAGUE0 + V.BOS_LEAGUES.index(m["lg"]), T_LOCK0 + (LOCKS.index(lock) if lock in LOCKS
                                                                             else len(LOCKS))
    if causal:                                  # E4: saw only the plays up to this one, so usable as context
        try:
            imp_t = T_IMP0 + int(max(0, min(10, round(float(j.get("importance"))))))
        except (TypeError, ValueError):
            imp_t = T_IMP0 + IMP_BUCKETS
        nxt = str(j.get("next", "")).strip().lower()
        toks = (league_t, T_MODE0 + 2, lock_t, imp_t, T_NEXT0 + (NEXTS.index(nxt) if nxt in NEXTS else len(NEXTS)),
                conf_t)
        text = {"commentary": str(j.get("commentary", ""))[:200]}
        return TeacherRow(m["lg"], m["gid"], m["m"], False, toks, text)
    toks = (league_t, T_MODE0 + (1 if blind else 0), lock_t, T_SAFE0 + {"yes": 0, "no": 1}.get(safe, 2), conf_t,
            stay_t)
    text = {k: str(j[k])[:200] for k in ("storyline", "why", "next") if k in j}
    return TeacherRow(m["lg"], m["gid"], m["m"], not blind, toks, text)


# ----------------------------------------------------------------------------------------------------- the cap
def budget(real_tokens: int, synthetic_tokens: int, cap_x: float = CAP_X) -> int:
    """Synthetic tokens still allowed for a league (never negative)."""
    return max(0, int(cap_x * real_tokens) - int(synthetic_tokens))


def check_cap(per_league: dict, cap_x: float = CAP_X) -> list[str]:
    """Leagues whose synthetic : real ratio is over the cap (the EXP-1400 ledger bar and CI check)."""
    bad = []
    for lg, e in sorted(per_league.items()):
        real, syn = int(e.get("real_tokens") or 0), int(e.get("synthetic_tokens") or 0)
        if syn > cap_x * real:
            bad.append(f"{lg}: synthetic {syn:,} > {cap_x:g} x real {real:,}")
    return bad

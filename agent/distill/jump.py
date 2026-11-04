"""Jump-to-highlights vs stay: moments, context and hindsight labels from the archive.

One row per DEAD-BALL moment in the reasoner export (timeouts, reviews, injuries, score breaks, end
of period...). The context is what the live app would know at that moment:

  * the break type and its CAUTIOUS predicted length: the 10th percentile of that league's real
    break lengths of that type on TRAIN nights (a stand-in for Brain v3's p10 until it lands);
  * the catch-up reel: big plays from OTHER games in the last 15 minutes (real ones, from the
    archive's wall clocks), each with importance and freshness;
  * switch costs (data/eval_config.yaml: YouTube TV 5.5 s each way) and a 5 s margin.

The label is Nick's judgment written as rules, applied with hindsight (``label``):

  STAY  never mid-play: the REAL break was shorter than reel + both switches + margin (we would
        have come back after the restart), or the cautious prediction didn't cover a 30 s reel
  STAY  crunch first: crunch time, or the viewer's team late, or the reasoner's lock_in says must_see
  STAY  NFL drives first: an NFL team inside the 20 or on a two-minute drive that resumes at the
        restart (not after a score, not at the end of a half, not in garbage time)
  STAY  a big play came within 30 s of the restart (too close to risk a late return)
  STAY  not worth it: fewer than 3 unseen clips, or reel value (importance x freshness) under 1.0
  JUMP  otherwise; reel length = the window minus switches and margin, cut to 30-60 s

Rows whose label sits near a boundary (window within 15 s of the need, crunch or must_see with a
long break, a big play right after the restart, the viewer's team) are the HARD set: hand-reviewed,
held out by night like everything else, and up-weighted as a top-up when training.
"""
from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from pathlib import Path

SWITCH_S = 5.5          # data/eval_config.yaml decider.switch_cost.youtubetv (each way)
MARGIN_S = 5.0
REEL_MIN_S, REEL_MAX_S = 30.0, 60.0
CLIP_S = 9.0            # an average highlight clip
LOOKBACK_S = 900.0      # unseen big plays from the last 15 min
MIN_CLIPS, MIN_VALUE = 3, 1.0
NEED_S = REEL_MIN_S + 2 * SWITCH_S + MARGIN_S          # 46 s: the shortest window a reel fits in
DEAD = ("timeout", "two_minute_warning", "end_period", "halftime", "intermission", "review", "injury",
        "score_break")
HUMAN = {"timeout": "timeout", "two_minute_warning": "two-minute warning", "end_period": "end of period",
         "halftime": "halftime", "intermission": "intermission", "review": "replay review",
         "injury": "injury stoppage", "score_break": "break after a score"}


def importance(league: str, text: str) -> float:
    t = text.lower()
    if "touchdown" in t or t == "goal" or "safety" in t:
        return 1.0
    if "interception" in t or "fumble" in t or "turnover" in t:
        return 0.8
    if league == "nfl":
        return 0.6           # explosive play (20+ yards)
    return 0.5


def state_of(brief: str) -> str:
    head = brief.splitlines()[0] if brief else ""
    parts = [p.strip() for p in head.split("|")]
    return parts[2] if len(parts) >= 3 else "live"


def nfl_drive(brief: str) -> str | None:
    """'red zone' / 'two-minute drive' when an NFL team is threatening, from the brief alone."""
    m = re.search(r"(\d+) yds to goal", brief)
    if m and int(m.group(1)) <= 20:
        return "red zone"
    m = re.search(r"\| Q([24]) (\d+):(\d+)", brief)
    if m and int(m.group(2)) < 2 and re.search(r"^ball ", brief, re.M):
        return "two-minute drive"
    return None


def live_drive(r: dict) -> str | None:
    """NFL red zone / two-minute drive that resumes at the next snap: not after a score (kickoff
    next), not at the end of a half (the Q2/Q4 clock that just ran out), not in garbage time."""
    if r.get("league") != "nfl" or r.get("garbage"):
        return None
    typ = state_of(r["brief"])
    if typ in ("halftime", "score_break") or (typ == "end_period" and re.search(r"\| Q[24] ", r["brief"])):
        return None
    return nfl_drive(r["brief"])


# ------------------------------------------------------------------------------------ archive

def game_clock(archive: Path, leagues=("nfl", "nba", "nhl")) -> dict:
    """{(league, game_id): [(abs wall s, play text type, big?) per play index]} from ESPN summaries."""
    from redzone.lab.sportsdata import _espn_big
    from redzone.models import League
    from redzone.sources import espn
    out = {}
    for lg in leagues:
        L = League(lg)
        for f in sorted((archive / lg).glob("summary_*.json")):
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            plays = espn.flatten_plays(L, data)
            seq = []
            last = None
            for p in plays:
                t = espn._ts(p.get("wallclock"))
                last = t if t is not None else last
                typ = (espn._get(p, "type", "text") or "")
                big = _espn_big(L, typ, p) or (lg == "nba" and bool(p.get("scoringPlay")) and p.get("scoreValue") == 3)
                seq.append((last, typ, big))
            out[(lg, f.stem.split("_")[-1])] = seq
    return out


def big_index(clock: dict) -> tuple[list[float], list[tuple]]:
    """All big plays across games, sorted by absolute wall time."""
    ev = sorted((t, lg, gid, typ) for (lg, gid), seq in clock.items() for t, typ, big in seq if big and t)
    return [e[0] for e in ev], ev


def window_s(clock: dict, lg: str, gid: str, i: int, rows_by_game: dict) -> float | None:
    """Real dead time left: wall seconds from this moment to the next LIVE moment of the same game.

    Uses the export's own states when the next rows are there, else the next play whose type isn't a
    stoppage in the archive.
    """
    seq = clock.get((lg, gid))
    if not seq or i >= len(seq) or seq[i][0] is None:
        return None
    t0 = seq[i][0]
    nxt = rows_by_game.get((lg, gid), {})
    for j in range(i + 1, min(len(seq), i + 40)):
        st = nxt.get(j)
        if st is not None and st != "live":
            continue
        if st is None and re.search(r"timeout|end of|review|injur|stoppage|challenge|official", seq[j][1], re.I):
            continue
        if seq[j][0] is not None:
            return max(0.0, seq[j][0] - t0)
    return None


# ------------------------------------------------------------------------------------ rows

def p10_by_type(samples: list[tuple[str, str, float]]) -> dict:
    """{(league, break type): cautious length} = 10th percentile of real windows (TRAIN nights only)."""
    by: dict = {}
    for lg, typ, w in samples:
        by.setdefault((lg, typ), []).append(w)
    out = {}
    for k, ws in by.items():
        ws = sorted(ws)
        out[k] = round(ws[max(0, int(0.1 * len(ws)) - 1)] if len(ws) >= 5 else 0.0, 1)
    return out


def clips_for(lg: str, gid: str, t: float, times: list[float], ev: list[tuple]) -> list[dict]:
    lo, hi = bisect_left(times, t - LOOKBACK_S), bisect_right(times, t)
    out = []
    for tt, l2, g2, typ in ev[lo:hi]:
        if g2 == gid and l2 == lg:
            continue
        age = t - tt
        out.append({"league": l2, "game": g2, "what": typ, "age_s": int(age),
                    "importance": importance(l2, typ), "freshness": round(1 - age / LOOKBACK_S, 2)})
    return out[-8:]


def context_text(typ: str, p10: float, clips: list[dict]) -> str:
    lines = [f"now: {HUMAN.get(typ, typ)}; cautious remaining length (p10) {int(p10)} s",
             f"switch: {SWITCH_S} s each way, margin {int(MARGIN_S)} s; return at the restart",
             f"unseen big plays elsewhere: {len(clips)}"]
    for c in clips:
        lines.append(f"- {c['league'].upper()} {c['what']} {c['age_s'] // 60}:{c['age_s'] % 60:02d} ago "
                     f"(importance {c['importance']}, freshness {c['freshness']})")
    return "\n".join(lines)


def label(r: dict, typ: str, window: float, p10: float, clips: list[dict]) -> dict:
    """Nick's judgment, applied with hindsight. Returns {decision, reel_s, why, rule}."""
    def stay(rule, why):
        return {"decision": "stay", "reel_s": 0, "why": why, "rule": rule}
    lock = r["target"]["lock_in"]
    if p10 < NEED_S:
        return stay("too_short_predicted", f"a {HUMAN.get(typ, typ)} usually isn't long enough for a reel and two switches")
    if window < NEED_S:
        return stay("never_mid_play", "this break ends too soon: we would come back after the restart")
    if r.get("crunch"):
        return stay("crunch_first", "crunch time: stay for the restart")
    if r.get("mine") and r.get("late"):
        return stay("your_team_late", "your team, late in the game: stay")
    if lock == "must_see":
        return stay("must_see", "something big is building here: stay for the restart")
    drive = live_drive(r)
    if drive:
        return stay("nfl_drive_first", f"NFL {drive}: stay for the next snap")
    if r.get("t_big") is not None and r.get("big_next") and float(r["t_big"]) <= window + 30:
        return stay("big_right_after", "a big play is likely right after the restart")
    value = sum(c["importance"] * c["freshness"] for c in clips)
    if len(clips) < MIN_CLIPS or value < MIN_VALUE:
        return stay("not_worth_it", "not enough unseen big plays elsewhere to make a reel worth it")
    fit = min(p10, window) - 2 * SWITCH_S - MARGIN_S
    reel = max(REEL_MIN_S, min(REEL_MAX_S, fit, CLIP_S * len(clips)))
    return {"decision": "jump", "reel_s": int(reel), "rule": "jump",
            "why": f"{len(clips)} unseen big plays fit in this {HUMAN.get(typ, typ)}; back at the restart"}


def hard(r: dict, j: dict) -> bool:
    """Near a boundary: the cases a teacher (and the student) most often get wrong.

    * the break was predicted long enough, but the REAL break only just fit or just didn't (+-15 s);
    * a reel would have fit, and only a judgment rule says stay (crunch, must_see, your team late,
      NFL drive, a big play right after the restart);
    * a jump that barely clears the value bar (3-4 clips or value under 1.5);
    * the viewer's own team.
    """
    lab, w, p10 = j["label"], j["window_s"], j["p10_s"]
    if r.get("mine"):
        return True
    if p10 < NEED_S:
        return False
    if abs(w - NEED_S) <= 15:
        return True
    if lab["rule"] in ("crunch_first", "must_see", "your_team_late", "nfl_drive_first", "big_right_after"):
        return True
    value = sum(c["importance"] * c["freshness"] for c in j["clips"])
    return lab["decision"] == "jump" and (len(j["clips"]) <= 4 or value < 1.5)


def build(rows: list[dict], archive: Path) -> list[dict]:
    """Dead-ball rows of the export with a ``jump`` context and hindsight ``label``.

    rows: the reasoner export rows of every split (train p10s are learned from train rows only).
    """
    clock = game_clock(archive)
    times, ev = big_index(clock)
    states = {}
    for r in rows:
        lg, gid, i = r["id"].split(":")
        states.setdefault((lg, gid), {})[int(i)] = state_of(r["brief"])
    cand = []
    for r in rows:
        typ = state_of(r["brief"])
        if typ not in DEAD:
            continue
        lg, gid, i = r["id"].split(":")
        w = window_s(clock, lg, gid, int(i), states)
        t = clock.get((lg, gid), [(None,)] * (int(i) + 1))[int(i)][0] if int(i) < len(clock.get((lg, gid), [])) else None
        # w < 1 s: ESPN logged the break and the next play at the same wall second (common in NBA
        # feeds), so the real length is unknown, not zero. Such rows can't be labeled honestly.
        if w is None or t is None or w < 1.0 or w > 1800:
            continue
        cand.append((r, typ, w, t))
    p10 = p10_by_type([(r["league"], typ, w) for r, typ, w, _ in cand if r.get("split") == "train"])
    out = []
    for r, typ, w, t in cand:
        lg, gid, _ = r["id"].split(":")
        pp = p10.get((lg, typ), 0.0)
        clips = clips_for(lg, gid, t, times, ev)
        j = {"type": typ, "p10_s": pp, "window_s": round(w, 1), "clips": clips,
             "text": context_text(typ, pp, clips)}
        j["label"] = label(r, typ, w, pp, clips)
        j["hard"] = hard(r, j)
        out.append({**r, "jump": j})
    return out


# ------------------------------------------------------------------------------------ grading

def grade(rows: list[dict], answers: dict[str, dict | None]) -> dict:
    """Jump decisions vs hindsight on the given rows (Nick's scorecard lines for the reel).

    reels_in_dead_time_pct    of the jumps made, the reel + switches fit the REAL break (target 100)
    big_missed_on_reel        jumps where a big play came before we were back (target 0)
    agree_pct                 same decision as the hindsight label
    jump_recall_pct           of the moments where a reel was right, how many it took
    """
    jumps = fit = missed = agree = 0
    right_jumps = took = 0
    n = 0
    for r in rows:
        a = answers.get(r["id"])
        if a is None:
            continue
        n += 1
        lab = r["jump"]["label"]
        w = r["jump"]["window_s"]
        agree += a["decision"] == lab["decision"]
        right_jumps += lab["decision"] == "jump"
        if a["decision"] == "jump":
            jumps += 1
            took += lab["decision"] == "jump"
            need = max(REEL_MIN_S, a.get("reel_s") or 0) + 2 * SWITCH_S
            fit += need <= w
            missed += bool(r.get("big_next") and r.get("t_big") is not None and float(r["t_big"]) < need)
    pct = lambda a, b: round(100.0 * a / b, 1) if b else None  # noqa: E731
    return {"n": n, "jumps": jumps, "reels_in_dead_time_pct": pct(fit, jumps), "big_missed_on_reel": missed,
            "agree_pct": pct(agree, n), "jump_recall_pct": pct(took, right_jumps)}

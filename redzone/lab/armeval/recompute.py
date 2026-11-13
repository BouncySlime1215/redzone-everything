"""Recount a verdict's per-night numbers from its raw logs, without running the show (the verifier's check, F-04 item 5).

    problems = recheck(verdict, raw_by_night, replays_by_night)     # [] when every number is reproduced

What is recounted, and from what:
    Rule #1     every logged cut becomes one switch-log row (as park_replay.graded builds it) and the FROZEN
                cutgrade.grade scores it again on the archive's recorded games (their sha256 must match the log);
                high-leverage mid-play leaves with the frozen grading.dead_at + leverage.high_leverage
    pace        jumps and flips counted from the log, jumps a minute over the logged span, warranted share
    freshness   worst / mean unseen and worst unseen live play, from the logged screen timeline and each game's
                logged live stretches (the seen_card rule, re-implemented here)
    value, big  summed from the logged graded items that were shown (a recount of the log, not a re-derivation)
Why: V2-PLAN 8.2 / 8.5: a number counts only when an independent check reproduces it from raw logs.
"""
from __future__ import annotations

from redzone.lab.armeval.episode import hl_leaves_mid_play


def rule1_from_cuts(rs: list, base: int, cuts: list) -> dict:
    from redzone.grading import SwitchLog
    from redzone.lab import cutgrade
    log = [SwitchLog(base + c["join_s"], rs[c["from"]].gid, rs[c["to"]].gid, "jump", "park replay",
                     leave_dt=c["leave_s"] - c["join_s"]) for c in cuts]
    card = cutgrade.grade(rs, log, 0.0, 0.0)
    tup = [(c["from"], c["leave_s"], c["to"], c["join_s"]) for c in cuts]
    return {"joins_mid_play": card.entries_mid_play, "joins_mid_play_strict": card.entries_mid_play_strict,
            "leaves_mid_play": card.leaves - card.leaves_in_dead - card.leaves_between_plays,
            "hl_leaves_mid_play": hl_leaves_mid_play(rs, base, tup), "joins": card.entries, "leaves": card.leaves}


def freshness_from_log(raw: dict) -> dict:
    h = raw["header"]
    a, b = h["span"]
    on_at = {}
    for t0, t1, g in raw["screen"]:
        for t in range(t0, t1 + 1):
            on_at[t] = g
    gs = h["games"]
    live = []
    for x in gs:
        s = set()
        for t0, t1 in x["live_runs"]:
            s.update(range(t0, t1 + 1))
        live.append(s)
    last = [max(a, x["a0"]) for x in gs]
    worst, run, worst_live, total, n = [0] * len(gs), [0] * len(gs), [0] * len(gs), 0, 0
    for t in range(a, b):
        for g, x in enumerate(gs):
            if not (x["a0"] <= t <= x["a1"]):
                continue
            if on_at.get(t) == g:
                last[g], run[g] = t, 0
            elif t in live[g]:
                run[g] += 1
                worst_live[g] = max(worst_live[g], run[g])
            worst[g] = max(worst[g], t - last[g])
            total, n = total + t - last[g], n + 1
    return {"worst_unseen_s": max(worst, default=0), "mean_unseen_s": round(total / n, 1) if n else 0.0,
            "worst_unseen_live_s": max(worst_live, default=0)}


def card_from_log(raw: dict, rs: list) -> dict:
    """The recountable part of a night's card, from its raw log and the recorded games."""
    h = raw["header"]
    a, b = h["span"]
    mins = max(1, b - a) / 60.0
    jumps, ws = len(raw["jumps"]), raw["warrants"]
    wok = sum(1 for w in ws if w)
    big = {(x["g"], i) for x in h["games"] for i in x["big"]}
    seen_big = sum(1 for g, ti, _m, _v in raw["shown"] if (g, ti) in big)
    pts = sum(v for _g, _t, _m, v in raw["shown"])
    v2t = h["v2_total"]
    return {
        "rule1": rule1_from_cuts(rs, h["base"], raw["cuts"]),
        "pace": {"jumps": jumps, "flips": len(raw["cuts"]), "jumps_per_min": round(jumps / mins, 3),
                 "warrant_n": len(ws), "warranted": wok, "warranted_share": round(wok / len(ws), 4) if ws else None},
        "freshness": freshness_from_log(raw),
        "big": {"seen_live": seen_big, "total": len(big), "must_sees_missed": len(big) - seen_big},
        "value": {"live_points": round(pts, 3), "live_share": round(pts / v2t, 4) if v2t else None},
    }


def compare(card: dict, again: dict, night: str) -> list:
    out = []
    for sec, vals in again.items():
        for k, v in vals.items():
            got = (card.get(sec) or {}).get(k)
            if got != v:
                out.append(f"{night}: {sec}.{k} verdict {got!r}, recounted {v!r}")
    return out


def check_files(raw: dict, files: dict) -> list:
    """The archive files behind a log must be the very bytes it was scored on (files: name -> path)."""
    from redzone.lab.fence import file_sha256
    out = []
    for x in raw["header"]["games"]:
        p = files.get(x["file"])
        if p is None:
            out.append(f"{raw['header']['night']}: {x['file']} not given")
        elif x["sha256"] is not None and file_sha256(p) != x["sha256"]:
            out.append(f"{raw['header']['night']}: {x['file']} changed since the run (sha256 differs)")
    return out


def recheck(verdict: dict, raws: dict, games: dict, files: dict | None = None) -> list:
    """Every per-night number the recount can reach, against the verdict. raws: night -> raw log;
    games: night -> the recorded games in slate order; files: night -> {file name: path} (sha256 check)."""
    out = []
    for card in verdict["per_night"]:
        n = card["night"]
        if n not in raws:
            out.append(f"{n}: no raw log")
            continue
        raw = raws[n]
        if raw["header"]["decisions_sha256"] != card["decisions_sha256"]:
            out.append(f"{n}: raw log is from another run (decisions sha256 differs)")
        if files is not None:
            out += check_files(raw, files.get(n, {}))
        out += compare(card, card_from_log(raw, games[n]), n)
    return out

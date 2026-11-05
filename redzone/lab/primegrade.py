"""[EXP-800 Prime] Grade the diamond plan's Prime tests (docs/sync/2026-11-05-diamond-plan.md, 7f32aaa6) on the
sticks' own media_session rows. Bars are the plan's, pre-registered here before any TNF row exists.

Rows: syncgrade's probe rows ({t, stick, app, channel, uptime_ms, ms: {state, position, updated, speed}, tune_t}),
plus `seek_t` (wall of the stick's last seek command, None = never) when the probe logs it. A1 measured the sticks
on the Mac's time axis to 0.06-0.09 s, so `t` is one clock for both sticks.

Between two media_session anchors E = wall - pos_now/1000 is constant by construction (pos_now is extrapolated),
so the test is what happens AT each re-anchor (`updated` changes) while the stream keeps playing:
  explained   a tune, a seek, or a non-playing row since the last anchor (pause, buffering)
  drift       the content clock running fast or slow vs the reported speed (the plan's "1% fast"): dE grows with
              the time between anchors; fitted per stick as slope = sum(dE*dt) / sum(dt^2) over unexplained steps
  jump        an unexplained step whose residual |dE - slope*dt| exceeds FLAT_TOL

Lines:
  A4/B1 flat      per stick: unexplained jumps per playing hour <= JUMPS_PER_H (B1); A4 wants none at all
  A4 pause        per commanded pause: E moves by the commanded length (play_t - pause_t) within PAUSE_TOL
  A4 sticks       both sticks on one channel, rows within PAIR_WIN s: median |E1 - E2| <= STICK_AGREE
  A4 landing      Prime deep-link landings per channel: sd of E at landing <= LAND_SD_MAX
  A4 epoch share  per stick and channel: share of playing rows whose position is an epoch (which content gives
                  an epoch and which a 24 h counter); info, no bar
  B2 key moments  clip epoch minus the play's feed wall time over >= MIN_KM seeks: spread (max - min) <= KM_SPREAD
                  keeps Key Moments as a +/- 2 s prior; wider kills it
Stick ids are output as stick1, stick2, ... (syncgrade's first-seen order); never an address.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median, pstdev

from redzone.lab.syncgrade import _stick_names, is_epoch, meter

FLAT_TOL = 0.1       # s, residual at a re-anchor that still counts as flat (A4)
JUMPS_PER_H = 2.0    # unexplained jumps per playing hour allowed before re-anchoring is required (B1)
PAUSE_TOL = 0.1      # s, |dE - pause length| (A4)
STICK_AGREE = 0.3    # s, median |E1 - E2| between the two sticks on one channel (A4)
PAIR_WIN = 1.0       # s, two sticks' rows this close in wall time are compared
LAND_SD_MAX = 0.5    # s, sd of E at Prime landings per channel (A4)
KM_SPREAD = 2.0      # s, Key Moments clip epoch minus feed time, max - min (B2)
MIN_KM = 5


def _wall_of_update(r: dict) -> float:
    """The wall time of this row's media_session anchor, on the stick's own clock."""
    return float(r["t"]) - (float(r["uptime_ms"]) - float(r["ms"]["updated"])) / 1000.0


def steps(rows: list[dict]) -> dict[str, list[dict]]:
    """Per raw stick id: every re-anchor between two playing rows, with dt, dE and whether an event explains it."""
    graded, _ = meter(rows)
    out: dict[str, list[dict]] = defaultdict(list)
    by_stick: dict[str, list[dict]] = defaultdict(list)
    for r in graded:
        by_stick[r["stick"]].append(r)
    for stick, rs in by_stick.items():
        last, gap = None, False
        for r in rs:
            ms = r.get("ms") or {}
            if ms.get("state") != "playing" or r["E"] is None:
                gap = gap or last is not None
                continue
            if last is not None and ms.get("updated") != last["ms"].get("updated"):
                event = gap or r.get("tune_t") != last.get("tune_t") or r.get("seek_t") != last.get("seek_t")
                out[stick].append({"t0": last["t"], "t": r["t"], "dt": _wall_of_update(r) - _wall_of_update(last),
                                   "dE": r["E"] - last["E"], "explained": event, "epoch": is_epoch(ms)})
            last, gap = r, False
    return out


def _playing_hours(rs: list[dict]) -> float:
    ts = [float(r["t"]) for r in rs if (r.get("ms") or {}).get("state") == "playing"]
    return (max(ts) - min(ts)) / 3600.0 if len(ts) > 1 else 0.0


def flat(rows: list[dict]) -> dict:
    """A4/B1: drift slope, unexplained jumps and jumps per playing hour, per stick and app."""
    names = _stick_names(rows)
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["stick"]].append(r)
    out = {}
    for stick, ss in sorted(steps(rows).items(), key=lambda kv: names[kv[0]]):
        quiet = [s for s in ss if not s["explained"]]
        den = sum(s["dt"] ** 2 for s in quiet)
        slope = sum(s["dE"] * s["dt"] for s in quiet) / den if den else 0.0
        jumps = [s for s in quiet if abs(s["dE"] - slope * s["dt"]) > FLAT_TOL]
        hours = _playing_hours(by[stick])
        rate = len(jumps) / hours if hours else None
        app = by[stick][0].get("app")
        out[f"{names[stick]}|{app}"] = {
            "reanchors": len(ss), "unexplained": len(quiet), "drift_s_per_s": round(slope, 5),
            "jumps": len(jumps), "jumps_per_h": None if rate is None else round(rate, 2),
            "max_jump_s": round(max((abs(s["dE"] - slope * s["dt"]) for s in jumps), default=0.0), 2),
            "a4_flat": "PENDING" if not quiet else ("PASS" if not jumps else "FAIL"),
            "b1_flat": "PENDING" if rate is None or not quiet else ("PASS" if rate <= JUMPS_PER_H else "FAIL")}
    return out


def pauses(rows: list[dict], commands: list[dict]) -> dict:
    """A4: each commanded pause ({stick, pause_t, play_t}: walls of the tester's pause and play commands) moves E
    by its commanded length. The length must come from the commands: E's own step across a pause equals wall
    minus content time by construction, so it can't be its own truth."""
    st = steps(rows)
    errs = []
    for c in commands:
        across = [s for s in st.get(c["stick"], []) if s["explained"] and s["t0"] <= c["pause_t"] < s["t"]]
        if across:
            errs.append(abs(across[0]["dE"] - (float(c["play_t"]) - float(c["pause_t"]))))
    return {"n": len(errs), "max_err_s": round(max(errs), 3) if errs else None,
            "verdict": "PENDING" if not errs else ("PASS" if max(errs) <= PAUSE_TOL else "FAIL")}


def sticks_agree(rows: list[dict]) -> dict:
    """A4: two sticks on one channel at the same moment read the same E."""
    graded, _ = meter(rows)
    names = _stick_names(rows)
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in graded:
        if r["E"] is not None and (r.get("ms") or {}).get("state") == "playing":
            by[(r.get("channel"), r["stick"])].append(r)
    out = {}
    for ch in sorted({c for c, _ in by}):
        ids = sorted((s for c, s in by if c == ch), key=lambda s: names[s])
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                diffs = []
                for ra in by[(ch, a)]:
                    near = [rb for rb in by[(ch, b)] if abs(rb["t"] - ra["t"]) <= PAIR_WIN]
                    if near:
                        diffs.append(abs(ra["E"] - min(near, key=lambda rb: abs(rb["t"] - ra["t"]))["E"]))
                if diffs:
                    m = median(diffs)
                    out[f"{ch}|{names[a]}~{names[b]}"] = {"n": len(diffs), "median_abs_s": round(m, 3),
                                                          "verdict": "PASS" if m <= STICK_AGREE else "FAIL"}
    return out


def landings(rows: list[dict]) -> dict:
    """A4: Prime deep-link landings per channel, sd of E at landing."""
    _, lands = meter(rows)
    by: dict[str, list[float]] = defaultdict(list)
    for x in lands:
        if x.get("app") == "prime":
            by[x.get("channel") or "?"].append(x["E_land"])
    return {ch: {"n": len(es), "sd_s": round(pstdev(es), 3),
                 "verdict": "PENDING" if len(es) < 2 else ("PASS" if pstdev(es) <= LAND_SD_MAX else "FAIL")}
            for ch, es in sorted(by.items())}


def epoch_share(rows: list[dict]) -> dict:
    """A4: which stick and channel report an epoch position and which a counter (info, no bar)."""
    names = _stick_names(rows)
    n: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        ms = r.get("ms") or {}
        if ms.get("state") == "playing" and ms.get("position") is not None:
            n[f'{names[r["stick"]]}|{r.get("app")}|{r.get("channel")}'].append(is_epoch(ms))
    return {k: {"n": len(v), "epoch_pct": round(100.0 * sum(v) / len(v), 1)} for k, v in sorted(n.items())}


def key_moments(seeks: list[dict]) -> dict:
    """B2: [{clip_epoch_s, feed_wall_s}] -> spread of clip epoch minus the play's feed wall time."""
    d = [float(s["clip_epoch_s"]) - float(s["feed_wall_s"]) for s in seeks
         if s.get("clip_epoch_s") is not None and s.get("feed_wall_s") is not None]
    spread = max(d) - min(d) if d else None
    return {"n": len(d), "median_s": round(median(d), 2) if d else None,
            "spread_s": None if spread is None else round(spread, 2),
            "verdict": "PENDING" if len(d) < MIN_KM else ("PASS" if spread <= KM_SPREAD else "FAIL")}


def grade(rows: list[dict], seeks: list[dict] | None = None, pause_cmds: list[dict] | None = None) -> dict:
    return {"flat": flat(rows), "pauses": pauses(rows, pause_cmds or []), "sticks": sticks_agree(rows), "landings": landings(rows),
            "epoch_share": epoch_share(rows), "key_moments": key_moments(seeks or [])}

"""[EXP-800 / EXP-801] Grade the sync program on the sticks' own behind-live meter (overseer tests, Nov 5 2:30 AM).

The truth (docs/sync/2026-11-05-yttv-live-edge-tests.md on codex/resume-redzone 503e837):
  pos_now = position + (uptime_ms_now - updated) * speed     media_session refreshes only on play/pause/seek/tune,
                                                              so the position is EXTRAPOLATED on the stick's clock
  E       = wall - pos_now / 1000                              constant while the stream plays in real time
  behind  = E - E_land                                         E_land = E at the landing of the stick's last
                                                              fresh tune (a deep link lands at the live edge
                                                              +/- 0.5 s); a pause or stall adds to it exactly

Prime Video (Nick, Nov 5 3:15 AM, same doc): its media_session position can be an EPOCH in ms, the wall-clock
time stamped on the frame on screen (position >= EPOCH_FLOOR). Then E itself IS the absolute delay behind the
real world, measured per stick: 6.3-7.0 s, median 6.4 s, 19 readings over 3 h on one stick. Another stick's Prime
position was a 24 h counter (86,368 s rising), which is a plain stream position and gives no absolute. Only epoch
rows feed `absolute_by_stick`. YTTV's ~20-30 s (ad bursts vs plays.jsonl wall) is a first ESTIMATE, not an
anchor: a channel's absolute stays PENDING until a measured anchor is passed in.

Nick's rule: no number is truth until measured. This grades what the meter measures (distance behind the live
edge, per stick) and leaves absolute latency (live edge vs the real world) PENDING until a channel has an
on-screen anchor (`anchors`), never filled in from an assumed lag such as ESPN's ~44 s.

Rows (one per stick per probe tick, lane 2's sync_probe after the uptime fix):
  {t: wall (mid of the adb call), stick, app: "youtubetv"|"prime", channel, uptime_ms,
   ms: {state, position, updated, speed}, tune_t: wall of the stick's last tune command (None = never),
   pred_behind_s: the controller's own behind-live belief for this stick (None = it had none)}

Lines:
  EXP-800  per stick and app: |pred_behind_s - behind| p95 <= MAX_ERR on >= MIN_TRUTH_ROWS rows; absolute PENDING
  EXP-801  per tune: tune-to-playing seconds, and whether it landed within LIVE_TOL of the channel's live edge
           (its landing vs the median landing of the OTHER tunes of that app on that channel); PASS when
           >= SHARE of >= MIN_TUNES tunes did. The reference is PINNED per app from a measured mechanism, never
           picked from the night's data (REF_BY_APP); both spreads are reported so a wrong pin shows up.

Stick ids (serials, IP:port) never leave this module: output names them stick1, stick2, ... in first-seen order.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median

MAX_ERR = 2.0       # seconds, EXP-800 bar (p95 per stick)
MIN_TRUTH_ROWS = 200      # truth rows per stick before EXP-800 can PASS or FAIL (northstar's sync_row bar)
LIVE_TOL = 3.0      # seconds from the live edge that still counts as landing live (EXP-801)
SHARE = 0.95        # share of tunes that must land live (EXP-801)
MIN_TUNES = 20
# Landing reference per app, frozen. youtubetv: deep links land at a constant stream position per channel
# (overseer, Nov 5 2:30 AM, docs/sync/2026-11-05-yttv-live-edge-tests.md: ESPN 46,803.0 +/- 0.3 s, n=7).
# Any other app (Prime) compares E at landing until it has its own measurement.
REF_BY_APP = {"youtubetv": "pos_s"}
REF_DEFAULT = "E_land"
LAND_WAIT = 25.0
EPOCH_FLOOR = 1e12  # a media_session position at or above this is an epoch in ms (Prime), not a stream offset    # seconds after a tune command within which it must reach PLAYING to count as landed


def pos_now(ms: dict, uptime_ms: float | None) -> float | None:
    """Extrapolated stream position (ms) on the stick's own clock; None if it can't be read."""
    p, u = ms.get("position"), ms.get("updated")
    if p is None or u is None or uptime_ms is None:
        return None
    speed = float(ms.get("speed") or 1.0) if ms.get("state") == "playing" else 0.0
    return float(p) + (float(uptime_ms) - float(u)) * speed


def origin(row: dict) -> float | None:
    """E = wall - pos_now: when this stream's position 0 was, on the wall clock."""
    pn = pos_now(row.get("ms") or {}, row.get("uptime_ms"))
    return None if pn is None else float(row["t"]) - pn / 1000.0


def is_epoch(ms: dict) -> bool:
    """True when the position is a wall-clock epoch (ms) rather than an offset into the stream."""
    p = ms.get("position")
    return p is not None and float(p) >= EPOCH_FLOOR


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def _stick_names(rows: list[dict]) -> dict:
    """Raw stick id -> stick1, stick2, ... in first-seen (wall) order; the raw id may be an address."""
    names: dict = {}
    for r in sorted(rows, key=lambda r: r["t"]):
        names.setdefault(r["stick"], f"stick{len(names) + 1}")
    return names


def _mad(xs: list[float]) -> float:
    m = median(xs)
    return median([abs(x - m) for x in xs])


def meter(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(rows with behind_s added, landings). A landing = the first PLAYING row after a tune command whose
    media_session `updated` changed from before the command, within LAND_WAIT."""
    out, lands = [], []
    by_stick: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["t"]):
        by_stick[r["stick"]].append(r)
    for stick, rs in by_stick.items():
        e_land, last_tune, upd_before, landed = None, None, None, True
        prev = None
        for r in rs:
            tt = r.get("tune_t")
            if tt is not None and tt != last_tune:
                last_tune, landed, e_land = tt, False, None
                upd_before = (prev or {}).get("ms", {}).get("updated") if prev and prev["t"] <= tt else None
            ms = r.get("ms") or {}
            e = origin(r)
            if not landed and last_tune is not None and ms.get("state") == "playing" and e is not None \
                    and ms.get("updated") != upd_before and r["t"] - last_tune <= LAND_WAIT:
                landed, e_land = True, e
                lands.append({"stick": stick, "app": r.get("app"), "channel": r.get("channel"), "t": r["t"],
                              "tune_s": r["t"] - last_tune, "pos_s": pos_now(ms, r.get("uptime_ms")) / 1000.0,
                              "E_land": e})
            behind = (e - e_land) if (e is not None and e_land is not None and ms.get("state") == "playing") else None
            out.append({**r, "E": e, "behind_s": behind})
            prev = r
    return out, lands


def exp800(rows: list[dict], anchors: dict | None = None) -> dict:
    """Per (stick, app): the controller's behind-live belief vs the meter. Absolute latency per channel is
    PENDING unless `anchors` gives a measured on-screen anchor for it."""
    graded, _ = meter(rows)
    names = _stick_names(rows)
    errs: dict[tuple, list[float]] = defaultdict(list)
    for r in graded:
        if r["behind_s"] is not None and r.get("pred_behind_s") is not None:
            errs[(names[r["stick"]], r.get("app"))].append(abs(float(r["pred_behind_s"]) - r["behind_s"]))
    out = {}
    for (stick, app), xs in sorted(errs.items()):
        p95 = _pct(xs, 0.95)
        verdict = "PENDING" if len(xs) < MIN_TRUTH_ROWS else ("PASS" if p95 <= MAX_ERR else "FAIL")
        out[f"{stick}|{app}"] = {"n": len(xs), "behind_err_p95_s": round(p95, 2), "verdict": verdict}
    chans = sorted({r.get("channel") for r in graded if r.get("channel")})
    out["absolute_latency"] = {c: ((anchors or {}).get(c) or "PENDING: no on-screen anchor yet") for c in chans}
    absolute: dict[str, list[float]] = defaultdict(list)
    for r in graded:
        ms = r.get("ms") or {}
        if r["E"] is not None and ms.get("state") == "playing" and is_epoch(ms):
            absolute[f'{names[r["stick"]]}|{r.get("app")}'].append(r["E"])
    out["absolute_by_stick"] = {k: {"n": len(xs), "median_s": round(median(xs), 2), "p95_s": round(_pct(xs, 0.95), 2),
                                    "source": "media_session epoch position (measured)"}
                                for k, xs in sorted(absolute.items())}
    return out


def exp801(rows: list[dict]) -> dict:
    """Each tune: tune-to-playing time, and landing within LIVE_TOL of the channel's live edge (leave-one-out:
    a tune is never its own reference)."""
    _, lands = meter(rows)
    names = _stick_names(rows)
    by_stick: dict[str, int] = defaultdict(int)
    for x in lands:
        by_stick[names[x["stick"]]] += 1
    by_ch: dict[str, list[dict]] = defaultdict(list)
    for x in lands:
        by_ch[f'{x.get("app") or "?"}|{x.get("channel") or "?"}'].append(x)
    graded = []
    refs = {}
    for ch, xs in sorted(by_ch.items()):
        key = REF_BY_APP.get(xs[0].get("app"), REF_DEFAULT)
        refs[ch] = {"ref": key, "mad_pos_s": round(_mad([x["pos_s"] for x in xs]), 2),
                    "mad_E_s": round(_mad([x["E_land"] for x in xs]), 2)}
        for i, x in enumerate(xs):
            others = [y[key] for j, y in enumerate(xs) if j != i]
            off = x[key] - median(others) if others else None
            graded.append({**x, "edge_off_s": off, "live": None if off is None else abs(off) <= LIVE_TOL})
    known = [g for g in graded if g["live"] is not None]
    share = sum(g["live"] for g in known) / len(known) if known else None
    verdict = "PENDING" if len(known) < MIN_TUNES else ("PASS" if share >= SHARE else "FAIL")
    tune_s = [g["tune_s"] for g in graded]
    per_app = {}
    for app in sorted({g.get("app") for g in graded if g.get("app")}):
        ts = [g["tune_s"] for g in graded if g.get("app") == app]
        per_app[app] = {"n": len(ts), "tune_to_playing_p50_s": round(median(ts), 2), "p95_s": round(_pct(ts, 0.95), 2)}
    return {"tunes": len(graded), "graded": len(known), "ref": refs, "live_share": None if share is None else round(share, 3),
            "tune_to_playing_p95_s": None if not tune_s else round(_pct(tune_s, 0.95), 2), "per_app": per_app,
            "tunes_per_stick": dict(sorted(by_stick.items())), "verdict": verdict}

"""[SYNC program A, EXP-800/801] Where each stick is against its stream's live edge, read every second.

Nick (Nov 5, 2:10 AM): "how do we consistently, on Prime and YouTube TV, join any game and not be behind,
no matter if we jump around, no matter TV latency, no matter Wi-Fi, every time." Step A: know where each
stick is. A stick's offset behind real life = the provider's latency (per app + channel, learned) + how far
the stick plays behind its stream's live edge. This module measures the second part from
`dumpsys media_session` alone. Read-only: one shell read per stick per tick, never a key press.

The meter (overseer's real-stick tests, docs/sync/2026-11-05-yttv-live-edge-tests.md, 2:30 AM):
  * YouTube TV refreshes the session's position only on play / pause / seek / tune, so the position NOW is
    position + speed x (uptime - updated), with uptime from /proc/uptime in the SAME adb call.
  * E = wall - pos_now. While the stream plays in real time E is flat; a 30 s pause raised it by 30.01 s;
    any rise while playing is a stall. So behind_live_s = E - E_land, E_land = E when the last fresh tune
    first played: every deep-link tune lands AT the live edge (repeatable to ~0.5 s).
  * A tune lands at a fixed position per channel (ESPN 46,803.0 s ±0.3, TNT 46,792.7 ±0.7): `land_pos_s`
    (the raw position of the tune's first playing state) is logged, and `landing_off_s` = that channel's
    median landing from its EARLIER tunes (leave-one-out, ≥ 3) minus this one: a tune that didn't land
    on the edge shows up there.
  * The app's belief is the controller's `held[target]`: the time-shift buffer's hold (0 = "a fresh tune
    plays from the live edge"). pred_behind_s - behind_live_s is EXP-800's error.

Prime Video (overseer, 3:15 AM): on some content the position is an EPOCH in ms, the clock time stamped on the
frame on screen (Nov 4 Prime NBA, one stick: wall - position = 6.3-7.0 s, median 6.4 s, 19 reads over 3 h). Then
E = wall - pos_now IS how far the picture runs behind real life: `abs_behind_s`, and `abs_err_s` = the sync
filter's delay belief minus it. The other stick's Prime position was a 24 h counter (a different content type?):
only positions above EPOCH_POSITION count. YouTube TV's absolute latency still needs an on-screen anchor per channel.

K (Nick, 3:30 AM): the Prime score bug's clock / score samples measure the screen's delay behind the ARENA (Nov 4:
15.8-16.2 s, sd 1.5); the epoch gives the screen's delay behind the FRAME CLOCK (6.4 s, sd ~0.1). Their difference
K = arena - frame clock (~9.6 s) is a property of the stream, fused per target by inverse variance (`frame_k`):
20 reads at sd 1.5 -> 0.34 s. Then epoch_delay_s = E + K is the screen's delay every second, not every score.

Rows (lane 3's syncgrade, #279) go to data/sync_truth/edge/<night>.jsonl, a subfolder, so the v10 grader's
data/sync_truth/*.jsonl glob never mixes them with its anchor rows. Sticks are named by HDMI input, never
by address.

Why: first added in b86b5cfc (2026-11-05) as "SYNC A+B: per-stick live-edge meter (shadow on) and re-link to live (flags off)"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from redzone.channels import ET

log = logging.getLogger(__name__)

_UPTIME = re.compile(r"^\s*(\d+\.\d+)\s+\d+\.\d+\s*$", re.M)
LANDINGS_KEPT = 20      # per channel, for the leave-one-out landing median
LANDINGS_MIN = 3
K_KEPT = 40             # per target, the last K samples fused
K_GATE = 3.0            # after K_GATE_N samples, one further than this many sds from the fused K is left out
K_GATE_N = 5
EPOCH_POSITION = 1e12   # [Prime] a position above this is the wall clock stamped on the frame (ms since 1970)


def night_of(t: float) -> str:
    """The US/Eastern evening a row belongs to (before 6 AM counts as the night before)."""
    d = datetime.fromtimestamp(t, ET)
    if d.hour < 6:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def parse_uptime(text: str) -> float | None:
    """Seconds since boot from /proc/uptime (the clock MediaSession's `updated` counts in, ms)."""
    m = _UPTIME.search(text or "")
    return float(m.group(1)) if m else None


def position_now(ms: dict, uptime_s: float | None) -> float | None:
    """Where the stream is now (s), from a parse_playback report read together with /proc/uptime."""
    pos, upd = ms.get("position"), ms.get("updated")
    if pos is None or pos < 0:
        return None
    p = pos / 1000.0
    if ms.get("state") == "playing" and upd is not None and uptime_s is not None:
        p += float(ms.get("speed") or 1.0) * max(0.0, uptime_s - upd / 1000.0)
    return p


@dataclass
class _Tune:
    target: str
    tune_t: float | None
    e_land: float | None = None      # E = wall - pos_now when it first played
    land_pos: float | None = None    # the raw position of its first playing state
    first_play_t: float | None = None


@dataclass
class EdgeTracker:
    """Per stick: the current tune and every read against it. Pure: no I/O, no clocks of its own."""
    tunes: dict[str, _Tune] = field(default_factory=dict)            # stick -> current tune
    landings: dict[str, list[float]] = field(default_factory=dict)   # target -> landing positions
    epoch_at: dict[str, tuple[float, float]] = field(default_factory=dict)   # target -> (wall, E) last epoch read
    k: dict[str, list[tuple[float, float]]] = field(default_factory=dict)   # target -> K samples (value, sd)
    k_out: dict[str, int] = field(default_factory=dict)                     # target -> samples gated out

    def k_of(self, target: str) -> tuple[float, float, int] | None:
        """K = arena - frame clock for this target: (inverse-variance mean, its sd, samples), None before any."""
        ks = self.k.get(target)
        if not ks:
            return None
        w = [1.0 / (sd * sd) for _, sd in ks]
        return sum(wi * z for wi, (z, _) in zip(w, ks)) / sum(w), sum(w) ** -0.5, len(ks)

    def frame_k(self, target: str, delay_s: float, t: float, sd: float, pair_s: float) -> float | None:
        """A score-bug delay sample (screen behind the arena at t) -> one K sample, paired with this target's
        latest epoch read (screen behind the frame clock) when that is at most pair_s old. Returns the sample."""
        last = self.epoch_at.get(target)
        if last is None or abs(t - last[0]) > pair_s or sd <= 0:
            return None
        z = delay_s - last[1]
        cur = self.k_of(target)
        if cur is not None and cur[2] >= K_GATE_N and abs(z - cur[0]) > K_GATE * (sd * sd + cur[1] ** 2) ** 0.5:
            self.k_out[target] = self.k_out.get(target, 0) + 1
            return None
        ks = self.k.setdefault(target, [])
        ks.append((z, sd))
        del ks[:-K_KEPT]
        return z

    def read(self, stick: str, role: str, target: str | None, ms: dict, uptime_s: float | None, wall: float,
             pred_behind_s: float = 0.0, tune_t: float | None = None,
             delay: tuple[float, float] | None = None) -> dict | None:
        """One read -> one row (None with no target or no clock to time it by)."""
        if not target or uptime_s is None:
            return None
        tu = self.tunes.get(stick)
        if tu is None or tu.target != target or tu.tune_t != tune_t:
            tu = self.tunes[stick] = _Tune(target, tune_t)          # a new tune: no origin until it plays
        p = position_now(ms, uptime_s)
        playing = ms.get("state") == "playing"
        row = {"t": round(wall, 3), "night": night_of(wall), "stick": stick, "role": role,
               "app": target.split(":", 1)[0], "channel": target.split(":", 1)[-1], "target": target,
               "uptime_ms": int(round(uptime_s * 1000)),
               "ms": {k: ms.get(k) for k in ("state", "position", "updated", "speed", "pkg") if ms.get(k) is not None},
               "tune_t": None if tune_t is None else round(tune_t, 3),
               "pred_behind_s": round(float(pred_behind_s), 2)}
        if p is None:
            return row
        e = wall - p
        row["pos_now_s"], row["E_s"] = round(p, 3), round(e, 3)
        if (ms.get("position") or 0) > EPOCH_POSITION:
            row["epoch"], row["abs_behind_s"] = True, round(e, 3)
            self.epoch_at[target] = (wall, e)
            k = self.k_of(target)
            if k is not None:
                row.update(k_s=round(k[0], 2), k_sd_s=round(k[1], 2), k_n=k[2], epoch_delay_s=round(e + k[0], 2))
            if delay is not None:
                row["abs_err_s"] = round(delay[0] - e, 2)
        if tu.e_land is None and playing:
            tu.e_land, tu.first_play_t = e, wall
            tu.land_pos = (ms.get("position") or 0) / 1000.0
            prev = self.landings.setdefault(target, [])
            if len(prev) >= LANDINGS_MIN:
                row["landing_off_s"] = round(statistics.median(prev) - tu.land_pos, 2)
            prev.append(tu.land_pos)
            del prev[:-LANDINGS_KEPT]
            if tune_t is not None:
                row["tune_to_playing_s"] = round(wall - tune_t, 2)
        if tu.e_land is None:
            return row      # loading: not playing yet since the tune
        behind = e - tu.e_land
        row.update(land_pos_s=round(tu.land_pos, 3), behind_live_s=round(behind, 2),
                   err_s=round(float(pred_behind_s) - behind, 2), played_s=round(wall - tu.first_play_t, 1))
        if delay is not None:
            row["delay_mu_s"], row["delay_sd_s"] = round(delay[0], 2), round(delay[1], 2)
        return row


def write_rows(rows: list[dict], root: Path) -> None:
    """Append to <root>/sync_truth/edge/<night>.jsonl (root = the app's data folder)."""
    if not rows:
        return
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["night"], []).append(r)
    d = root / "sync_truth" / "edge"
    try:
        d.mkdir(parents=True, exist_ok=True)
        for night, rs in by.items():
            with open(d / f"{night}.jsonl", "a") as f:
                for r in rs:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
    except OSError:
        log.exception("could not write the live-edge log")
